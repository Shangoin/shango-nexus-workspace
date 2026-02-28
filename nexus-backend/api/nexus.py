"""
nexus/api/nexus.py
Nexus dashboard API — aggregate KPIs from all pods.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from dependencies import get_supabase, get_redis, get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter()

POD_META = [
    {"id": "aurora",              "label": "Aurora",           "role": "Sales Organ",        "completion": 65,  "mrr": 99},
    {"id": "janus",               "label": "Janus",            "role": "Trading Brain",       "completion": 75,  "mrr": 0},
    {"id": "dan",                 "label": "DAN",              "role": "IT Swarm",            "completion": 60,  "mrr": 49},
    {"id": "syntropy",            "label": "Syntropy",         "role": "Tutor Organ",         "completion": 85,  "mrr": 29},
    {"id": "ralph",               "label": "Ralph",            "role": "PRD Forge",           "completion": 95,  "mrr": 0},
    {"id": "sentinel_prime",      "label": "Sentinel Prime",   "role": "Doc Intel",           "completion": 80,  "mrr": 199},
    {"id": "sentinel_researcher", "label": "Sentinel Research","role": "Research Eye",        "completion": 45,  "mrr": 0},
    {"id": "shango_automation",   "label": "Shango Automation","role": "Webhook Veins",       "completion": 90,  "mrr": 19},
    {"id": "syntropy_lite",       "label": "Syntropy Lite",    "role": "KG Brain",            "completion": 70,  "mrr": 0},
    {"id": "syntropy_war_room",   "label": "War Room",         "role": "Exam Arena",          "completion": 85,  "mrr": 0},
    {"id": "syntropy_scaffold",   "label": "Scaffold",         "role": "Launch Pad",          "completion": 75,  "mrr": 0},
    {"id": "viral_music",         "label": "Viral Music",      "role": "Creative Limb",       "completion": 85,  "mrr": 0},
    {"id": "syntropy_launch",     "label": "Syntropy Launch",  "role": "Deployer",            "completion": 95,  "mrr": 0},
]


@router.get("/pods")
async def list_pods():
    """Static pod catalogue."""
    return {"pods": POD_META}


@router.get("/kpis")
async def nexus_kpis(request: Request):
    """Aggregate live KPIs from all pods via Supabase."""
    supabase = get_supabase(request)

    async def _query(table: str, select: str = "id", filters: dict | None = None):
        try:
            q = supabase.table(table).select(select)
            if filters:
                for k, v in filters.items():
                    q = q.eq(k, v)
            res = await asyncio.to_thread(lambda: q.execute())
            return res.data or []
        except Exception as exc:
            logger.warning("[kpis] query %s fail: %s", table, exc)
            return []

    aurora_calls, evolutions, events = await asyncio.gather(
        _query("aurora_calls", "overall_score"),
        _query("nexus_evolutions", "pod,best_score"),
        _query("nexus_events", "pod,event_type"),
    )

    avg_score = (
        sum(r.get("overall_score", 0) for r in aurora_calls) / len(aurora_calls)
        if aurora_calls else 0
    )

    return {
        "total_pods": len(POD_META),
        "aurora": {
            "total_calls": len(aurora_calls),
            "avg_score": round(avg_score, 1),
        },
        "evolution": {
            "total_cycles": len(evolutions),
            "pods_evolved": len({e.get("pod") for e in evolutions}),
        },
        "events": {
            "total": len(events),
            "pods_active": len({e.get("pod") for e in events}),
        },
        "estimated_mrr_usd": sum(p["mrr"] for p in POD_META),
    }


@router.get("/events")
async def recent_events(request: Request, limit: int = 50):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(
            lambda: supabase.table("nexus_events")
            .select("*")
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return {"events": res.data or []}
    except Exception as exc:
        return {"events": [], "error": str(exc)}


@router.get("/variant-stats")
async def get_variant_stats(pod: str = "aurora", request: Request = None):
    """
    Purpose:     Fetch variant performance stats for Aurora A/B analytics dashboard.
    Inputs:      pod query param (default: "aurora")
    Outputs:     {variants: list[dict], pod: str}
    Side Effects: None (read-only Supabase query)
    """
    supabase = get_supabase(request) if request else None
    if not supabase:
        return {"variants": [], "pod": pod}
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("nexus_variant_stats")
            .select("*")
            .eq("pod_name", pod)
            .order("win_rate", desc=True)
            .execute()
        )
        return {"variants": result.data or [], "pod": pod}
    except Exception as exc:
        logger.warning("[nexus] variant-stats query fail: %s", exc)
        return {"variants": [], "pod": pod, "error": str(exc)}
