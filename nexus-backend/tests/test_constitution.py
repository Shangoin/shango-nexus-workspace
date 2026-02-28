"""tests/test_constitution.py — Sprint 2 S2-06"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_validate_passes_clean_text():
    from core.constitution import get_constitution
    const = get_constitution()
    ok, reason = const.validate("This is a normal message about sales.", pod="aurora")
    assert ok is True
    assert reason is None


def test_validate_blocks_forbidden_phrase():
    from core.constitution import get_constitution
    const = get_constitution()
    ok, reason = const.validate("Let me help you with tax evasion strategies.", pod="dan")
    assert ok is False
    assert reason is not None
    assert "forbidden" in reason.lower() or "Rule" in reason


def test_circuit_breaker_opens_after_threshold():
    from core.constitution import CircuitBreaker
    cb = CircuitBreaker(name="test_breaker", failure_threshold=3, recovery_timeout_seconds=60)
    assert not cb.is_open
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()  # threshold = 3
    assert cb.is_open


def test_circuit_breaker_recovers_after_timeout():
    import time
    from core.constitution import CircuitBreaker
    cb = CircuitBreaker(name="fast_recover", failure_threshold=1, recovery_timeout_seconds=0)
    cb.record_failure()
    assert cb.is_open
    # With recovery_timeout=0, it should recover immediately
    time.sleep(0.01)
    assert not cb.is_open


@pytest.mark.asyncio
async def test_alert_violation_calls_slack_webhook():
    """alert_violation should POST to Slack when SLACK_WEBHOOK_URL is set."""
    import os
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}), \
         patch("core.constitution.httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from core.constitution import alert_violation
        await alert_violation("no_pii_storage", "aurora", "test@example.com")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "hooks.slack.com" in call_kwargs[0][0]


@pytest.mark.asyncio
async def test_alert_violation_skips_when_no_webhook():
    """alert_violation should silently skip when SLACK_WEBHOOK_URL is not set."""
    import os
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLACK_WEBHOOK_URL", None)
        from core.constitution import alert_violation
        # Should not raise any exception
        await alert_violation("test_rule", "nexus", "harmless snippet")
