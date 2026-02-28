"""
tests/test_dan_graph.py — Sprint 7 S7-02 (rewritten for correct CI patching)

Purpose:  Full CI-safe test suite for DAN LangGraph state machine.
          Patches at source (core.ai_cascade, events.bus) since imports
          happen inside node functions — no real LLM calls needed.
          Passes with or without OPENAI_API_KEY / network access.
Inputs:   None (all external calls mocked)
Outputs:  pytest pass / fail
Side Effects: None
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pods.dan.graph import dan_app, DANState


# ── Core patch targets (imports happen inside node functions) ────────────────
CASCADE_TARGET = "core.ai_cascade.cascade_call"
PUBLISH_TARGET = "events.bus.publish"
CONSTITUTION_TARGET = "core.constitution.get_constitution"


def _mock_constitution(breaker_open: bool = False):
    """Return a mock constitution instance."""
    mock_const = MagicMock()
    mock_const.validate.return_value = (True, "ok")
    mock_const.check_breaker.return_value = not breaker_open
    mock_const.record_success.return_value = None
    mock_const.record_failure.return_value = None
    return mock_const


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dan_state_reaches_end():
    """Full happy path: planner → critic → executor → verifier → END."""
    async def _mock_cascade(prompt, task_type="", pod_name="dan", **kwargs):
        responses = {
            "planning":     "Step 1: Analyse\nStep 2: Execute\nStep 3: Verify",
            "critique":     "Risk: None identified. Plan looks solid.",
            "execution":    "SUCCESS: All 3 steps completed and confirmed.",
            "verification": "YES — task completed successfully.",
        }
        return responses.get(task_type, "Mock response")

    with patch(CASCADE_TARGET, side_effect=_mock_cascade), \
         patch(PUBLISH_TARGET, new_callable=AsyncMock), \
         patch(CONSTITUTION_TARGET, return_value=_mock_constitution()):
        result = await dan_app.ainvoke(DANState(task="Deploy new FastAPI endpoint"))

    assert isinstance(result, dict)
    assert result["verified"] == True
    assert result["iterations"] >= 1
    assert len(result["plan"]) > 0


@pytest.mark.asyncio
async def test_dan_healer_triggered_on_circuit_open():
    """When executor hits CIRCUIT_OPEN, healer node fires and healed=True."""
    call_n = {"n": 0}

    async def _circuit_cascade(prompt, task_type="", pod_name="dan", **kwargs):
        call_n["n"] += 1
        routing = {
            "planning":     "Step 1: Do thing",
            "critique":     "No risks",
            "execution":    "CIRCUIT_OPEN",         # triggers healer
            "self_heal":    "Recovery: re-route through backup",
            "verification": "YES",
        }
        return routing.get(task_type, "Mock")

    with patch(CASCADE_TARGET, side_effect=_circuit_cascade), \
         patch(PUBLISH_TARGET, new_callable=AsyncMock), \
         patch(CONSTITUTION_TARGET, return_value=_mock_constitution()):
        result = await dan_app.ainvoke(DANState(task="Test circuit open recovery"))

    assert isinstance(result, dict)
    assert result["healed"] == True


@pytest.mark.asyncio
async def test_dan_max_retries_stops_at_3():
    """Executor always fails → iterations are capped at 3."""
    async def _always_fail(prompt, task_type="", pod_name="dan", **kwargs):
        if task_type == "execution":
            return "error: connection refused"
        if task_type == "verification":
            return "NO — task failed"
        if task_type in ("planning", "self_heal"):
            return "1. Retry step"
        return "No risks"

    with patch(CASCADE_TARGET, side_effect=_always_fail), \
         patch(PUBLISH_TARGET, new_callable=AsyncMock), \
         patch(CONSTITUTION_TARGET, return_value=_mock_constitution()):
        result = await dan_app.ainvoke(DANState(task="Deliberately failing task"))

    assert result["iterations"] <= 3


@pytest.mark.asyncio
async def test_dan_router_invokes_graph():
    """Router calls dan_app.ainvoke and returns TaskResponse."""
    with patch("pods.dan.router.dan_app") as mock_app, \
         patch("pods.dan.router.publish", new_callable=AsyncMock), \
         patch("pods.dan.router.get_supabase", return_value=MagicMock()):
        mock_app.ainvoke = AsyncMock(return_value=DANState(
            task="test",
            plan="Step 1",
            result="SUCCESS",
            verified=True,
            iterations=1,
            healed=False,
        ))
        from pods.dan.router import run_task, TaskRequest
        req = MagicMock()  # Simulate FastAPI Request
        resp = await run_task(TaskRequest(input="test task"), req)

    assert resp.verified == True
    mock_app.ainvoke.assert_called_once()


def test_dan_state_model_defaults():
    """DANState Pydantic model initialises with correct defaults."""
    state = DANState(task="test task")
    assert state.task == "test task"
    assert state.plan == ""
    assert state.iterations == 0
    assert state.verified == False
    assert state.healed == False
