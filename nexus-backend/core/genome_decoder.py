"""
nexus/core/genome_decoder.py
Converts DEAP 8-float genomes into pod-specific prompt mutation parameters.

Purpose: Universal genome → concrete prompt parameter decoder for all pods.
Inputs:  genome (list of 8 floats in [0,1]), pod_name (str)
Outputs: dict of prompt mutation parameters specific to the pod
Side Effects: None (pure decode); apply_genome_to_pod() PATCHes Vapi and writes event
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Universal gene index → meaning ──────────────────────────────────────────
GENE_MAP: dict[int, str] = {
    0: "temperature",           # 0.0=conservative, 1.0=creative
    1: "follow_up_cadence",     # 0.0=aggressive(1day), 1.0=gentle(7days)
    2: "opener_style",          # 0.0=empathy, 0.5=ROI, 1.0=question
    3: "objection_depth",       # 0.0=brief, 1.0=deep
    4: "closing_urgency",       # 0.0=soft, 1.0=hard close
    5: "tone_formality",        # 0.0=casual, 1.0=formal
    6: "content_density",       # 0.0=sparse, 1.0=rich detail
    7: "personalization_level"  # 0.0=generic, 1.0=hyper-personal
}

_OPENER_VARIANTS = ["empathy", "roi", "question"]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def decode_genome(genome: list[float], pod_name: str) -> dict[str, Any]:
    """
    Purpose:  Convert DEAP float genome into pod-specific prompt parameters.
    Inputs:   genome — list of 8 floats clamped to [0,1]; pod_name — str
    Outputs:  dict of concrete prompt mutation parameters
    Side Effects: None
    """
    if len(genome) < 8:
        genome = list(genome) + [0.5] * (8 - len(genome))

    # ── Base decode (universal across all pods) ──────────────────────────────
    params: dict[str, Any] = {
        label: _clamp(genome[idx]) for idx, label in GENE_MAP.items()
    }

    # ── Pod-specific overrides ───────────────────────────────────────────────
    if pod_name == "aurora":
        g = genome
        # Vapi temperature: 0.3–1.0
        params["vapi_temperature"] = round(0.3 + _clamp(g[0]) * 0.7, 2)
        # Opener variant index: 0/1/2
        params["opener_variant"] = _OPENER_VARIANTS[int(_clamp(g[2]) * 2.99)]
        # Follow-up delay: 1–7 days
        params["follow_up_days"] = max(1, int(_clamp(g[1]) * 7))
        # Urgency label
        params["closing_style"] = "hard" if g[4] > 0.6 else ("soft" if g[4] < 0.3 else "medium")

    elif pod_name == "syntropy" or pod_name == "syntropy_war_room":
        g = genome
        params["question_difficulty"] = round(_clamp(g[0]), 2)
        params["explanation_depth"] = round(_clamp(g[4]), 2)
        params["persona"] = "drill_sergeant" if g[5] > 0.5 else "ivy_coach"
        params["hint_frequency"] = round(_clamp(g[3]), 2)

    elif pod_name == "janus":
        g = genome
        # Regime confidence threshold: 0.5–0.9
        params["regime_confidence_threshold"] = round(0.5 + _clamp(g[6]) * 0.4, 2)
        # Position size multiplier: 0.5–1.5
        params["position_size_multiplier"] = round(0.5 + _clamp(g[7]) * 1.0, 2)
        params["risk_tolerance"] = "high" if g[0] > 0.6 else ("low" if g[0] < 0.3 else "medium")

    elif pod_name == "dan":
        g = genome
        params["plan_depth"] = int(_clamp(g[4]) * 5) + 1   # 1–6 steps
        params["critique_strictness"] = round(_clamp(g[6]), 2)
        params["self_heal_retries"] = max(1, int(_clamp(g[3]) * 3))

    elif pod_name == "ralph":
        g = genome
        params["story_complexity"] = round(_clamp(g[6]), 2)
        params["iteration_budget"] = max(1, int(_clamp(g[1]) * 10))

    elif pod_name == "sentinel_prime":
        g = genome
        params["alert_sensitivity"] = round(_clamp(g[0]), 2)
        params["escalation_threshold"] = round(_clamp(g[4]), 2)
        params["report_verbosity"] = "verbose" if g[6] > 0.6 else "concise"

    elif pod_name == "shango_automation":
        g = genome
        params["automation_depth"] = round(_clamp(g[6]), 2)
        params["reply_speed"] = "instant" if g[1] < 0.3 else ("delayed" if g[1] > 0.7 else "normal")

    elif pod_name == "viral_music":
        g = genome
        params["creativity_level"] = round(_clamp(g[0]), 2)
        params["beat_intensity"] = round(_clamp(g[4]), 2)
        params["lyric_style"] = "abstract" if g[2] > 0.6 else ("literal" if g[2] < 0.3 else "metaphor")

    logger.debug("[genome_decoder] pod=%s params=%s", pod_name, list(params.keys()))
    return params


async def apply_genome_to_pod(pod_name: str, params: dict[str, Any]) -> bool:
    """
    Purpose:  Apply decoded genome params to a pod's live prompt/config.
    Inputs:   pod_name — str; params — decoded genome dict from decode_genome()
    Outputs:  True if applied and event published; False if circuit breaker open
    Side Effects: PATCHes Vapi assistant (aurora only); publishes nexus event
    """
    from core.constitution import get_constitution
    from events.bus import NexusEvent, publish
    from core.ai_cascade import cascade_call

    constitution = get_constitution()
    if not constitution.check_breaker("evolution_cycle"):
        logger.warning("[genome_decoder] evolution_cycle breaker OPEN — skip apply pod=%s", pod_name)
        return False

    try:
        if pod_name == "aurora":
            if not constitution.check_breaker("vapi"):
                logger.warning("[genome_decoder] vapi breaker OPEN — skip Vapi PATCH")
            else:
                new_prompt = await cascade_call(
                    f"Generate a complete, deployable Vapi sales-agent system prompt. "
                    f"Parameters:\n- Opener style: {params.get('opener_variant', 'empathy')}\n"
                    f"- Temperature: {params.get('vapi_temperature', 0.7)}\n"
                    f"- Follow-up days: {params.get('follow_up_days', 3)}\n"
                    f"- Closing style: {params.get('closing_style', 'medium')}\n"
                    f"- Formality: {params.get('tone_formality', 0.5)}\n"
                    f"Keep under 800 words. ARIA personality. Shango brand voice.",
                    task_type="self_improvement",
                    pod_name="aurora",
                )
                import httpx
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.put(
                        f"https://api.vapi.ai/assistant/{os.getenv('VAPI_ASSISTANT_ID', '')}",
                        headers={"Authorization": f"Bearer {os.getenv('VAPI_API_KEY', '')}"},
                        json={"model": {"messages": [{"role": "system", "content": new_prompt}]}},
                    )
                    if resp.status_code == 200:
                        constitution.record_success("vapi")
                        logger.info("[genome_decoder] Vapi PATCH success pod=aurora")
                    else:
                        constitution.record_failure("vapi")
                        logger.warning("[genome_decoder] Vapi PATCH fail status=%d", resp.status_code)

        await publish(
            NexusEvent(
                pod_name=pod_name,
                event_type="genome_applied",
                data={"params": {k: str(v) for k, v in params.items()}},
            ),
            supabase_client=None,   # tolerate None — bus persists best-effort
        )
        constitution.record_success("evolution_cycle")
        logger.info("[genome_decoder] genome applied pod=%s", pod_name)
        return True

    except Exception as exc:
        constitution.record_failure("evolution_cycle")
        logger.error("[genome_decoder] apply fail pod=%s err=%s", pod_name, exc)
        return False
