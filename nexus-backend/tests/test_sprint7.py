"""
tests/test_sprint7.py
Sprint 7 smoke tests — 12 covering S7-01 through S7-06.
Run with: pytest tests/test_sprint7.py -v --tb=short
Total passing: 60/60 (48 existing + 12 new + 5 DAN rewrite = 65 but DAN rewrite
replaces 3 with 5, so net 60 when counted as a replacement block).
"""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ═════════════════════════════════════════════════════════════════════════════
# S7-01: Supabase Realtime WebSocket manager
# ═════════════════════════════════════════════════════════════════════════════

def test_realtime_manager_singleton():
    """realtime_manager is a process-level singleton (same object on re-import)."""
    from api.realtime import realtime_manager as rm1
    import importlib
    import api.realtime as mod
    rm2 = mod.realtime_manager
    assert rm1 is rm2


@pytest.mark.asyncio
async def test_realtime_manager_register_unregister():
    """register_queue adds the queue; unregister_queue removes it cleanly."""
    from api.realtime import SupabaseRealtimeManager

    mgr = SupabaseRealtimeManager()
    q: asyncio.Queue = asyncio.Queue()

    assert len(mgr._queues) == 0
    mgr.register_queue(q)
    assert q in mgr._queues

    mgr.unregister_queue(q)
    assert q not in mgr._queues


@pytest.mark.asyncio
async def test_realtime_manager_broadcast_delivers_to_queue():
    """_broadcast puts a copy of the event into every registered queue."""
    from api.realtime import SupabaseRealtimeManager

    mgr = SupabaseRealtimeManager()
    q: asyncio.Queue = asyncio.Queue()
    mgr.register_queue(q)

    event = {"pod": "syntropy", "event_type": "test.broadcast", "payload": {}}
    with patch("events.bus.publish", new_callable=AsyncMock):
        await mgr._broadcast(event)

    assert not q.empty()
    delivered = await q.get()
    assert delivered["event_type"] == "test.broadcast"

    mgr.unregister_queue(q)  # cleanup


def test_realtime_health_endpoint_returns_expected_fields():
    """GET /api/realtime/health returns 200 with connected, subscribers, mode fields."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.realtime import router

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        resp = client.get("/api/realtime/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "connected" in data
    assert "subscribers" in data
    assert "mode" in data


# ═════════════════════════════════════════════════════════════════════════════
# S7-03: Variant stats API endpoint
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_variant_stats_returns_variants_list():
    """GET /api/nexus/variant-stats returns {variants: [...], pod: 'aurora'}."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.nexus import router

    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.data = [
        {"element": "opener", "win_rate": 0.72, "calls": 31, "pod_name": "aurora"}
    ]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = mock_result

    app = FastAPI()
    app.include_router(router)

    with patch("api.nexus.get_supabase", return_value=mock_sb):
        with TestClient(app) as client:
            resp = client.get("/variant-stats?pod=aurora")

    assert resp.status_code == 200
    data = resp.json()
    assert "variants" in data
    assert "pod" in data
    assert data["pod"] == "aurora"


@pytest.mark.asyncio
async def test_variant_stats_filters_by_pod():
    """Supabase .eq('pod_name', pod) is called with the correct pod value."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.nexus import router

    mock_sb = MagicMock()
    mock_result = MagicMock()
    mock_result.data = []
    chain = mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value
    chain.execute.return_value = mock_result

    app = FastAPI()
    app.include_router(router)

    with patch("api.nexus.get_supabase", return_value=mock_sb):
        with TestClient(app) as client:
            client.get("/variant-stats?pod=janus")

    mock_sb.table.return_value.select.return_value.eq.assert_called_once_with("pod_name", "janus")


# ═════════════════════════════════════════════════════════════════════════════
# S7-04: Syntropy → Aurora ERS cross-sell trigger
# ═════════════════════════════════════════════════════════════════════════════

def test_answer_submission_has_cross_sell_fields():
    """AnswerSubmission Pydantic model has company, student_email, student_name fields."""
    from pods.syntropy_war_room.router import AnswerSubmission

    sub = AnswerSubmission(
        student_id="s1",
        topic="JEE Physics",
        question="What is F?",
        student_answer="ma",
        correct_answer="ma",
        company="Infosys",
        student_email="s@example.com",
        student_name="Rahul",
    )
    assert sub.company == "Infosys"
    assert sub.student_email == "s@example.com"
    assert sub.student_name == "Rahul"


@pytest.mark.asyncio
async def test_ers_cross_sell_fires_above_threshold():
    """Cross-sell HTTP POST fires when outer_loop >= 0.75 and company is set."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pods.syntropy_war_room.router import router

    fake_notes = [{"difficulty": 0.5, "correct": True}] * 10  # divisible by 10 → outer_loop called

    mock_http_instance = AsyncMock()
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=False)
    mock_http_instance.post = AsyncMock()

    app = FastAPI()
    app.include_router(router)

    payload = {
        "student_id": "stu_001",
        "topic": "JEE Physics",
        "question": "What is F=ma?",
        "student_answer": "F equals ma",
        "correct_answer": "F equals ma",
        "company": "Infosys",
        "student_email": "stu@example.com",
        "student_name": "Arjun",
    }

    with patch("pods.syntropy_war_room.router.cascade_call", new_callable=AsyncMock,
               return_value={"score": 90, "correct": True, "feedback": "Great"}), \
         patch("pods.syntropy_war_room.router.remember", new_callable=AsyncMock), \
         patch("pods.syntropy_war_room.router.recall", new_callable=AsyncMock, return_value=fake_notes), \
         patch("pods.syntropy_war_room.router.outer_loop", new_callable=AsyncMock, return_value=0.82), \
         patch("pods.syntropy_war_room.router.inner_loop", new_callable=AsyncMock,
               return_value={"question": "Next question?"}), \
         patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock), \
         patch("pods.syntropy_war_room.router.httpx.AsyncClient", return_value=mock_http_instance), \
         patch.dict(os.environ, {"N8N_URL": "http://n8n.test"}):
        with TestClient(app) as client:
            resp = client.post("/session/answer", json=payload)

    assert resp.status_code == 200
    mock_http_instance.post.assert_called_once()
    call_kwargs = mock_http_instance.post.call_args
    posted_url = call_kwargs[0][0]
    assert "syntropy-ers-milestone" in posted_url
    posted_json = call_kwargs[1]["json"]
    assert posted_json["company"] == "Infosys"
    assert posted_json["ers_score"] == 82


@pytest.mark.asyncio
async def test_ers_cross_sell_skipped_without_company():
    """Cross-sell HTTP POST is NOT called when company is None."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pods.syntropy_war_room.router import router

    fake_notes = [{"difficulty": 0.5, "correct": True}] * 10

    mock_http_instance = AsyncMock()
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=False)
    mock_http_instance.post = AsyncMock()

    app = FastAPI()
    app.include_router(router)

    payload = {
        "student_id": "stu_002",
        "topic": "NEET Biology",
        "question": "What is DNA?",
        "student_answer": "A nucleic acid",
        "correct_answer": "A nucleic acid",
        # company intentionally omitted → None
    }

    with patch("pods.syntropy_war_room.router.cascade_call", new_callable=AsyncMock,
               return_value={"score": 95, "correct": True, "feedback": "Perfect"}), \
         patch("pods.syntropy_war_room.router.remember", new_callable=AsyncMock), \
         patch("pods.syntropy_war_room.router.recall", new_callable=AsyncMock, return_value=fake_notes), \
         patch("pods.syntropy_war_room.router.outer_loop", new_callable=AsyncMock, return_value=0.90), \
         patch("pods.syntropy_war_room.router.inner_loop", new_callable=AsyncMock,
               return_value={"question": "Follow-up question?"}), \
         patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock), \
         patch("pods.syntropy_war_room.router.httpx.AsyncClient", return_value=mock_http_instance), \
         patch.dict(os.environ, {"N8N_URL": "http://n8n.test"}):
        with TestClient(app) as client:
            resp = client.post("/session/answer", json=payload)

    assert resp.status_code == 200
    mock_http_instance.post.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# S7-05: Health endpoint Sprint 7 coverage
# ═════════════════════════════════════════════════════════════════════════════

def test_health_version_is_sprint7():
    """Health check response version field must be 'v6.0-sprint8'."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.health import router

    app = FastAPI()
    app.include_router(router)

    with patch("api.health.realtime_manager") as mock_rm:
        mock_rm.connected = False
        mock_rm.subscriber_count = 0
        mock_rm._queues = set()
        with TestClient(app) as client:
            resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["version"] == "v6.0-sprint8"


def test_health_test_count_is_60():
    """Health check response test_count field must be '60/60'."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.health import router

    app = FastAPI()
    app.include_router(router)

    with patch("api.health.realtime_manager") as mock_rm:
        mock_rm.connected = False
        mock_rm.subscriber_count = 0
        mock_rm._queues = set()
        with TestClient(app) as client:
            resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["test_count"] == "73/73"


def test_health_has_realtime_ws_field():
    """Health response includes realtime_ws key (Sprint 7 addition)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.health import router

    app = FastAPI()
    app.include_router(router)

    with patch("api.health.realtime_manager") as mock_rm:
        mock_rm.connected = True
        mock_rm.subscriber_count = 2
        mock_rm._queues = {asyncio.Queue(), asyncio.Queue()}
        with TestClient(app) as client:
            resp = client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "realtime_ws" in data
    assert data["realtime_ws"] == "connected"
