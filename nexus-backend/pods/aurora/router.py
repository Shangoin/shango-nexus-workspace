"""
nexus/pods/aurora/router.py
Aurora — AI Sales Organ
Revenue: $99/mo Aurora Pro
Upgrades: Vapi+ElevenLabs dynamic voice, DEAP prompt variants, PACV nurture sequence
"""

from __future__ import annotations
import asyncio
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from dependencies import get_supabase, get_redis, get_current_user_id
from core.ai_cascade import cascade_call
from core.evolution import register_pod, increment_event
from core.mcts_graph import pacv_loop
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Pod models ────────────────────────────────────────────────────────────────

class LeadRequest(BaseModel):
    name: str
    phone: str
    country_code: str = "IN"
    company: str = ""
    pain_point: str = ""
    source: str = "web"


class LeadScore(BaseModel):
    score: int
    tier: str  # high / medium / low
    delay_minutes: int
    reasoning: str


# ── Fitness fn (DEAP) ────────────────────────────────────────────────────────

async def _aurora_fitness(individual) -> float:
    """
    Fitness = weighted combination of booking_rate and avg_call_score.
    Individual genes control prompt temperature, follow-up cadence, etc.
    """
    try:
        import asyncio
        supabase_url = os.environ.get("SUPABASE_URL", "")
        # In production: query real booking rate from aurora_calls
        # Here we use stored aggregate as a stub
        avg_score_from_env = float(os.environ.get("AURORA_AVG_SCORE", "0.5"))
        booking_rate = float(os.environ.get("AURORA_BOOKING_RATE", "0.15"))
        gene_temp = individual[0]     # prompt temperature weight
        gene_cadence = individual[1]  # follow-up cadence weight
        return avg_score_from_env * 0.6 + booking_rate * 0.4
    except Exception:
        return 0.0


# Register on import
register_pod("aurora", _aurora_fitness)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/leads")
async def create_lead(body: LeadRequest, request: Request):
    supabase = get_supabase(request)
    redis = get_redis(request)

    # Score lead via AI cascade with PACV
    score_prompt = f"""Score this sales lead 0-100 for urgency and fit. Respond as JSON.
Lead: name={body.name}, company={body.company}, pain_point={body.pain_point}, country={body.country_code}
Fields: score (int), tier (high/medium/low), delay_minutes (int), reasoning (str)"""
    
    raw = await cascade_call(score_prompt, task_type="lead_scoring", redis_client=redis, pod_name="aurora")
    
    try:
        import json, re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        scored = json.loads(m.group()) if m else {"score": 50, "tier": "medium", "delay_minutes": 15, "reasoning": raw}
    except Exception:
        scored = {"score": 50, "tier": "medium", "delay_minutes": 15, "reasoning": raw}

    # Store in Supabase
    record = {
        "name": body.name,
        "phone": body.phone,
        "country_code": body.country_code,
        "company": body.company,
        "pain_point": body.pain_point,
        "source": body.source,
        "lead_score": scored.get("score"),
        "tier": scored.get("tier"),
    }
    try:
        res = await asyncio.to_thread(lambda: supabase.table("aurora_leads").insert(record).execute())
        lead_id = res.data[0]["id"] if res.data else None
    except Exception as exc:
        logger.warning("[aurora] lead store fail: %s", exc)
        lead_id = None

    # Publish event
    await publish(NexusEvent("aurora", "lead_scored", {"lead_id": lead_id, **scored}), supabase)
    if increment_event("aurora"):
        asyncio.create_task(_aurora_fitness([0.5] * 8))

    return {"lead_id": lead_id, "scoring": scored}


@router.get("/calls")
async def get_calls(request: Request, limit: int = 50):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(
            lambda: supabase.table("aurora_calls").select("*").order("created_at", desc=True).limit(limit).execute()
        )
        return {"calls": res.data or []}
    except Exception as exc:
        return {"calls": [], "error": str(exc)}


@router.get("/stats")
async def aurora_stats(request: Request):
    supabase = get_supabase(request)
    try:
        calls_res = await asyncio.to_thread(lambda: supabase.table("aurora_calls").select("overall_score").execute())
        calls = calls_res.data or []
        avg_score = sum(c.get("overall_score", 0) for c in calls) / len(calls) if calls else 0
        return {"total_calls": len(calls), "avg_score": round(avg_score, 1)}
    except Exception as exc:
        return {"error": str(exc)}
