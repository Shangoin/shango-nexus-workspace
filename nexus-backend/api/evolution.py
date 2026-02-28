"""nexus/api/evolution.py — Evolution management endpoints."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends, Request
from dependencies import verify_admin, get_supabase
from core.evolution import genetic_cycle, run_all_pod_cycles, POD_FITNESS_FNS

router = APIRouter(tags=["evolution"])


@router.post("/trigger/{pod_name}")
async def trigger_evolution(pod_name: str, request: Request, _=Depends(verify_admin)):
    supabase = get_supabase(request)
    result = await genetic_cycle(pod_name, supabase)
    return result


@router.post("/trigger-all")
async def trigger_all_evolution(request: Request, _=Depends(verify_admin)):
    supabase = get_supabase(request)
    results = await run_all_pod_cycles(supabase)
    return {"results": results}


@router.get("/history")
async def evolution_history(request: Request, limit: int = 100):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(
            lambda: supabase.table("nexus_evolutions")
            .select("*")
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return {"evolutions": res.data or []}
    except Exception as exc:
        return {"evolutions": [], "error": str(exc)}


@router.get("/registered-pods")
async def registered_pods():
    return {"pods": list(POD_FITNESS_FNS.keys())}
