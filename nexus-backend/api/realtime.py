"""
nexus/api/realtime.py
Server-Sent Events (SSE) realtime stream for Shango Nexus dashboard.

Purpose:     Sprint 6: Expose GET /api/realtime/events as SSE push stream.
             Sprint 7: Add SupabaseRealtimeManager — singleton WS connection to
             Supabase Realtime "nexus_events_live" channel. On INSERT → pushes to
             all active asyncio.Queues. Reconnects with exponential backoff.
             GET /api/realtime/health reports connection state + subscriber count.
Inputs:      GET /api/realtime/events?pod=all&limit=100
             GET /api/realtime/health
Outputs:     text/event-stream of JSON event objects + periodic heartbeat;
             or {connected, subscribers, mode} JSON for health
Side Effects: Subscribe/unsubscribe to events/bus; WS connection to Supabase
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Set

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/realtime", tags=["realtime"])


# ── Sprint 7: Supabase Realtime Manager ─────────────────────────────────────


class SupabaseRealtimeManager:
    """
    Purpose:     Singleton Supabase Realtime WS manager.
                 Bridges Supabase INSERT events on nexus_events table to all
                 active SSE client queues. Reconnects with exponential backoff.
    Inputs:      SUPABASE_URL, SUPABASE_SERVICE_KEY env vars
    Outputs:     None (runs as background task, broadcasts to registered queues)
    Side Effects: Maintains live WS connection; publishes realtime.event_pushed
    """

    def __init__(self) -> None:
        self._queues: Set[asyncio.Queue] = set()
        self._connected: bool = False
        self._reconnect_delay: int = 1

    def register_queue(self, q: asyncio.Queue) -> None:
        """Register a client queue to receive broadcast events."""
        self._queues.add(q)

    def unregister_queue(self, q: asyncio.Queue) -> None:
        """Remove a client queue (call on client disconnect)."""
        self._queues.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    @property
    def connected(self) -> bool:
        return self._connected

    async def _broadcast(self, event: dict) -> None:
        """Push event to all registered subscriber queues, dropping slow clients."""
        dead: set = set()
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.add(q)
        self._queues -= dead

    async def start(self) -> None:
        """
        Purpose:     Connect to Supabase Realtime, subscribe to nexus_events table.
        Inputs:      None (reads SUPABASE_URL, SUPABASE_SERVICE_KEY from env)
        Outputs:     None (runs forever, reconnects on failure with backoff)
        Side Effects: Calls _broadcast on every INSERT into nexus_events;
                      publishes realtime.event_pushed to the in-process event bus
        """
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_KEY", "")

        if not url or not key:
            logger.warning("[realtime] SUPABASE_URL/KEY not set — WS mode disabled")
            return

        while True:
            try:
                from supabase import create_client
                client = create_client(url, key)
                channel = client.channel("nexus_events_live")

                def handle_insert(payload: dict) -> None:
                    record = payload.get("new", {})
                    asyncio.create_task(self._broadcast(record))
                    try:
                        from events.bus import NexusEvent, publish
                        asyncio.create_task(
                            publish(
                                NexusEvent(
                                    pod=record.get("pod_name", "nexus"),
                                    event_type="realtime.event_pushed",
                                    payload={
                                        "event_type": record.get("event_type", "unknown"),
                                        "pod": record.get("pod_name", "nexus"),
                                    },
                                )
                            )
                        )
                    except Exception:
                        pass

                channel.on(
                    "postgres_changes",
                    event="INSERT",
                    schema="public",
                    table="nexus_events",
                    callback=handle_insert,
                ).subscribe()

                self._connected = True
                self._reconnect_delay = 1  # Reset backoff on successful connect
                logger.info("[realtime] Supabase Realtime connected → nexus_events")

                # Keep-alive loop — channel runs until client disconnects/errors
                while self._connected:
                    await asyncio.sleep(5)

            except Exception as exc:
                self._connected = False
                logger.warning(
                    "[realtime] Supabase WS disconnected: %s — reconnecting in %ds",
                    exc,
                    self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30)


# Process-level singleton
realtime_manager = SupabaseRealtimeManager()


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/health")
async def realtime_health():
    """
    Purpose:     Report Supabase Realtime WS connection state + active subscribers.
    Inputs:      None
    Outputs:     {connected: bool, subscribers: int, mode: str}
    Side Effects: None
    """
    return {
        "connected": realtime_manager.connected,
        "subscribers": realtime_manager.subscriber_count,
        "mode": "supabase_realtime" if realtime_manager.connected else "sse_fallback",
    }


@router.get("/events")
async def stream_events(pod: str = "all", limit: int = 100):
    """
    Purpose:     SSE stream of live nexus_events.
                 Sprint 7: now backed by SupabaseRealtimeManager when connected;
                 falls back to in-process event bus when WS unavailable.
    Inputs:      pod query param — filter by pod name, or "all";
                 limit — max buffered events (default 100, ignored once streaming)
    Outputs:     text/event-stream — one JSON object per event, heartbeat every 30s
    Side Effects: Registers/unregisters queue with realtime_manager
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # Always register with SupabaseRealtimeManager (used if WS is connected)
    realtime_manager.register_queue(queue)

    # Also subscribe to in-process bus as fallback / for locally published events
    bus_token = None
    try:
        from events.bus import subscribe

        async def handler(event) -> None:
            if hasattr(event, "to_dict"):
                event_dict = event.to_dict()
            elif isinstance(event, dict):
                event_dict = event
            else:
                event_dict = {"type": str(event)}
            pod_name = event_dict.get("pod", "nexus")
            if pod == "all" or pod_name == pod:
                try:
                    queue.put_nowait(event_dict)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event_dict)
                    except Exception:
                        pass

        bus_token = subscribe("*", handler)
    except Exception as exc:
        logger.warning("[realtime] event bus subscribe failed: %s", exc)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            realtime_manager.unregister_queue(queue)
            if bus_token is not None:
                try:
                    from events.bus import unsubscribe
                    unsubscribe(bus_token)
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
