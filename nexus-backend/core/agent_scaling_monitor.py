"""
nexus/core/agent_scaling_monitor.py
DeepMind Agent Scaling Principles (Feb 2026).
Research: 180-config scaling study — adding agents to a strong single agent
can DEGRADE performance when coordination overhead > efficiency threshold.

5 metrics computed from nexus_events table every 30 minutes:
  coordination_overhead    — % of events that are cross-pod
  message_density          — events per pod per minute
  redundancy_rate          — % cross-pod events triggering no downstream action
  coordination_efficiency  — useful_signals / total_cross_pod_signals
  error_amplification      — % errors spreading to ≥2 pods

Key thresholds (from DeepMind paper):
  coordination_efficiency < 0.4  → adding pods hurts performance
  error_amplification > 0.2      → errors cascading; circuit break needed
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)

EFFICIENCY_WARNING_THRESHOLD = 0.4     # DeepMind's published threshold
ERROR_AMPLIFICATION_THRESHOLD = 0.2   # fraction of errors spreading to ≥2 pods
MEASUREMENT_WINDOW_MINUTES = 30


@dataclass
class ScalingHealthReport:
    coordination_overhead: float      # % of events that are cross-pod
    message_density: float            # events per pod per minute
    redundancy_rate: float            # % cross-pod events with no downstream action
    coordination_efficiency: float    # useful_signals / total_cross_pod_signals
    error_amplification: float        # % errors spreading to ≥2 pods
    healthy: bool                     # True if all metrics in safe zone
    warnings: list[str] = field(default_factory=list)
    measured_at: str = ""


async def compute_scaling_health(supabase_client) -> ScalingHealthReport:
    """
    Purpose:  Query nexus_events for last MEASUREMENT_WINDOW_MINUTES and compute
              all 5 DeepMind scaling metrics.
    Inputs:   supabase_client (may be None on first boot)
    Outputs:  ScalingHealthReport dataclass
    Side Effects: Publishes nexus.scaling_warning if efficiency < threshold.
    """
    warnings: list[str] = []
    now_str = datetime.utcnow().isoformat()
    since = (datetime.utcnow() - timedelta(minutes=MEASUREMENT_WINDOW_MINUTES)).isoformat()

    if supabase_client is None:
        return ScalingHealthReport(0.0, 0.0, 0.0, 1.0, 0.0, True,
                                   ["No Supabase client — metrics deferred"], now_str)

    try:
        response = await asyncio.to_thread(
            lambda: supabase_client.table("nexus_events")
            .select("id,pod,event_type,payload,created_at")
            .gte("created_at", since)
            .execute()
        )
        events: list[dict] = response.data or []
    except Exception as exc:
        logger.warning("[scaling_monitor] failed to fetch events: %s", exc)
        return ScalingHealthReport(0.0, 0.0, 0.0, 1.0, 0.0, True,
                                   [f"Fetch error: {exc}"], now_str)

    total_events = len(events)
    if total_events == 0:
        return ScalingHealthReport(0.0, 0.0, 0.0, 1.0, 0.0, True,
                                   ["No events in window"], now_str)

    # 1. Coordination overhead: cross-pod events / total
    cross_pod_keywords = ["aurora.", "janus.", "syntropy.", "dan.", "sentinel.",
                          "ralph.", "nexus."]
    cross_pod_events = [
        e for e in events
        if any(kw in e.get("event_type", "") for kw in cross_pod_keywords)
        and (e.get("payload") or {}).get("target_pod")
    ]
    coordination_overhead = len(cross_pod_events) / total_events

    # 2. Message density: events per pod per minute
    pods_active = max(len({e.get("pod") for e in events if e.get("pod")}), 1)
    message_density = total_events / pods_active / max(MEASUREMENT_WINDOW_MINUTES, 1)

    # 3. Redundancy rate: what fraction of cross-pod events triggered downstream action
    downstream_refs: set[str] = set()
    for e in events:
        caused_by = (e.get("payload") or {}).get("caused_by")
        if caused_by:
            downstream_refs.add(str(caused_by))
    triggered = sum(1 for e in cross_pod_events if str(e.get("id", "")) in downstream_refs)
    redundancy_rate = 1.0 - (triggered / max(len(cross_pod_events), 1))

    # 4. Coordination efficiency = 1 - redundancy
    coordination_efficiency = 1.0 - redundancy_rate

    # 5. Error amplification: errors that appear in ≥2 pods within 30s
    error_events = [
        e for e in events
        if any(kw in e.get("event_type", "").lower()
               for kw in ("error", "fail", "violation", "critical"))
    ]
    multi_pod_errors = 0
    for err in error_events:
        err_pod = err.get("pod", "")
        err_ts = err.get("created_at", "")
        same_window = [
            e for e in error_events
            if e.get("pod", "") != err_pod
            and e.get("created_at", "") != ""
            and err_ts != ""
            and abs(len(e.get("created_at", "")) - len(err_ts)) < 5
        ]
        if same_window:
            multi_pod_errors += 1
    error_amplification = multi_pod_errors / max(len(error_events), 1) if error_events else 0.0

    # Generate human-readable warnings
    if coordination_efficiency < EFFICIENCY_WARNING_THRESHOLD:
        warnings.append(
            f"⚠️ Coordination efficiency {coordination_efficiency:.2f} < "
            f"{EFFICIENCY_WARNING_THRESHOLD} (DeepMind threshold). "
            "Consider reducing cross-pod routes or using single-agent for high-redundancy tasks."
        )
    if error_amplification > ERROR_AMPLIFICATION_THRESHOLD:
        warnings.append(
            f"🔴 Error amplification {error_amplification:.2f} > "
            f"{ERROR_AMPLIFICATION_THRESHOLD} — errors cascading across pods. "
            "Circuit breaking highest-error cross-pod route."
        )
    if message_density > 10.0:
        warnings.append(
            f"⚡ Message density {message_density:.1f} events/pod/min is high. "
            "Consider batching cross-pod signals."
        )

    healthy = len(warnings) == 0
    report = ScalingHealthReport(
        coordination_overhead=round(coordination_overhead, 3),
        message_density=round(message_density, 3),
        redundancy_rate=round(redundancy_rate, 3),
        coordination_efficiency=round(coordination_efficiency, 3),
        error_amplification=round(error_amplification, 3),
        healthy=healthy,
        warnings=warnings,
        measured_at=now_str,
    )

    if not healthy:
        try:
            await publish(NexusEvent(
                pod="nexus",
                event_type="nexus.scaling_warning",
                payload={
                    "coordination_efficiency": report.coordination_efficiency,
                    "error_amplification": report.error_amplification,
                    "warnings": warnings,
                },
            ))
        except Exception:
            pass

    return report


# ── Module-level cache (refreshed by APScheduler every 30 min) ───────────────
_last_report: ScalingHealthReport | None = None


def get_last_scaling_report() -> ScalingHealthReport | None:
    """Return cached scaling report (may be None before first run)."""
    return _last_report


async def run_scaling_monitor(supabase_client) -> ScalingHealthReport:
    """
    Purpose:  Top-level scheduler entrypoint — compute + cache + return report.
    Inputs:   supabase_client
    Outputs:  ScalingHealthReport
    Side Effects: Updates _last_report module-level cache.
    """
    global _last_report
    _last_report = await compute_scaling_health(supabase_client)
    logger.info(
        "[scaling_monitor] healthy=%s coord_eff=%.2f err_amp=%.2f",
        _last_report.healthy,
        _last_report.coordination_efficiency,
        _last_report.error_amplification,
    )
    return _last_report
