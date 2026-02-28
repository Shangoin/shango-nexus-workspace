"""tests/test_market_feed.py — Sprint 3 S3-03"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

_REGIME_OPTIONS = ["bull", "bear", "crab", "panic", "recovery"]


@pytest.mark.asyncio
async def test_detect_regime_live_returns_valid_regime():
    """detect_regime_live must return a string from the known regime list."""

    class MockNode:
        action = "bull"

    with patch("pods.janus.market_feed.mcts_plan", new_callable=AsyncMock, return_value=MockNode()), \
         patch("pods.janus.market_feed.cascade_call", new_callable=AsyncMock, return_value="0.75"), \
         patch("pods.janus.market_feed.publish", new_callable=AsyncMock):
        from pods.janus.market_feed import detect_regime_live
        regime = await detect_regime_live({})

    assert isinstance(regime, str)
    assert regime in _REGIME_OPTIONS or regime == "unknown"


@pytest.mark.asyncio
async def test_detect_regime_live_handles_mcts_failure():
    """detect_regime_live should return 'unknown' when MCTS fails."""
    with patch("pods.janus.market_feed.mcts_plan", new_callable=AsyncMock, side_effect=RuntimeError("MCTS fail")), \
         patch("pods.janus.market_feed.publish", new_callable=AsyncMock):
        from pods.janus.market_feed import detect_regime_live
        regime = await detect_regime_live({"SPY": {"change_pct": -2.0}})

    assert regime == "unknown"


@pytest.mark.asyncio
async def test_get_regime_signals_returns_stub_without_api_key():
    """get_regime_signals returns stub signals when no API key is set."""
    import os
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("POLYGON_API_KEY", None)
        os.environ.pop("FINNHUB_API_KEY", None)

        from pods.janus.market_feed import get_regime_signals
        signals = await get_regime_signals(["SPY"])

    assert isinstance(signals, dict)
    assert "SPY" in signals
    assert signals["SPY"]["source"] == "stub"


@pytest.mark.asyncio
async def test_get_regime_signals_handles_polygon_error():
    """Should still return signals dict when Polygon API errors."""
    import os
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"error": "server error"}

    with patch.dict(os.environ, {"POLYGON_API_KEY": "test_key"}), \
         patch("pods.janus.market_feed.httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from pods.janus.market_feed import get_regime_signals
        signals = await get_regime_signals(["SPY"])

    assert isinstance(signals, dict)


def test_regime_options_are_complete():
    from pods.janus.market_feed import _REGIME_OPTIONS
    assert len(_REGIME_OPTIONS) == 5
    assert "bull" in _REGIME_OPTIONS
    assert "panic" in _REGIME_OPTIONS
