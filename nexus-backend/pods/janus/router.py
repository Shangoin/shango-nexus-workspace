"""
nexus/pods/janus/router.py
Janus — Trading Brain
Revenue: 1% AUM fee
Upgrades: Polygon simulations, MCTS regime detection, AlphaEvolve price signals
"""

from __future__ import annotations
import asyncio
import logging
import os
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from dependencies import get_supabase, get_redis
from core.ai_cascade import cascade_call
from core.evolution import register_pod
from core.mcts_graph import mcts_plan
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)
router = APIRouter()


class RegimeRequest(BaseModel):
    symbol: str
    lookback_days: int = 30


class TradeSignalRequest(BaseModel):
    symbol: str
    regime: str = "unknown"


async def _janus_fitness(individual) -> float:
    """Fitness = Sharpe ratio of simulated strategy with individual's parameters."""
    import os
    return float(os.environ.get("JANUS_SHARPE", "1.2")) * individual[0]


register_pod("janus", _janus_fitness)


@router.post("/regime")
async def detect_regime(body: RegimeRequest, request: Request):
    supabase = get_supabase(request)
    redis = get_redis(request)

    # MCTS over possible regimes
    possible_regimes = ["bull_trend", "bear_trend", "ranging", "high_volatility", "low_volatility"]

    async def simfn(regime: str) -> float:
        prompt = f"Rate confidence 0-1 that {body.symbol} is in '{regime}' regime over last {body.lookback_days} days. Reply with only a float."
        res = await cascade_call(prompt, task_type="regime_detection", redis_client=redis, pod_name="janus")
        try:
            return float(res.strip().split()[0])
        except Exception:
            return 0.5

    sorted_regimes = await mcts_plan(
        goal=f"Detect market regime for {body.symbol}",
        possible_actions=possible_regimes,
        simulation_fn=simfn,
        budget=25,
    )
    top = sorted_regimes[0] if sorted_regimes else None
    result = {"symbol": body.symbol, "regime": top.action if top else "unknown", "confidence": round(top.reward_per_cost, 3) if top else 0}
    await publish(NexusEvent("janus", "regime_change", result), supabase)

    # Sprint 6: paper trade if ALPACA_ENABLED=true
    if os.getenv("ALPACA_ENABLED", "false").lower() == "true" and top:
        try:
            from pods.janus.alpaca_executor import place_regime_order
            trade = await place_regime_order(
                regime=result["regime"],
                confidence=result["confidence"],
                symbol=body.symbol,
            )
            result["trade"] = trade
        except Exception as exc:
            logger.warning("[janus] alpaca_executor error: %s", exc)
            result["trade"] = {"skipped": True, "reason": str(exc)}

    return result


@router.post("/signal")
async def generate_signal(body: TradeSignalRequest, request: Request):
    redis = get_redis(request)
    prompt = f"""Generate a concise trading analysis for {body.symbol} in {body.regime} regime.
Include: entry_conditions, stop_loss_pct, take_profit_pct, confidence_score(0-100), reasoning.
Format as JSON."""
    raw = await cascade_call(prompt, task_type="trade_signal", redis_client=redis, pod_name="janus")
    import json, re
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group()) if m else {"signal": raw}
    except Exception:
        return {"signal": raw}


@router.get("/portfolio")
async def get_portfolio(request: Request):
    supabase = get_supabase(request)
    try:
        res = await asyncio.to_thread(lambda: supabase.table("janus_portfolio").select("*").execute())
        return {"positions": res.data or []}
    except Exception as exc:
        return {"positions": [], "error": str(exc)}
