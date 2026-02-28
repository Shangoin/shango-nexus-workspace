"""
nexus/pods/syntropy/router.py
Syntropy Tutor — Tutor Organ
Revenue: $29/mo | Upgrades: pgvector quizzes, CrewAI debates
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from dependencies import get_supabase, get_redis
from core.ai_cascade import cascade_call
from core.evolution import register_pod
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)
router = APIRouter()


async def _fitness(individual) -> float:
    import os
    base = float(os.environ.get("_FITNESS", "0.85"))
    return base * max(individual[0], 0.1)


register_pod("syntropy", _fitness)


class TaskRequest(BaseModel):
    input: str
    context: str = ""


@router.post("/run")
async def run_task(body: TaskRequest, request: Request):
    supabase = get_supabase(request)
    redis = get_redis(request)
    prompt = f"Tutor Organ task. Context: {body.context}\nInput: {body.input}\nProduce a detailed, actionable response."
    result = await cascade_call(prompt, task_type="quiz_generation", redis_client=redis, pod_name="syntropy")
    await publish(NexusEvent("syntropy", "task_completed", {"input": body.input[:100], "result_len": len(result)}), supabase)
    return {"pod": "syntropy", "result": result}


@router.get("/status")
async def status(request: Request):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(lambda: supabase.table("nexus_events").select("id").eq("pod", "syntropy").execute())
        return {"pod": "syntropy", "role": "Tutor Organ", "event_count": len(res.data or []), "completion_pct": }
    except Exception as exc:
        return {"pod": "syntropy", "error": str(exc)}
