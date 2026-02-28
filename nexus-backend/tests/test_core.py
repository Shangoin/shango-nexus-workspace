"""
nexus/tests/test_core.py
Smoke tests for core modules (no external API calls).
Run: cd nexus-backend && pytest tests/ -v --tb=short
"""

from __future__ import annotations

import asyncio
import pytest


# ── ai_cascade.py ────────────────────────────────────────────────────────────

def test_scrub_pii():
    from core.ai_cascade import scrub_pii
    text = "Contact me at test@shango.in or +919876543210"
    result = scrub_pii(text)
    assert "test@shango.in" not in result
    assert "[REDACTED]" in result


def test_humanize():
    from core.ai_cascade import humanize
    text = "We leverage robust solutions to empower your paradigm."
    result = humanize(text)
    assert "leverage" not in result.lower()
    assert "robust" not in result.lower()
    assert "paradigm" not in result.lower()
    assert "use" in result.lower()


def test_cache_key_deterministic():
    from core.ai_cascade import _cache_key
    k1 = _cache_key("hello world", "general")
    k2 = _cache_key("hello world", "general")
    k3 = _cache_key("hello world", "scoring")
    assert k1 == k2
    assert k1 != k3


# ── constitution.py ──────────────────────────────────────────────────────────

def test_constitution_loads():
    from core.constitution import get_constitution
    c = get_constitution()
    assert len(c.rules) > 0
    assert len(c.circuit_breakers) > 0


def test_constitution_validate_pii():
    from core.constitution import get_constitution
    c = get_constitution()
    ok, reason = c.validate("Normal business text without PII")
    assert ok
    ok2, reason2 = c.validate("Send invoice to test@example.com")
    assert not ok2
    assert "PII" in reason2 or "Rule" in reason2


def test_circuit_breaker():
    from core.constitution import CircuitBreaker
    cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout_seconds=5)
    assert not cb.is_open
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open


# ── evolution.py ─────────────────────────────────────────────────────────────

def test_register_pod():
    from core.evolution import register_pod, POD_FITNESS_FNS
    async def dummy_fitness(ind): return 0.5
    register_pod("test_pod_unit", dummy_fitness)
    assert "test_pod_unit" in POD_FITNESS_FNS


def test_increment_event_threshold():
    from core.evolution import increment_event, POD_EVENT_COUNTERS, CYCLE_THRESHOLD
    POD_EVENT_COUNTERS["thresh_test"] = 0
    for i in range(CYCLE_THRESHOLD - 1):
        result = increment_event("thresh_test")
    assert not result
    result = increment_event("thresh_test")
    assert result


@pytest.mark.asyncio
async def test_genetic_cycle_dummy():
    from core.evolution import register_pod, genetic_cycle
    async def fast_fitness(ind): return sum(ind) / len(ind)
    register_pod("test_genetic", fast_fitness)
    result = await genetic_cycle("test_genetic", supabase_client=None)
    assert "best_score" in result
    assert result["pod"] == "test_genetic"
    assert 0 <= result["best_score"] <= 1


# ── events/bus.py ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bus_publish_no_supabase():
    from events.bus import NexusEvent, publish, subscribe
    received = []
    subscribe("test.event", lambda e: received.append(e))
    ev = NexusEvent("test_pod", "test.event", {"key": "val"})
    await publish(ev)  # No supabase — should not raise
    assert len(received) == 1
    assert received[0].payload["key"] == "val"


# ── mcts_graph.py ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcts_plan():
    from core.mcts_graph import mcts_plan
    actions = ["action_a", "action_b", "action_c"]
    async def sim(action: str) -> float:
        return {"action_a": 0.9, "action_b": 0.3, "action_c": 0.6}[action]
    nodes = await mcts_plan("test goal", actions, sim, budget=20)
    assert len(nodes) == 3
    assert nodes[0].action == "action_a"  # highest reward


@pytest.mark.asyncio
async def test_pacv_loop():
    from core.mcts_graph import pacv_loop
    call_count = {"n": 0}
    async def mock_ai(prompt: str) -> str:
        call_count["n"] += 1
        if "Is this output satisfactory" in prompt:
            return "YES"
        if "plan" in prompt.lower():
            return "Step 1: do it."
        return "Here is the result."
    state = await pacv_loop("Write hello world", mock_ai, max_iterations=2)
    assert state.result
    assert state.verified
