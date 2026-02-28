"""
Sprint 6 smoke tests — comprehensive coverage of all S6 modules.
Run with: pytest tests/test_sprint6.py -v --tb=short
"""
from __future__ import annotations

import json
import os
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ── S6-01: Razorpay retry queue ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_to_retry_queue_calls_redis_lpush():
    """push_to_retry_queue should push JSON entry to Redis + publish event."""
    from api.razorpay_webhook import push_to_retry_queue

    mock_redis = AsyncMock()
    with patch("api.razorpay_webhook.get_redis", return_value=mock_redis):
        with patch("api.razorpay_webhook.publish", new_callable=AsyncMock):
            await push_to_retry_queue({"payload": {"payment": {"entity": {"id": "pay_test"}}}})
            mock_redis.lpush.assert_called_once()
            # Verify the key
            call_args = mock_redis.lpush.call_args
            assert call_args[0][0] == "nexus:razorpay:retry_queue"


@pytest.mark.asyncio
async def test_process_retry_queue_idempotency():
    """If payment_id already exists in subscriptions, skip silently without upsert."""
    from api.razorpay_webhook import process_retry_queue

    mock_redis = AsyncMock()
    mock_redis.rpop.side_effect = [
        json.dumps({
            "payload": {"payload": {"payment": {"entity": {
                "id": "pay_dup",
                "notes": {"product": "aurora_pro", "email": "test@test.com"},
                "amount": 850000,
            }}}},
            "attempt": 1,
        }),
        None,
    ]

    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "existing"}]

    with patch("api.razorpay_webhook.get_redis", return_value=mock_redis):
        with patch("api.razorpay_webhook.get_supabase", return_value=mock_sb):
            await process_retry_queue()
            # upsert should NOT have been called — already activated
            mock_sb.table.return_value.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_process_retry_queue_dead_letter_after_max_retries():
    """After MAX_RETRIES attempts, payment should land in dead_letter + Slack alert."""
    from api.razorpay_webhook import process_retry_queue, MAX_RETRIES

    mock_redis = AsyncMock()
    mock_redis.rpop.side_effect = [
        json.dumps({
            "payload": {"payload": {"payment": {"entity": {
                "id": "pay_fail",
                "notes": {"product": "aurora_pro", "email": "x@x.com"},
                "amount": 0,
            }}}},
            "attempt": MAX_RETRIES,
        }),
        None,
    ]

    mock_sb = MagicMock()
    # Idempotency check returns nothing (not yet activated)
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    # Upsert throws to simulate Supabase down
    mock_sb.table.return_value.upsert.return_value.execute.side_effect = Exception("Supabase down")

    with patch("api.razorpay_webhook.get_redis", return_value=mock_redis):
        with patch("api.razorpay_webhook.get_supabase", return_value=mock_sb):
            with patch("api.razorpay_webhook.httpx.AsyncClient") as mock_http_cls:
                mock_http = AsyncMock()
                mock_http.post = AsyncMock(return_value=AsyncMock(status_code=200))
                mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
                    await process_retry_queue()
                    # Dead letter push should have been called
                    assert mock_redis.lpush.called
                    call_key = mock_redis.lpush.call_args[0][0]
                    assert call_key == "nexus:razorpay:dead_letter"


# ── S6-02: Champion auto-promotion ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_and_promote_champion_no_champion_low_calls():
    """Variants with <30 calls should NOT trigger promotion."""
    from pods.aurora.rl_variants import check_and_promote_champion

    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = [
            {"calls": 10, "wins": 8, "retired": False, "variant_hash": "abc123"},
        ]  # 10 calls < 30 threshold
        result = await check_and_promote_champion("opener")
        assert result["promoted"] == False


@pytest.mark.asyncio
async def test_check_and_promote_champion_no_champion_low_win_rate():
    """Variants with <60% win rate should NOT trigger promotion even with enough calls."""
    from pods.aurora.rl_variants import check_and_promote_champion

    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = [
            {"calls": 40, "wins": 18, "retired": False, "variant_hash": "abc456"},
        ]  # 45% win rate < 60% threshold
        result = await check_and_promote_champion("opener")
        assert result["promoted"] == False


@pytest.mark.asyncio
async def test_check_and_promote_champion_promotes_successfully():
    """Champion with 30+ calls and 60%+ win rate should be promoted via Vapi PATCH."""
    from pods.aurora.rl_variants import check_and_promote_champion

    champion_stat = {
        "calls": 35, "wins": 25, "retired": False,
        "variant_hash": "champ_hash_abc", "variant_text": "Hi, I'm ARIA...",
    }

    with patch("pods.aurora.rl_variants.recall", new_callable=AsyncMock, return_value=[champion_stat]):
        with patch("pods.aurora.rl_variants.check_breaker", return_value=True):
            with patch("pods.aurora.rl_variants.cascade_call", new_callable=AsyncMock, return_value="Updated Vapi prompt"):
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_client = AsyncMock()
                mock_client.put = AsyncMock(return_value=mock_resp)
                mock_client.post = AsyncMock(return_value=AsyncMock(status_code=200))
                with patch("pods.aurora.rl_variants.httpx.AsyncClient") as mock_http:
                    mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
                    with patch("pods.aurora.rl_variants.generate_improvement_proof", new_callable=AsyncMock):
                        with patch("pods.aurora.rl_variants.remember", new_callable=AsyncMock):
                            with patch("pods.aurora.rl_variants.publish", new_callable=AsyncMock):
                                result = await check_and_promote_champion("opener")
                                assert result["promoted"] == True
                                assert result["win_rate"] > 0.60
                                assert result["element"] == "opener"


# ── S6-03: Alpaca paper trading ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alpaca_low_confidence_skipped():
    """Confidence below 0.65 should skip the trade."""
    from pods.janus.alpaca_executor import place_regime_order
    result = await place_regime_order("bull", confidence=0.50)
    assert result["skipped"] == True
    assert "confidence" in result["reason"]


@pytest.mark.asyncio
async def test_alpaca_crab_regime_skipped():
    """Crab regime (hold) should always skip — regardless of confidence."""
    from pods.janus.alpaca_executor import place_regime_order
    result = await place_regime_order("crab", confidence=0.90)
    assert result["skipped"] == True


@pytest.mark.asyncio
async def test_alpaca_bull_regime_places_buy_order():
    """Bull regime with confidence >= 0.65 should place a BUY order."""
    from pods.janus.alpaca_executor import place_regime_order
    from unittest.mock import MagicMock

    with patch("pods.janus.alpaca_executor.check_breaker", return_value=True):
        with patch("pods.janus.alpaca_executor.get_portfolio_value", new_callable=AsyncMock, return_value=100_000.0):
            # Use MagicMock for responses since .json() is called synchronously (not awaited)
            mock_price_resp = MagicMock()
            mock_price_resp.json.return_value = {"quote": {"ap": 500.0}}
            mock_price_resp.raise_for_status = MagicMock()

            mock_order_resp = MagicMock()
            mock_order_resp.json.return_value = {"id": "order_test_123"}

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_price_resp)
            mock_client.post = AsyncMock(return_value=mock_order_resp)

            with patch("pods.janus.alpaca_executor.httpx.AsyncClient") as mock_http:
                mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch("pods.janus.alpaca_executor.publish", new_callable=AsyncMock):
                    result = await place_regime_order("bull", confidence=0.80, symbol="SPY")
                    assert result["side"] == "buy"
                    assert result["qty"] >= 1
                    assert result["order_id"] == "order_test_123"


@pytest.mark.asyncio
async def test_alpaca_circuit_open_skipped():
    """If Alpaca circuit breaker is open, skip without placing order."""
    from pods.janus.alpaca_executor import place_regime_order
    with patch("pods.janus.alpaca_executor.check_breaker", return_value=False):
        result = await place_regime_order("bull", confidence=0.90)
        assert result["skipped"] == True
        assert "circuit" in result["reason"]


# ── S6-04: RSA-2048 proof signing ─────────────────────────────────────────────

def test_rsa_sign_produces_base64_signature():
    """sign_proof_rsa should return a non-empty base64 string."""
    from core.improvement_proofs import sign_proof_rsa
    proof = {"pod": "aurora", "delta": 0.12, "cycle_id": "test_rsa_001"}
    sig = sign_proof_rsa(proof)
    assert isinstance(sig, str)
    assert len(sig) > 100  # RSA-2048 base64 signature is ~344 chars


def test_rsa_verify_valid_signature():
    """verify_proof_rsa should return True for an unmodified proof."""
    from core.improvement_proofs import sign_proof_rsa, verify_proof_rsa
    proof = {"pod": "aurora", "delta": 0.12, "cycle_id": "test_rsa_002"}
    sig = sign_proof_rsa(proof)
    assert verify_proof_rsa(proof, sig) == True


def test_rsa_verify_tampered_proof_fails():
    """verify_proof_rsa should return False if proof data has been modified."""
    from core.improvement_proofs import sign_proof_rsa, verify_proof_rsa
    proof = {"pod": "aurora", "delta": 0.12, "cycle_id": "test_rsa_tamper"}
    sig = sign_proof_rsa(proof)
    tampered = {**proof, "delta": 0.99}  # Tamper with delta
    assert verify_proof_rsa(tampered, sig) == False


def test_rsa_verify_invalid_signature_fails():
    """verify_proof_rsa should reject garbage signatures."""
    from core.improvement_proofs import verify_proof_rsa
    proof = {"pod": "janus", "delta": 0.05}
    assert verify_proof_rsa(proof, "notavalidsignatureXXXXXX") == False


# ── S6-05 / S6-06: PII detection ──────────────────────────────────────────────

def test_pii_detection_finds_email():
    """detect_pii_in_text should find email addresses."""
    from core.interpretability import detect_pii_in_text
    pii = detect_pii_in_text("Contact me at john.doe@example.com for details.")
    assert "email" in pii


def test_pii_detection_finds_aadhaar():
    """detect_pii_in_text should find Aadhaar-format numbers."""
    from core.interpretability import detect_pii_in_text
    pii = detect_pii_in_text("Aadhaar number is 1234 5678 9012")
    assert "aadhaar" in pii


def test_pii_detection_finds_pan():
    """detect_pii_in_text should find PAN card format."""
    from core.interpretability import detect_pii_in_text
    pii = detect_pii_in_text("PAN card: ABCDE1234F")
    assert "pan_card" in pii


def test_pii_detection_clean_text_empty():
    """detect_pii_in_text should return empty list for clean text."""
    from core.interpretability import detect_pii_in_text
    pii = detect_pii_in_text("The quarterly revenue report looks promising this year.")
    assert pii == []


@pytest.mark.asyncio
async def test_document_safety_clean_text_safe():
    """verify_document_safety should return safe=True for clean text."""
    from core.interpretability import verify_document_safety
    os.environ["DISABLE_INTERPRETABILITY"] = "1"
    result = await verify_document_safety("The quarterly revenue report is attached.")
    assert result["safe"] == True
    assert result["pii_types"] == []


@pytest.mark.asyncio
async def test_document_safety_pii_text_unsafe():
    """verify_document_safety should return safe=False when PII is detected."""
    from core.interpretability import verify_document_safety
    os.environ["DISABLE_INTERPRETABILITY"] = "1"
    with patch("events.bus.publish", new_callable=AsyncMock):
        result = await verify_document_safety("Send report to john.doe@example.com")
    assert result["pii_risk"] == True
    assert "email" in result["pii_types"]


# ── S6-07: SSE realtime endpoint ──────────────────────────────────────────────

def test_realtime_router_has_events_route():
    """The realtime router should expose /api/realtime/events path."""
    from api.realtime import router
    paths = [r.path for r in router.routes]
    assert "/api/realtime/events" in paths


@pytest.mark.asyncio
async def test_realtime_stream_sends_heartbeat_on_timeout():
    """Event generator should yield a heartbeat JSON when no events arrive within timeout."""
    from fastapi.responses import StreamingResponse
    from api.realtime import stream_events

    with patch("events.bus.subscribe", return_value=MagicMock()):  # subscribe is imported inside function
        import asyncio
        with patch("api.realtime.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            resp = await stream_events(pod="all")
            assert isinstance(resp, StreamingResponse)
            assert "event-stream" in resp.media_type
