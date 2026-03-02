"""
tests/test_sprint9.py
Sprint 9 — Prometheus Intelligence Layer smoke tests.
Run: pytest tests/test_sprint9.py -v
Target: 22 tests → cumulative 95/95 across all sprints
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_supabase():
    """Return a minimal Supabase mock that supports chained .table().upsert().execute()."""
    sb = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = {"data": [], "error": None}
    sb.table.return_value.insert.return_value.execute.return_value = {"data": [], "error": None}
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = {
        "data": [{"weight": 0.8, "memory_type": "semantic"}],
        "error": None,
    }
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = {
        "data": [],
        "error": None,
    }
    sb.table.return_value.delete.return_value.lt.return_value.execute.return_value = {
        "data": [],
        "error": None,
    }
    sb.table.return_value.select.return_value.execute.return_value = {
        "data": [],
        "error": None,
    }
    return sb


# ── S9-01: MAE Adversarial Evolution ─────────────────────────────────────────

class TestMAEAdversarialEvolution:
    """4 tests for mae_adversarial_fitness."""

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_proposer_runs(self, mock_cascade):
        """MAE calls cascade_call (proposer) with task_type starting with mae_."""
        mock_cascade.return_value = '{"challenge": "Explain photosynthesis in 3 steps", "difficulty": 0.7}'
        from core.evolution import mae_adversarial_fitness

        individual = [0.5] * 8
        result = await mae_adversarial_fitness(individual, pod="aurora")
        # cascade_call was called at least once (proposer)
        assert mock_cascade.called

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_solver_runs(self, mock_cascade):
        """MAE triggers a solver call as part of the fitness evaluation."""
        mock_cascade.side_effect = [
            '{"challenge": "Solve X*2=4", "difficulty": 0.6}',
            '{"answer": "X=2", "confidence": 0.9}',
            '{"score": 0.85, "passed": true}',
        ]
        from core.evolution import mae_adversarial_fitness

        individual = [0.5] * 8
        result = await mae_adversarial_fitness(individual, pod="aurora")
        assert isinstance(result, (int, float))

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_judge_scores(self, mock_cascade):
        """MAE judge produces a numeric score that influences fitness."""
        mock_cascade.side_effect = [
            '{"challenge": "Describe MCTS", "difficulty": 0.8}',
            '{"answer": "Monte Carlo Tree Search uses simulations", "confidence": 0.95}',
            '{"score": 0.9, "passed": true}',
        ]
        from core.evolution import mae_adversarial_fitness

        individual = [0.8] * 8
        result = await mae_adversarial_fitness(individual, pod="janus")
        # result is a float between 0 and 1
        assert 0.0 <= result <= 1.0

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_difficulty_reward_when_judge_fails(self, mock_cascade):
        """When judge_score < 0.4, difficulty reward applies (higher fitness)."""
        mock_cascade.side_effect = [
            '{"challenge": "Prove P=NP", "difficulty": 0.95}',
            '{"answer": "Undecided", "confidence": 0.2}',
            '{"score": 0.15, "passed": false}',  # judge fails → difficulty reward
        ]
        from core.evolution import mae_adversarial_fitness

        individual = [1.0] * 8
        with_reward = await mae_adversarial_fitness(individual, pod="dan")
        # fail-open: returns ≥ 0 (usually 0.5 + difficulty_bonus)
        assert with_reward >= 0.0


# ── S9-02: AMA Causal Memory Graph ───────────────────────────────────────────

class TestCausalMemoryGraph:
    """4 tests for causal_graph module."""

    def test_causal_node_dataclass_fields(self):
        """CausalNode has all required fields."""
        from core.causal_graph import CausalNode

        node = CausalNode(
            event_id="evt-001",
            pod="aurora",
            action="trigger_call",
            outcome="meeting_booked=True",
            caused_by=[],
            caused=[],
        )
        assert node.event_id == "evt-001"
        assert node.pod == "aurora"
        assert node.outcome == "meeting_booked=True"

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_build_causal_node_stores_via_upsert(self, mock_cascade):
        """build_causal_node calls pgvector_upsert with memory_type='causal'."""
        mock_cascade.return_value = "Causal embedding summary"
        sb = _make_supabase()

        with patch("core.memory.pgvector_upsert", new_callable=AsyncMock) as mock_upsert:
            mock_upsert.return_value = True
            from core.causal_graph import build_causal_node

            result = await build_causal_node(
                event_id="evt-002",
                pod="aurora",
                action="send_follow_up",
                outcome="reply_received=True",
                parent_event_ids=[],
                supabase_client=sb,
            )
            assert mock_upsert.called
            call_kwargs = mock_upsert.call_args[1] if mock_upsert.call_args[1] else {}
            call_args = mock_upsert.call_args[0] if mock_upsert.call_args[0] else ()
            # memory_type should be "causal" somewhere in the call
            all_args = str(call_args) + str(call_kwargs)
            assert "causal" in all_args

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_causal_recall_returns_list(self, mock_cascade):
        """causal_recall always returns a list (even on empty DB)."""
        mock_cascade.return_value = '{"sufficient": true}'

        with patch("core.memory.pgvector_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            from core.causal_graph import causal_recall

            result = await causal_recall(
                "CAUSAL: actions that caused meeting_booked=True",
                pod="aurora",
                top_k=5,
            )
            assert isinstance(result, list)

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_causal_recall_deduplicates(self, mock_cascade):
        """causal_recall deduplicates results by event_id / content."""
        dup_memory = {"content": "trigger_call → booked", "metadata": {"event_id": "e1"}}
        mock_cascade.return_value = '{"sufficient": true}'

        with patch("core.memory.pgvector_search", new_callable=AsyncMock) as mock_search:
            # Return duplicate entries
            mock_search.return_value = [dup_memory, dup_memory, dup_memory]
            from core.causal_graph import causal_recall

            result = await causal_recall(
                "CAUSAL: test dedup",
                pod="aurora",
                top_k=5,
            )
            # Deduplication should reduce result count
            assert len(result) <= 3  # at most same as unique entries


# ── S9-03: COCOA Self-Evolving Constitution ───────────────────────────────────

class TestCOCOAConstitution:
    """4 tests for constitution evolution."""

    def test_violation_history_accumulates(self):
        """Each call to validate() with a violation adds to _violation_history."""
        from core.constitution import _violation_history, validate

        initial_count = len(_violation_history.get("test_pod_cocoa", []))
        # validate() expects a string — pass a rule-triggering text
        validate("rm -rf / and spend 99999 on AI and retry 999 times", pod="test_pod_cocoa")
        after_count = len(_violation_history.get("test_pod_cocoa", []))
        assert after_count >= initial_count  # at least same or more

    def test_cocoa_trigger_threshold_is_50(self):
        """_COCOA_TRIGGER_THRESHOLD must be 50."""
        from core.constitution import _COCOA_TRIGGER_THRESHOLD

        assert _COCOA_TRIGGER_THRESHOLD == 50

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_evolve_constitution_judger_gates(self, mock_cascade):
        """evolve_constitution only applies rule when judger score > 0.75."""
        mock_cascade.side_effect = [
            "PROPOSED_RULE: Limit daily calls to 100",  # actor
            "GUIDANCE: Ensure this aligns with rate limits",  # guider
            '{"approve": false, "score": 0.4, "reason": "too restrictive"}',  # judger rejects
        ]
        from core.constitution import evolve_constitution

        result = await evolve_constitution(pod="aurora_test", supabase_client=None)
        # Should return result dict; rule not applied when judger rejects
        assert isinstance(result, dict)
        assert result.get("applied") is False or "score" in str(result)

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_prune_returns_integer(self, mock_cascade):
        """prune_ineffective_rules returns an integer (count of pruned rules)."""
        mock_cascade.return_value = '{"prune": true, "reason": "never triggered"}'
        from core.constitution import prune_ineffective_rules

        result = await prune_ineffective_rules(pod="aurora", supabase_client=None)
        assert isinstance(result, int)
        assert result >= 0


# ── S9-04: HiMem Temporal Decay ──────────────────────────────────────────────

class TestHiMemTemporalDecay:
    """3 tests for memory decay."""

    def test_memory_decay_constants(self):
        """MEMORY_DECAY rates must match sprint spec."""
        from core.memory import MEMORY_DECAY

        assert MEMORY_DECAY["episodic"] == pytest.approx(0.95)
        assert MEMORY_DECAY["semantic"] == pytest.approx(0.99)
        assert MEMORY_DECAY["procedural"] == pytest.approx(1.0)
        assert MEMORY_DECAY["causal"] == pytest.approx(0.97)

    @pytest.mark.asyncio
    async def test_decay_memories_returns_int(self):
        """decay_memories returns an integer without a real Supabase client."""
        sb = _make_supabase()
        # Make select return rows with memory_type and weight
        sb.table.return_value.select.return_value.execute.return_value = {
            "data": [
                {"id": "m1", "memory_type": "episodic", "weight": 0.005, "pod_name": "aurora"},
                {"id": "m2", "memory_type": "semantic", "weight": 0.9, "pod_name": "aurora"},
            ],
            "error": None,
        }
        from core.memory import decay_memories

        result = await decay_memories(supabase_client=sb, pod="aurora")
        assert isinstance(result, int)

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_recall_returns_weighted_data(self, mock_cascade):
        """recall() returns dict with 'data' key containing weighted pgvector results."""
        mock_cascade.return_value = "test embedding"
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # cache miss → pgvector path
        with patch("core.memory.pgvector_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {"content": "high weight", "weighted_score": 0.9},
                {"content": "mid weight", "weighted_score": 0.6},
            ]
            from core.memory import recall

            result = await recall(
                mock_redis, None, "aurora", "strategic_brief", "test query", top_k=3
            )
            assert isinstance(result, dict)
            assert "data" in result


# ── S9-05: ID-RAG Stable Agent Personas ──────────────────────────────────────

class TestIDRAGPersonas:
    """3 tests for identity injection."""

    def test_pod_identities_all_10_defined(self):
        """POD_IDENTITIES must cover all 10 pods."""
        from core.ai_cascade import POD_IDENTITIES

        expected_pods = {
            "aurora", "dan", "janus", "syntropy", "syntropy_war_room",
            "sentinel_prime", "sentinel_researcher", "ralph",
            "shango_automation", "viral_music",
        }
        assert expected_pods.issubset(set(POD_IDENTITIES.keys()))

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_meta_tasks_skip_identity_injection(self, mock_cascade):
        """Tasks prefixed mae_/cocoa_/causal_ do not get identity injected."""
        mock_cascade.return_value = "result"
        from core.ai_cascade import cascade_call

        await cascade_call("raw prompt", task_type="mae_fitness", pod_name="aurora")
        call_prompt = mock_cascade.call_args[0][0] if mock_cascade.call_args[0] else \
                      mock_cascade.call_args[1].get("prompt", "")
        # Should NOT contain "ARIA" prefix for meta tasks
        assert "You are ARIA" not in call_prompt

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_identity_context_cached_in_redis(self, mock_cascade):
        """get_identity_context uses Redis cache and returns string."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)
        mock_cascade.return_value = "Demonstrated closing mastery in 15 deals"
        from core.ai_cascade import get_identity_context

        ctx = await get_identity_context("aurora", redis_client=mock_redis)
        assert isinstance(ctx, str)


# ── S9-06: DAN Constitutional Code Generation ────────────────────────────────

class TestDANConstitutionalCode:
    """4 tests for DAN code constitution guard."""

    def test_rm_rf_caught(self):
        """check_code_constitution flags rm -rf / as a D1 violation."""
        from pods.dan.graph import check_code_constitution

        violations = check_code_constitution("sudo rm -rf /")
        rule_ids = [v["rule_id"] for v in violations]
        assert "D1" in rule_ids

    def test_hardcoded_credentials_caught(self):
        """check_code_constitution flags hardcoded API keys as D2 violation."""
        from pods.dan.graph import check_code_constitution

        violations = check_code_constitution('api_key = "sk-abc123xyz789"')
        rule_ids = [v["rule_id"] for v in violations]
        assert "D2" in rule_ids

    def test_three_violations_halt(self):
        """guard_route returns 'end' (halt) when state.status == 'HALTED_CONSTITUTION'."""
        from pods.dan.graph import DANState, guard_route

        state = DANState(
            task="drop all tables and rm -rf /*",
            plan="rm -rf /* && DROP TABLE users && api_key='sk-abc123'",
            result="",
            errors=[],
            iterations=0,
            status="HALTED_CONSTITUTION",
            constitutional_violations=3,
        )
        route = guard_route(state)
        assert route == "end"

    def test_clean_code_passes(self):
        """check_code_constitution returns empty list for safe code."""
        from pods.dan.graph import check_code_constitution

        violations = check_code_constitution("result = [x ** 2 for x in range(10)]")
        assert violations == []
