"""
Sprint 5 smoke tests
Run with: pytest tests/test_sprint5.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# S5-01: Razorpay webhook
# ─────────────────────────────────────────────────────────────────────────────

def test_product_map_completeness():
    """All six products must be in PRODUCT_MAP."""
    from api.razorpay_webhook import PRODUCT_MAP

    expected = {"aurora_pro", "dan_pro", "sentinel_prime",
                "shango_automation", "syntropy_pack", "nexus_pro"}
    assert set(PRODUCT_MAP.keys()) == expected


def test_verify_razorpay_signature_valid():
    """Valid HMAC-SHA256 signature passes."""
    import hashlib, hmac
    from api.razorpay_webhook import verify_razorpay_signature

    secret = "test_secret"
    body = b'{"event": "payment.captured"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_razorpay_signature(body, sig, secret) is True


def test_verify_razorpay_signature_invalid():
    """Tampered signature is rejected."""
    from api.razorpay_webhook import verify_razorpay_signature

    assert verify_razorpay_signature(b"body", "badhash", "secret") is False


@pytest.mark.asyncio
async def test_razorpay_webhook_ignores_non_payment_events():
    """Non payment.captured events return ignored status."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from api.razorpay_webhook import router

    app = FastAPI()
    app.include_router(router)

    payload = json.dumps({"event": "order.paid", "payload": {}}).encode()
    with patch.dict("os.environ", {"RAZORPAY_WEBHOOK_SECRET": ""}):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/razorpay/webhook",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_razorpay_webhook_valid_payment_captured():
    """Valid payment.captured event returns ok and calls upsert."""
    import os

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test123",
                    "amount": 850000,
                    "notes": {"product": "aurora_pro", "email": "test@shango.in"},
                }
            }
        },
    }
    body = json.dumps(payload).encode()

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from api.razorpay_webhook import router

    app = FastAPI()
    app.include_router(router)

    mock_sb = MagicMock()
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": ""}), \
         patch("api.razorpay_webhook.publish", new_callable=AsyncMock), \
         patch("api.razorpay_webhook.send_welcome_email", new_callable=AsyncMock), \
         patch("api.razorpay_webhook.check_breaker", return_value=True):
        with patch("api.razorpay_webhook.get_settings") as mock_settings, \
             patch("api.razorpay_webhook.create_client", return_value=mock_sb):
            mock_settings.return_value.supabase_url = "https://test.supabase.co"
            mock_settings.return_value.supabase_key = "test_key"
            mock_settings.return_value.supabase_service_key = ""
            with TestClient(app) as client:
                resp = client.post(
                    "/webhooks/razorpay/webhook",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["product"] == "aurora_pro"


# ─────────────────────────────────────────────────────────────────────────────
# S5-02: RL variant retirement
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_variant_retirement_below_threshold():
    """Variants with <10% win rate after 20+ calls are retired."""
    from pods.aurora.rl_variants import retire_losing_variants

    mock_stats = [
        {"calls": 25, "wins": 1, "variant_hash": "abc123"},   # 4% → retire
        {"calls": 25, "wins": 5, "variant_hash": "def456"},   # 20% → keep
        {"calls": 10, "wins": 0, "variant_hash": "ghi789"},   # <20 calls → skip
    ]
    from types import SimpleNamespace as _SN
    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=mock_stats), \
         patch("pods.aurora.rl_variants.remember", new_callable=AsyncMock), \
         patch("pods.aurora.rl_variants.publish", new_callable=AsyncMock), \
         patch("pods.aurora.rl_variants.NexusEvent", side_effect=lambda **kw: _SN(**kw)):
        retired = await retire_losing_variants("opener")

    assert "abc123" in retired
    assert "def456" not in retired
    assert "ghi789" not in retired


@pytest.mark.asyncio
async def test_variant_retirement_publishes_event():
    """Event is published when at least one variant is retired."""
    from pods.aurora.rl_variants import retire_losing_variants

    mock_stats = [{"calls": 30, "wins": 0, "variant_hash": "xyz999"}]
    from types import SimpleNamespace as _SN
    mock_publish = AsyncMock()
    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=mock_stats), \
         patch("pods.aurora.rl_variants.remember", new_callable=AsyncMock), \
         patch("pods.aurora.rl_variants.publish", mock_publish), \
         patch("pods.aurora.rl_variants.NexusEvent", side_effect=lambda **kw: _SN(**kw)):
        await retire_losing_variants("opener")

    mock_publish.assert_called_once()
    call_args = mock_publish.call_args[0][0]
    assert call_args.event_type == "aurora.variants_retired"


@pytest.mark.asyncio
async def test_variant_retirement_no_retire_when_empty():
    """No crash when memory returns empty list."""
    from pods.aurora.rl_variants import retire_losing_variants

    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=[]):
        retired = await retire_losing_variants("closing_ask")

    assert retired == []


@pytest.mark.asyncio
async def test_get_active_variants_filters_retired():
    """get_active_variants excludes retired entries."""
    from pods.aurora.rl_variants import get_active_variants

    mock_data = [
        {"variant_text": "Active variant A", "retired": False},
        {"variant_text": "Retired variant B", "retired": True},
        {"variant_text": "Active variant C"},
    ]
    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=mock_data):
        result = await get_active_variants("opener")

    assert len(result) == 2
    assert all("Retired" not in v for v in result)


# ─────────────────────────────────────────────────────────────────────────────
# S5-03: SEAL frontend endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seal_session_start_returns_question():
    """start_session returns a QuestionResponse with question and difficulty."""
    from pods.syntropy_war_room.router import start_session, StartSessionRequest

    req = StartSessionRequest(student_id="stu_001", topic="JEE Physics", difficulty=0.5)
    mock_inner = {"question": "What is Newton's 2nd Law?", "difficulty": 0.5}

    with patch("pods.syntropy_war_room.router.inner_loop", new_callable=AsyncMock, return_value=mock_inner), \
         patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock):
        result = await start_session(req)

    assert result.question == "What is Newton's 2nd Law?"
    assert result.difficulty == 0.5
    assert result.question_number == 1
    assert 0 <= result.estimated_percentile <= 99


@pytest.mark.asyncio
async def test_seal_answer_correct_increases_difficulty():
    """Correct answer should nudge difficulty upward."""
    from pods.syntropy_war_room.router import submit_answer, AnswerSubmission

    sub = AnswerSubmission(
        student_id="stu_001",
        topic="JEE Physics",
        question="F=ma stands for?",
        student_answer="Force = mass × acceleration",
        correct_answer="Force = mass × acceleration",
        time_taken_seconds=25,
    )

    with patch("pods.syntropy_war_room.router.cascade_call",
               new_callable=AsyncMock, return_value={"score": 95, "correct": True, "feedback": "Perfect"}), \
         patch("pods.syntropy_war_room.router.remember", new_callable=AsyncMock), \
         patch("pods.syntropy_war_room.router.recall",
               new_callable=AsyncMock, return_value=[{"difficulty": 0.5, "correct": True}] * 3), \
         patch("pods.syntropy_war_room.router.inner_loop",
               new_callable=AsyncMock, return_value={"question": "Harder question", "difficulty": 0.55}), \
         patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock):
        result = await submit_answer(sub)

    assert result.difficulty >= 0.5
    assert result.question != ""


@pytest.mark.asyncio
async def test_seal_get_performance_empty():
    """Performance endpoint returns zero counts for unknown student."""
    from pods.syntropy_war_room.router import get_performance

    with patch("pods.syntropy_war_room.router.recall", new_callable=AsyncMock, return_value=[]):
        result = await get_performance("unknown_student")

    assert result["total_questions"] == 0
    assert result["accuracy"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# S5-04: Improvement proofs (existing core module, Sprint 5 schema now persists them)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proof_hash_is_64_chars():
    """Proof hash must be exactly 64 hex chars (SHA-256)."""
    with patch("core.improvement_proofs.remember", new_callable=AsyncMock):
        from core.improvement_proofs import generate_improvement_proof

        proof = await generate_improvement_proof(
            pod_name="aurora",
            cycle_id="s5_test_001",
            avg_score_before=65.0,
            avg_score_after=72.0,
            genome=[0.5] * 8,
            n_calls=25,
        )

    assert len(proof["proof_hash"]) == 64
    assert proof["improved"] is True
    assert proof["delta"] > 0


@pytest.mark.asyncio
async def test_proof_not_improved_when_score_drops():
    """improved=False when score decreases."""
    with patch("core.improvement_proofs.remember", new_callable=AsyncMock):
        from core.improvement_proofs import generate_improvement_proof

        proof = await generate_improvement_proof(
            pod_name="janus",
            cycle_id="s5_test_002",
            avg_score_before=0.80,
            avg_score_after=0.65,
            genome=[0.3] * 8,
            n_calls=25,
        )

    assert proof["improved"] is False
    assert proof["delta"] < 0
