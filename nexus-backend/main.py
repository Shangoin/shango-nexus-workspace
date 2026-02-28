"""
nexus/main.py
Shango Nexus — FastAPI monolith entrypoint.
Lifespan: initializes Supabase, Redis, scheduler, event bus, and all pod routers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

from config import get_settings
from core.constitution import get_constitution
from events.bus import wire_evolution_triggers

# Pod routers
from pods.aurora.router import router as aurora_router
from pods.janus.router import router as janus_router
from pods.dan.router import router as dan_router
from pods.syntropy.router import router as syntropy_router
from pods.ralph.router import router as ralph_router
from pods.sentinel_prime.router import router as sentinel_prime_router
from pods.sentinel_researcher.router import router as sentinel_researcher_router
from pods.shango_automation.router import router as shango_automation_router
from pods.syntropy_lite.router import router as syntropy_lite_router
from pods.syntropy_war_room.router import router as syntropy_war_room_router
from pods.syntropy_scaffold.router import router as syntropy_scaffold_router
from pods.viral_music.router import router as viral_music_router
from pods.syntropy_launch.router import router as syntropy_launch_router

# Nexus API routes
from api.nexus import router as nexus_router
from api.evolution import router as evolution_router
from api.payments import router as payments_router
from api.health import router as health_router
from api.razorpay_webhook import router as razorpay_webhook_router
from api.realtime import router as realtime_router, realtime_manager  # Sprint 6/7 — SSE push + WS manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Supabase
    app.state.supabase = create_client(settings.supabase_url, settings.supabase_key)
    logger.info("[nexus] Supabase client ready")

    # Redis
    try:
        app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        await app.state.redis.ping()
        logger.info("[nexus] Redis connected %s", settings.redis_url)
    except Exception as exc:
        app.state.redis = None
        logger.warning("[nexus] Redis unavailable, using in-memory cache: %s", exc)

    # Constitution
    app.state.constitution = get_constitution()
    logger.info("[nexus] Constitution loaded")

    # Event Bus
    wire_evolution_triggers(app.state.supabase)
    logger.info("[nexus] Event bus wired")

    # Scheduler — periodic evolution sweep every hour
    scheduler = AsyncIOScheduler()

    async def _hourly_evolution():
        from core.evolution import run_all_pod_cycles
        results = await run_all_pod_cycles(app.state.supabase)
        logger.info("[scheduler] hourly evolution done pods=%d", len(results))

    async def _6h_prospect_scout():
        from pods.aurora.proactive_scout import scout_prospects
        prospects = await scout_prospects()
        logger.info("[scheduler] proactive scout complete found=%d", len(prospects))

    async def _daily_variant_retirement():
        from pods.aurora.rl_variants import retire_losing_variants
        results = await asyncio.gather(*[
            retire_losing_variants(el)
            for el in ["opener", "objection_reframe", "closing_ask", "follow_up_subject"]
        ])
        total_retired = sum(len(r) for r in results)
        logger.info("[scheduler] variant retirement complete retired=%d", total_retired)

    scheduler.add_job(_hourly_evolution, "interval", hours=1, id="hourly_evolution")
    scheduler.add_job(_6h_prospect_scout, "interval", hours=6, id="aurora_proactive_scout")
    scheduler.add_job(_daily_variant_retirement, "cron", hour=2, id="variant_retirement_daily")

    # Sprint 6: Razorpay retry queue worker (every 5 minutes)
    from api.razorpay_webhook import process_retry_queue
    scheduler.add_job(process_retry_queue, "interval", minutes=5, id="razorpay_retry_worker")

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("[nexus] Scheduler started")

    # Sprint 7: Supabase Realtime WS manager (background task)
    asyncio.create_task(realtime_manager.start())
    logger.info("[nexus] Supabase Realtime manager started")

    logger.info("[nexus] 🚀 Shango Nexus online — all systems go")
    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    if app.state.redis:
        await app.state.redis.close()
    logger.info("[nexus] graceful shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Shango Nexus",
        description="Unified AI pod orchestration — shango.in",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Core routes ──────────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(nexus_router, prefix="/api/nexus")
    app.include_router(evolution_router, prefix="/api/evolution")
    app.include_router(payments_router, prefix="/api/payments")
    app.include_router(razorpay_webhook_router)
    app.include_router(realtime_router)  # Sprint 6 — SSE /api/realtime/events

    # ── Pod routes ───────────────────────────────────────────────────────────
    app.include_router(aurora_router, prefix="/api/aurora", tags=["aurora"])
    app.include_router(janus_router, prefix="/api/janus", tags=["janus"])
    app.include_router(dan_router, prefix="/api/dan", tags=["dan"])
    app.include_router(syntropy_router, prefix="/api/syntropy", tags=["syntropy"])
    app.include_router(ralph_router, prefix="/api/ralph", tags=["ralph"])
    app.include_router(sentinel_prime_router, prefix="/api/sentinel-prime", tags=["sentinel-prime"])
    app.include_router(sentinel_researcher_router, prefix="/api/sentinel-researcher", tags=["sentinel-researcher"])
    app.include_router(shango_automation_router, prefix="/api/automation", tags=["automation"])
    app.include_router(syntropy_lite_router, prefix="/api/syntropy-lite", tags=["syntropy-lite"])
    app.include_router(syntropy_war_room_router, prefix="/api/war-room", tags=["war-room"])
    app.include_router(syntropy_scaffold_router, prefix="/api/scaffold", tags=["scaffold"])
    app.include_router(viral_music_router, prefix="/api/viral-music", tags=["viral-music"])
    app.include_router(syntropy_launch_router, prefix="/api/launch", tags=["launch"])

    return app


app = create_app()
