"""
nexus/pods/janus/market_feed.py
Live market data + MCTS-based regime detection for Janus Trading Brain.

Purpose:  Fetch real-time OHLCV + headlines from Polygon.io and Finnhub.
          Feed signals into MCTS regime detector to classify market state.
Inputs:   symbols list (default: NIFTY/BTC/SPY proxies); no auth required for caller
Outputs:  signals dict (OHLCV + sentiment per symbol); detected regime str
Side Effects: Publishes janus.regime_change event when regime identified
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS = ["SPY", "QQQ", "BTC-USD"]
_REGIME_OPTIONS = ["bull", "bear", "crab", "panic", "recovery"]


async def get_regime_signals(symbols: list[str] | None = None) -> dict:
    """
    Purpose:  Fetch live OHLCV + news sentiment for regime detection inputs.
    Inputs:   symbols list (default: SPY, QQQ, BTC-USD)
    Outputs:  dict keyed by symbol with price/change/volume/sentiment fields
    Side Effects: None (read-only API calls)
    """
    from core.constitution import get_constitution

    symbols = symbols or _DEFAULT_SYMBOLS
    const = get_constitution()
    signals: dict = {}

    polygon_key = os.getenv("POLYGON_API_KEY", "")
    finnhub_key = os.getenv("FINNHUB_API_KEY", "")

    async with httpx.AsyncClient(timeout=10) as client:
        for symbol in symbols:
            sym_data: dict = {"symbol": symbol, "source": "none"}

            # Try Polygon snapshot
            if polygon_key and const.check_breaker("janus_polygon"):
                try:
                    r = await client.get(
                        f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
                        params={"apiKey": polygon_key},
                    )
                    if r.status_code == 200:
                        ticker = r.json().get("ticker", {})
                        day = ticker.get("day", {})
                        sym_data.update({
                            "open": day.get("o", 0),
                            "high": day.get("h", 0),
                            "low": day.get("l", 0),
                            "close": day.get("c", 0),
                            "volume": day.get("v", 0),
                            "change_pct": ticker.get("todaysChangePerc", 0),
                            "source": "polygon",
                        })
                        const.record_success("janus_polygon")
                    else:
                        const.record_failure("janus_polygon")
                except Exception as exc:
                    logger.warning("[market_feed] polygon fail symbol=%s: %s", symbol, exc)
                    const.record_failure("janus_polygon")

            # Try Finnhub sentiment (best-effort overlay)
            if finnhub_key and sym_data.get("source") != "none":
                try:
                    r = await client.get(
                        "https://finnhub.io/api/v1/news-sentiment",
                        params={"symbol": symbol, "token": finnhub_key},
                    )
                    if r.status_code == 200:
                        sentiment = r.json().get("sentiment", {})
                        sym_data["news_sentiment"] = sentiment.get("companyNewsScore", 0.5)
                        sym_data["bullish_pct"] = sentiment.get("bullishPercent", 0.5)
                except Exception:
                    pass  # sentiment is optional enrichment

            signals[symbol] = sym_data

    if not any(v.get("source") != "none" for v in signals.values()):
        logger.info("[market_feed] No live data — returning stub signals")
        # Return stub signals so regime detection can still run
        signals = {s: {"symbol": s, "change_pct": 0.0, "volume": 0, "source": "stub"} for s in symbols}

    return signals


async def detect_regime_live(signals: dict) -> str:
    """
    Purpose:  MCTS + live market signals → classify current market regime.
    Inputs:   signals dict from get_regime_signals()
    Outputs:  regime str — one of bull/bear/crab/panic/recovery
    Side Effects: Publishes janus.regime_change event with detected regime
    """
    from core.mcts_graph import mcts_plan
    from core.ai_cascade import cascade_call
    from events.bus import NexusEvent, publish

    async def simulate_regime(action: str) -> float:
        """Score probability of a given regime given current signals."""
        try:
            score_text = await cascade_call(
                f"Given these live market signals: {signals}\n"
                f"Rate the probability that the current market regime is '{action}' on a scale of 0.0 to 1.0.\n"
                f"Return ONLY a float. No explanation.",
                task_type="regime_detection",
                pod_name="janus",
            )
            return max(0.0, min(1.0, float(score_text.strip().split()[0])))
        except Exception:
            return 0.2  # uniform baseline on failure

    try:
        best_node = await mcts_plan(
            goal="detect_regime",
            possible_actions=_REGIME_OPTIONS,
            simulation_fn=simulate_regime,
            budget=25,
        )
        regime = best_node.action if best_node else "unknown"
    except Exception as exc:
        logger.error("[market_feed] mcts_plan fail: %s", exc)
        regime = "unknown"

    try:
        await publish(
            NexusEvent("janus", "regime_change", {"regime": regime, "symbols": list(signals.keys())}),
            supabase_client=None,
        )
    except Exception:
        pass

    logger.info("[market_feed] detected regime=%s", regime)
    return regime
