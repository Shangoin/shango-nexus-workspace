"""
nexus/pods/janus/alpaca_executor.py
Janus — Alpaca Paper Trading Executor (Sprint 6)

Purpose:     Wire Janus regime signals to Alpaca paper trading orders.
             Bull regime → increase allocation; Bear → reduce/hedge; Panic → exit.
             Crab or low-confidence → hold (skip trade).
Inputs:      regime (str), confidence (float), symbol (str, default "SPY")
Outputs:     Alpaca order response dict or {"skipped": True, "reason": str}
Side Effects: Places paper trade order via Alpaca REST API,
              publishes janus.order_placed event to event bus.
"""

from __future__ import annotations

import logging
import os

import httpx

from core.constitution import check_breaker
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)

ALPACA_BASE = "https://paper-api.alpaca.markets"  # Paper trading — safe default

# Regime → side + portfolio percentage
REGIME_ALLOCATION: dict[str, dict] = {
    "bull":        {"side": "buy",  "qty_pct": 0.10},  # 10% of portfolio
    "bull_trend":  {"side": "buy",  "qty_pct": 0.10},  # MCTS label alias
    "recovery":    {"side": "buy",  "qty_pct": 0.05},  # 5% cautious buy
    "crab":        {"side": None,   "qty_pct": 0.00},  # Hold — no trade
    "ranging":     {"side": None,   "qty_pct": 0.00},  # Hold — no trade
    "bear":        {"side": "sell", "qty_pct": 0.05},  # 5% reduce
    "bear_trend":  {"side": "sell", "qty_pct": 0.05},  # MCTS label alias
    "panic":       {"side": "sell", "qty_pct": 0.15},  # 15% exit
    "high_volatility": {"side": None, "qty_pct": 0.00},  # Hold — too risky
}

_ALPACA_HEADERS_TEMPLATE = {
    "Content-Type": "application/json",
}


def _alpaca_headers() -> dict:
    return {
        **_ALPACA_HEADERS_TEMPLATE,
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_API_SECRET", os.getenv("ALPACA_SECRET_KEY", "")),
    }


async def get_portfolio_value() -> float:
    """
    Purpose:     Fetch current Alpaca paper portfolio value.
    Inputs:      None (reads ALPACA_API_KEY + ALPACA_API_SECRET from env)
    Outputs:     float portfolio value in USD
    Side Effects: None
    """
    if not check_breaker("alpaca"):
        logger.debug("[alpaca_executor] alpaca circuit open — returning default $100k")
        return 100_000.0

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{ALPACA_BASE}/v2/account",
                headers=_alpaca_headers(),
            )
            r.raise_for_status()
            return float(r.json().get("portfolio_value", 100_000.0))
    except Exception as exc:
        logger.warning("[alpaca_executor] get_portfolio_value failed: %s — using $100k", exc)
        return 100_000.0


async def place_regime_order(
    regime: str,
    confidence: float,
    symbol: str = "SPY",
) -> dict:
    """
    Purpose:     Place Alpaca paper order based on Janus regime signal.
    Inputs:      regime — market regime string from Janus MCTS;
                 confidence — float 0.0–1.0 from MCTS reward;
                 symbol — ticker symbol (default "SPY")
    Outputs:     Alpaca order response dict, or {"skipped": True, "reason": str}
    Side Effects: Alpaca paper order placed, janus.order_placed event published
    """
    # Safety: skip if confidence below threshold
    if confidence < 0.65:
        logger.debug("[alpaca_executor] skipping — confidence %.2f below 0.65", confidence)
        return {"skipped": True, "reason": f"confidence {confidence:.2f} below 0.65 threshold"}

    allocation = REGIME_ALLOCATION.get(regime)
    if not allocation or not allocation.get("side"):
        logger.debug("[alpaca_executor] skipping — regime '%s' maps to hold", regime)
        return {"skipped": True, "reason": f"hold regime '{regime}' — no trade"}

    if not check_breaker("alpaca"):
        return {"skipped": True, "reason": "alpaca circuit open"}

    portfolio_value = await get_portfolio_value()

    # Fetch latest ask price for symbol
    ask_price = 100.0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            price_r = await client.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
                headers=_alpaca_headers(),
            )
            ask_price = float(
                price_r.json().get("quote", {}).get("ap", 100.0)
            ) or 100.0
    except Exception as exc:
        logger.warning("[alpaca_executor] price fetch failed for %s: %s — using $100", symbol, exc)

    # Calculate order quantity
    trade_value = portfolio_value * allocation["qty_pct"] * confidence
    qty = max(1, int(trade_value / ask_price))

    # Build idempotency key
    client_order_id = f"janus_{regime}_{symbol}_{int(confidence * 100)}"

    # Place market order
    order_id = "unknown"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            order_r = await client.post(
                f"{ALPACA_BASE}/v2/orders",
                headers=_alpaca_headers(),
                json={
                    "symbol": symbol,
                    "qty": str(qty),
                    "side": allocation["side"],
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": client_order_id,
                },
            )
            order = order_r.json()
            order_id = order.get("id", "unknown")
    except Exception as exc:
        logger.error("[alpaca_executor] order placement failed: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    await publish(
        NexusEvent(
            pod="janus",
            event_type="janus.order_placed",
            payload={
                "regime": regime,
                "confidence": confidence,
                "symbol": symbol,
                "side": allocation["side"],
                "qty": qty,
                "order_id": order_id,
            },
        )
    )

    logger.info(
        "[alpaca_executor] ✅ order placed regime=%s side=%s qty=%d symbol=%s order_id=%s",
        regime, allocation["side"], qty, symbol, order_id,
    )

    return {
        "order_id": order_id,
        "regime": regime,
        "side": allocation["side"],
        "qty": qty,
        "symbol": symbol,
        "estimated_value": round(qty * ask_price, 2),
    }
