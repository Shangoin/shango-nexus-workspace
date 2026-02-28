"""
nexus/events/bus.py
Supabase Realtime event bus.
All pods publish events; the bus routes them to:
  → evolution cycle (when threshold hit)
  → MCTS planner
  → cross-pod signal propagation
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Event schema ──────────────────────────────────────────────────────────────

class NexusEvent:
    __slots__ = ("pod", "event_type", "payload", "timestamp")

    def __init__(self, pod: str, event_type: str, payload: dict):
        self.pod = pod
        self.event_type = event_type
        self.payload = payload
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "pod": self.pod,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ── In-process pub/sub (supplements Supabase realtime) ───────────────────────

_subscribers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event_type: str, handler: Callable) -> Callable:
    """Subscribe to a nexus event type (or '*' for all). Returns the handler as a token."""
    _subscribers[event_type].append(handler)
    return handler


def unsubscribe(handler: Callable) -> None:
    """Unsubscribe a previously registered handler from all event types."""
    for handlers in _subscribers.values():
        try:
            handlers.remove(handler)
        except ValueError:
            pass


async def publish(event: NexusEvent, supabase_client=None) -> None:
    """Publish event to in-process bus and Supabase nexus_events table."""
    # In-process routing
    for handler in _subscribers.get(event.event_type, []):
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as exc:
            logger.warning("[bus] handler fail type=%s: %s", event.event_type, exc)

    for handler in _subscribers.get("*", []):
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as exc:
            logger.warning("[bus] wildcard handler fail: %s", exc)

    # Persist to Supabase
    if supabase_client:
        try:
            await asyncio.to_thread(
                lambda: supabase_client.table("nexus_events").insert(event.to_dict()).execute()
            )
        except Exception as exc:
            logger.warning("[bus] supabase publish fail: %s", exc)


# ── Evolution trigger wiring ──────────────────────────────────────────────────

def wire_evolution_triggers(supabase_client=None):
    """
    Register a wildcard subscriber that increments pod event counters
    and triggers genetic cycles when thresholds are crossed.
    """
    from core.evolution import increment_event, genetic_cycle

    async def _on_any_event(event: NexusEvent):
        if increment_event(event.pod):
            logger.info("[bus] evolution threshold hit pod=%s", event.pod)
            asyncio.create_task(genetic_cycle(event.pod, supabase_client))

    subscribe("*", _on_any_event)
    logger.info("[bus] evolution triggers wired")


# ── Cross-pod signal propagation ──────────────────────────────────────────────

_CROSS_POD_MAP: dict[str, list[str]] = {
    # Aurora sales pain signals → Syntropy + Janus
    "aurora.booking_failed": ["syntropy.generate_resource", "janus.analyze_objection"],
    # Janus trading signals → Aurora (premium upsell hint)
    "janus.regime_change": ["aurora.trigger_upsell"],
    # Dan IT swarm issues → Sentinel Prime (doc intel)
    "dan.incident_detected": ["sentinel_prime.analyze_incident"],
    # Syntropy quiz results → Ralph (PRD updates)
    "syntropy.quiz_completed": ["ralph.update_prd"],
}


async def propagate_cross_pod(event: NexusEvent, supabase_client=None) -> None:
    """Route event to downstream pods per cross-pod signal map."""
    targets = _CROSS_POD_MAP.get(f"{event.pod}.{event.event_type}", [])
    for target in targets:
        target_pod, target_action = target.rsplit(".", 1)
        cross_event = NexusEvent(
            pod=target_pod,
            event_type=target_action,
            payload={"source_pod": event.pod, "original": event.payload},
        )
        await publish(cross_event, supabase_client)
        logger.info("[bus] cross-pod %s.%s → %s", event.pod, event.event_type, target)
