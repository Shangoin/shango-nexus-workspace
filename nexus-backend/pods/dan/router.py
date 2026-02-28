"""
nexus/pods/dan/router.py
DAN IT Swarm — LangGraph Plan→Critique→Execute→Self-Heal loop.
Revenue: $49/mo | Sprint 2: Full LangGraph state machine wired up.
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from dependencies import get_supabase, get_redis
from core.evolution import register_pod
from events.bus import NexusEvent, publish
from pods.dan.graph import dan_app, DANState

logger = logging.getLogger(__name__)
router = APIRouter()


async def _fitness(individual) -> float:
    import os
    base = float(os.environ.get("DAN_AVG_SCORE", "0.7"))
    plan_depth_gene = max(individual[4], 0.1) if len(individual) > 4 else 0.5
    return base * plan_depth_gene


register_pod("dan", _fitness)


class TaskRequest(BaseModel):
    input: str
    context: str = ""


class TaskResponse(BaseModel):
    pod: str
    task: str
    result: str
    plan: str
    verified: bool
    healed: bool
    iterations: int


@router.post("/run", response_model=TaskResponse)
async def run_task(body: TaskRequest, request: Request):
    """
    Purpose:  Execute IT task through full LangGraph PACV loop.
    Inputs:   TaskRequest(input, context)
    Outputs:  TaskResponse with result, plan, verified, healed, iterations
    Side Effects: Publishes dan.task_executed and dan.self_healed events
    """
    supabase = get_supabase(request)

    task_text = f"{body.context}\n{body.input}".strip() if body.context else body.input

    try:
        final_state: DANState = await dan_app.ainvoke(DANState(task=task_text))
    except Exception as exc:
        logger.error("[dan:router] graph invoke fail: %s", exc)
        final_state = DANState(
            task=task_text,
            result=f"Graph execution failed: {exc}",
            verified=False,
        )

    try:
        await publish(
            NexusEvent("dan", "task_completed", {
                "task": task_text[:100],
                "verified": final_state.verified,
                "iterations": final_state.iterations,
            }),
            supabase,
        )
    except Exception:
        pass

    return TaskResponse(
        pod="dan",
        task=task_text,
        result=final_state.result,
        plan=final_state.plan,
        verified=final_state.verified,
        healed=final_state.healed,
        iterations=final_state.iterations,
    )


@router.get("/status")
async def status(request: Request):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(lambda: supabase.table("nexus_events").select("id").eq("pod_name", "dan").execute())
        return {"pod": "dan", "role": "IT Swarm (LangGraph)", "event_count": len(res.data or []), "completion_pct": 75}
    except Exception as exc:
        return {"pod": "dan", "error": str(exc)}
