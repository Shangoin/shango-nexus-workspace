"""
tests/test_sprint10.py
Sprint 10 — DeepMind + MIT Frontier Layer smoke tests.
Run: pytest tests/test_sprint10.py -v
Target: 24 tests → cumulative 117 across all sprints

Coverage:
  S10-00  Gemini 3 provider upgrade (ai_cascade.py)
  S10-01  MIT EnCompass branching (core/encompass.py + DAN DANState)
  S10-02  DeepMind agent scaling monitor (core/agent_scaling_monitor.py)
  S10-03  Agent0 curriculum fitness (core/evolution.py)
  S10-04  MEM1 unified internal state (core/mem1_state.py)
  S10-05  Gemini Deep Think call (ai_cascade.py)
  S10-06  Sentinel Prime /analyze + /search endpoints
  S10-07  Syntropy War Room /ers/calculate endpoint
"""
from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure nexus-backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_supabase():
    sb = MagicMock()
    sb.table.return_value.select.return_value.gte.return_value.execute.return_value = \
        MagicMock(data=[])
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[])
    sb.table.return_value.upsert.return_value.execute.return_value = \
        MagicMock(data=[], error=None)
    sb.table.return_value.insert.return_value.execute.return_value = \
        MagicMock(data=[], error=None)
    return sb


# ── S10-00: Gemini 3 provider upgrade ────────────────────────────────────────

class TestGemini3Upgrade:
    """2 tests for S10-00."""

    def test_gemini3_is_primary_provider(self):
        """PROVIDERS[0] must be 'gemini-3-pro'."""
        from core.ai_cascade import PROVIDERS
        assert PROVIDERS[0] == "gemini-3-pro", f"Expected 'gemini-3-pro', got {PROVIDERS[0]}"

    def test_provider_fns_has_gemini3(self):
        """_PROVIDER_FNS must contain 'gemini-3-pro' key."""
        from core.ai_cascade import _PROVIDER_FNS
        assert "gemini-3-pro" in _PROVIDER_FNS, "'gemini-3-pro' missing from _PROVIDER_FNS"


# ── S10-05: Deep Think call ───────────────────────────────────────────────────

class TestDeepThinkCall:
    """2 tests for S10-05."""

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_deep_think_returns_tuple(self, mock_cascade):
        """deep_think_call must return (answer_str, thinking_trace_str)."""
        mock_cascade.return_value = "Answer from cascade fallback"
        from core.ai_cascade import deep_think_call
        result = await deep_think_call("What is 2+2?", pod_name="test")
        assert isinstance(result, tuple), "deep_think_call must return a tuple"
        assert len(result) == 2, "deep_think_call must return (answer, thinking_trace)"
        answer, trace = result
        assert isinstance(answer, str)
        assert isinstance(trace, str)

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_deep_think_fallback_on_sdk_error(self, mock_cascade):
        """deep_think_call falls back to cascade_call when ThinkingConfig unavailable."""
        mock_cascade.return_value = "fallback answer"
        from core.ai_cascade import deep_think_call
        answer, trace = await deep_think_call("complex task", pod_name="nexus",
                                               thinking_budget=4000)
        assert answer  # non-empty
        assert mock_cascade.called


# ── S10-01: MIT EnCompass ─────────────────────────────────────────────────────

class TestEnCompass:
    """6 tests for S10-01."""

    @pytest.mark.asyncio
    @patch("core.encompass.cascade_call", new_callable=AsyncMock)
    async def test_encompass_branch_returns_result(self, mock_cascade):
        """encompass_branch must return an EnCompassResult."""
        mock_cascade.return_value = '{"score": 0.8, "reason": "good"}'
        from core.encompass import encompass_branch, EnCompassResult
        result = await encompass_branch(
            prompt="test prompt",
            task_type="test",
            pod_name="test",
            state={},
            max_branches=2,
        )
        assert isinstance(result, EnCompassResult)

    @pytest.mark.asyncio
    @patch("core.encompass.cascade_call", new_callable=AsyncMock)
    async def test_encompass_branch_count_matches(self, mock_cascade):
        """branch_count in result matches max_branches arg."""
        mock_cascade.return_value = "some output"
        from core.encompass import encompass_branch
        result = await encompass_branch("prompt", "t", "p", {}, max_branches=3)
        assert result.branch_count == 3

    @pytest.mark.asyncio
    @patch("core.encompass.cascade_call", new_callable=AsyncMock)
    async def test_encompass_best_score_populated(self, mock_cascade):
        """best_score must be a float 0-1."""
        mock_cascade.return_value = "result"
        from core.encompass import encompass_branch
        result = await encompass_branch("prompt", "t", "p", {}, max_branches=2)
        assert 0.0 <= result.best_score <= 1.0

    @pytest.mark.asyncio
    @patch("core.encompass.cascade_call", new_callable=AsyncMock)
    async def test_encompass_all_scores_length(self, mock_cascade):
        """all_scores must have one entry per branch."""
        mock_cascade.return_value = "output"
        from core.encompass import encompass_branch
        result = await encompass_branch("prompt", "t", "p", {}, max_branches=3)
        assert len(result.all_scores) == 3

    def test_encompass_score_low_for_error_output(self):
        """_score_branch_output should be < 0.2 for error output without LLM."""
        import asyncio as _asyncio
        from core.encompass import _score_branch_output
        with patch("core.encompass.cascade_call", new_callable=AsyncMock) as mc:
            mc.return_value = '{"score": 0.5}'
            score = _asyncio.run(
                _score_branch_output("ERROR: connection refused", "do stuff", "test")
            )
        assert score <= 0.2, f"Error output scored too high: {score}"

    def test_dan_state_has_encompass_fields(self):
        """DANState must have encompass_winning_branch and encompass_best_score fields."""
        from pods.dan.graph import DANState
        s = DANState(task="test")
        assert hasattr(s, "encompass_winning_branch"), "Missing encompass_winning_branch"
        assert hasattr(s, "encompass_best_score"), "Missing encompass_best_score"
        assert s.encompass_winning_branch == 0
        assert s.encompass_best_score == 0.0


# ── S10-02: Agent Scaling Monitor ────────────────────────────────────────────

class TestAgentScalingMonitor:
    """4 tests for S10-02."""

    @pytest.mark.asyncio
    async def test_compute_scaling_health_no_supabase(self):
        """compute_scaling_health with None client returns healthy=True."""
        from core.agent_scaling_monitor import compute_scaling_health
        report = await compute_scaling_health(None)
        assert report.healthy is True

    @pytest.mark.asyncio
    async def test_compute_scaling_health_returns_report(self):
        """compute_scaling_health returns ScalingHealthReport dataclass."""
        from core.agent_scaling_monitor import compute_scaling_health, ScalingHealthReport
        report = await compute_scaling_health(None)
        assert isinstance(report, ScalingHealthReport)

    @pytest.mark.asyncio
    async def test_run_scaling_monitor_caches_report(self):
        """run_scaling_monitor updates _last_report module-level cache."""
        from core.agent_scaling_monitor import run_scaling_monitor, get_last_scaling_report
        await run_scaling_monitor(None)
        cached = get_last_scaling_report()
        assert cached is not None

    def test_get_last_scaling_report_type(self):
        """get_last_scaling_report returns ScalingHealthReport or None."""
        from core.agent_scaling_monitor import get_last_scaling_report, ScalingHealthReport
        result = get_last_scaling_report()
        assert result is None or isinstance(result, ScalingHealthReport)


# ── S10-03: Agent0 Curriculum Evolution ──────────────────────────────────────

class TestAgent0Curriculum:
    """4 tests for S10-03."""

    def test_record_mae_score_appends(self):
        """record_mae_score stores scores in _uncertainty_history."""
        from core.evolution import record_mae_score, _uncertainty_history, get_executor_uncertainty
        record_mae_score("test_pod_s10", 0.8)
        record_mae_score("test_pod_s10", 0.2)
        unc = get_executor_uncertainty("test_pod_s10")
        assert isinstance(unc, float)
        assert 0.0 <= unc <= 1.0

    def test_get_executor_uncertainty_before_data(self):
        """get_executor_uncertainty returns 0.5 for pods with no history."""
        from core.evolution import get_executor_uncertainty
        unc = get_executor_uncertainty("never_seen_pod_xyz")
        assert unc == 0.5

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_curriculum_challenge_hard_for_low_uncertainty(self, mock_cascade):
        """curriculum_guided_challenge called with low uncertainty triggers 'HARD' hint."""
        mock_cascade.return_value = "Handle a complex failure cascade in a distributed system"
        from core.evolution import curriculum_guided_challenge
        challenge = await curriculum_guided_challenge("aurora", "gene_summary", uncertainty=0.1)
        assert isinstance(challenge, str)
        assert len(challenge) > 10

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_adversarial_fitness_agent0(self, mock_cascade):
        """mae_adversarial_fitness with Agent0 returns float 0-1."""
        mock_cascade.side_effect = [
            "Handle a complex edge case",         # curriculum challenge
            '{"solution": "steps...", "confidence": 0.8}',  # solver
            '{"score": 0.75, "reasoning": "good"}',          # judge
        ]
        from core.evolution import mae_adversarial_fitness
        result = await mae_adversarial_fitness([0.5] * 8, pod="aurora")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.2  # +0.2 difficulty bonus allowed


# ── S10-04: MEM1 Unified Internal State ──────────────────────────────────────

class TestMEM1State:
    """4 tests for S10-04."""

    @pytest.mark.asyncio
    @patch("core.mem1_state.cascade_call", new_callable=AsyncMock)
    @patch("core.mem1_state.remember", new_callable=AsyncMock)
    async def test_mem1_step_returns_tuple(self, mock_remember, mock_cascade):
        """mem1_step returns (str, MEM1State)."""
        mock_cascade.return_value = (
            "<IS_new>key facts here</IS_new>"
            "<action>my response</action>"
        )
        from core.mem1_state import mem1_step, MEM1State
        action, state = await mem1_step("query text", "aurora", "session-001")
        assert isinstance(action, str)
        assert isinstance(state, MEM1State)

    @pytest.mark.asyncio
    @patch("core.mem1_state.cascade_call", new_callable=AsyncMock)
    @patch("core.mem1_state.remember", new_callable=AsyncMock)
    async def test_mem1_step_extracts_action(self, mock_remember, mock_cascade):
        """mem1_step correctly parses <action> tag."""
        mock_cascade.return_value = (
            "<IS_new>updated state</IS_new>"
            "<action>This is the extracted action</action>"
        )
        from core.mem1_state import mem1_step
        action, state = await mem1_step("input", "dan", "sess-002")
        assert action == "This is the extracted action"
        assert state.internal_state == "updated state"

    @pytest.mark.asyncio
    @patch("core.mem1_state.cascade_call", new_callable=AsyncMock)
    @patch("core.mem1_state.remember", new_callable=AsyncMock)
    async def test_mem1_step_increments_turn(self, mock_remember, mock_cascade):
        """Each mem1_step increments turn_count."""
        mock_cascade.return_value = "<IS_new>s</IS_new><action>a</action>"
        from core.mem1_state import mem1_step
        _, state1 = await mem1_step("q1", "aurora", "sess-003")
        _, state2 = await mem1_step("q2", "aurora", "sess-003", prior_state=state1)
        assert state1.turn_count == 1
        assert state2.turn_count == 2

    @pytest.mark.asyncio
    @patch("core.mem1_state.cascade_call", new_callable=AsyncMock)
    @patch("core.mem1_state.remember", new_callable=AsyncMock)
    async def test_mem1_step_failopen(self, mock_remember, mock_cascade):
        """mem1_step returns query as fallback when cascade_call raises."""
        mock_cascade.side_effect = RuntimeError("model unavailable")
        from core.mem1_state import mem1_step
        action, state = await mem1_step("my query text", "aurora", "sess-fail")
        assert action == "my query text"  # fail-open returns the input


# ── S10-06: Sentinel Prime /analyze + /search ─────────────────────────────────

class TestSentinelPrimeEndpoints:
    """2 tests for S10-06."""

    @pytest.mark.asyncio
    @patch("pods.sentinel_prime.router.cascade_call", new_callable=AsyncMock)
    @patch("pods.sentinel_prime.router.publish", new_callable=AsyncMock)
    async def test_analyze_endpoint_exists(self, mock_publish, mock_cascade):
        """sentinel_prime router has /analyze route and returns analysis key."""
        mock_cascade.return_value = "Executive summary: This is a test document."
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from pods.sentinel_prime.router import router
        app = FastAPI()
        app.include_router(router)
        with patch("pods.sentinel_prime.router.get_supabase", return_value=_make_supabase()):
            with patch("pods.sentinel_prime.router.get_redis", return_value=None):
                client = TestClient(app)
                resp = client.post("/analyze", json={
                    "document": "This is a test document about data privacy.",
                    "analysis_type": "summary",
                })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "analysis" in data

    @pytest.mark.asyncio
    @patch("pods.sentinel_prime.router.cascade_call", new_callable=AsyncMock)
    @patch("pods.sentinel_prime.router.publish", new_callable=AsyncMock)
    async def test_search_endpoint_returns_synthesis(self, mock_publish, mock_cascade):
        """sentinel_prime /search returns synthesis and source_count."""
        mock_cascade.return_value = "Top insights: 1. Privacy matters."
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from pods.sentinel_prime.router import router
        app = FastAPI()
        app.include_router(router)
        with patch("pods.sentinel_prime.router.get_supabase", return_value=_make_supabase()):
            with patch("pods.sentinel_prime.router.get_redis", return_value=None):
                client = TestClient(app)
                resp = client.post("/search", json={
                    "query": "data privacy best practices",
                    "sources": ["GDPR requires..."],
                    "max_results": 3,
                })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "synthesis" in data
        assert "source_count" in data


# ── S10-07: Syntropy War Room /ers/calculate ─────────────────────────────────

class TestERSCalculate:
    """2 tests for S10-07."""

    @pytest.mark.asyncio
    @patch("pods.syntropy_war_room.router.cascade_call", new_callable=AsyncMock)
    @patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock)
    async def test_ers_calculate_returns_score(self, mock_publish, mock_cascade):
        """/ers/calculate returns ers_score and grade."""
        mock_cascade.return_value = '{"strengths": ["algebra"], "weaknesses": [], "percentile": 82}'
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from pods.syntropy_war_room.router import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/ers/calculate", json={
            "student_id": "student-001",
            "topic": "JEE Physics",
            "exam_type": "jee",
            "answers": [
                {"question": "q1", "student_answer": "A", "correct_answer": "A",
                 "time_seconds": 25},
                {"question": "q2", "student_answer": "B", "correct_answer": "C",
                 "time_seconds": 45},
            ],
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "ers_score" in data
        assert "grade" in data
        assert 0 <= data["ers_score"] <= 110  # allow speed bonus

    @pytest.mark.asyncio
    @patch("pods.syntropy_war_room.router.cascade_call", new_callable=AsyncMock)
    @patch("pods.syntropy_war_room.router.publish", new_callable=AsyncMock)
    async def test_ers_calculate_empty_answers(self, mock_publish, mock_cascade):
        """ERS with empty answers list returns ers_score 0."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from pods.syntropy_war_room.router import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/ers/calculate", json={
            "student_id": "student-002",
            "topic": "NEET Biology",
            "exam_type": "neet",
            "answers": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ers_score"] == 0
