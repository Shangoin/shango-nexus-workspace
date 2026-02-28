"""
nexus/pods/syntropy_lite/router.py
Syntropy Lite — KG Brain
Revenue: $0/mo | Upgrades: Mem0 tiered
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
    base = float(os.environ.get("_FITNESS", "0.7"))
    return base * max(individual[0], 0.1)


register_pod("syntropy_lite", _fitness)


class TaskRequest(BaseModel):
    input: str
    context: str = ""


@router.post("/run")
async def run_task(body: TaskRequest, request: Request):
    supabase = get_supabase(request)
    redis = get_redis(request)
    prompt = f"KG Brain task. Context: {body.context}\nInput: {body.input}\nProduce a detailed, actionable response."
    result = await cascade_call(prompt, task_type="kg_query", redis_client=redis, pod_name="syntropy_lite")
    await publish(NexusEvent("syntropy_lite", "task_completed", {"input": body.input[:100], "result_len": len(result)}), supabase)
    return {"pod": "syntropy_lite", "result": result}


@router.get("/status")
async def status(request: Request):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(lambda: supabase.table("nexus_events").select("id").eq("pod", "syntropy_lite").execute())
        return {"pod": "syntropy_lite", "role": "KG Brain", "event_count": len(res.data or []), "completion_pct": }
    except Exception as exc:
        return {"pod": "syntropy_lite", "error": str(exc)}
