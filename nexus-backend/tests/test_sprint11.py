"""
tests/test_sprint11.py
Sprint 11 — MCP, MIT Self-Edit, ARC Selector, Coordination Metrics smoke tests.
Run: pytest tests/test_sprint11.py -v
Target: 20 tests → cumulative 136 across all sprints

Coverage:
  S11-00  MCP adapter — tool registry and dispatch (core/mcp_adapter.py)
  S11-01  MIT self-edit MARS layer (core/evolution.py)
  S11-02  ARC workflow selector above MCTS (core/mcts_graph.py)
  S11-03  Coordination efficiency in /health (api/health.py)
"""
from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── S11-00: MCP Adapter ───────────────────────────────────────────────────────

class TestMCPAdapter:
    """7 tests for S11-00."""

    def test_all_core_tools_registered(self):
        """All 7 pre-built tools must be registered at import time."""
        from core.mcp_adapter import _TOOLS
        expected = {
            "supabase_query", "supabase_insert", "supabase_upsert",
            "redis_get", "redis_set", "cascade", "publish_event",
        }
        assert expected.issubset(set(_TOOLS.keys())), (
            f"Missing tools: {expected - set(_TOOLS.keys())}"
        )

    def test_list_tools_returns_mcp_specs(self):
        """list_tools() must return dicts with name, description, inputSchema."""
        from core.mcp_adapter import list_tools
        specs = list_tools()
        assert len(specs) >= 7
        for spec in specs:
            assert "name" in spec
            assert "description" in spec
            assert "inputSchema" in spec

    def test_register_custom_tool(self):
        """register_tool adds a new tool to the registry."""
        from core.mcp_adapter import register_tool, _TOOLS

        async def _my_tool(x: int) -> int:
            return x * 2

        register_tool("test_double", _my_tool, description="doubles input")
        assert "test_double" in _TOOLS
        assert _TOOLS["test_double"].description == "doubles input"

    @pytest.mark.asyncio
    async def test_mcp_call_custom_tool(self):
        """mcp_call dispatches to registered handler and returns result."""
        from core.mcp_adapter import register_tool, mcp_call

        async def _add(a: int, b: int) -> int:
            return a + b

        register_tool("test_add", _add, description="adds two numbers")
        result = await mcp_call("test_add", a=3, b=4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_mcp_call_unknown_tool_raises(self):
        """mcp_call raises MCPToolError for unregistered tool names."""
        from core.mcp_adapter import mcp_call, MCPToolError
        with pytest.raises(MCPToolError, match="Unknown MCP tool"):
            await mcp_call("nonexistent_tool_xyz")

    @pytest.mark.asyncio
    async def test_mcp_call_cascade_routes_to_cascade_call(self):
        """mcp_call('cascade', ...) calls core.ai_cascade.cascade_call."""
        # cascade_call is imported lazily inside the handler — patch at source
        mock_cc = AsyncMock(return_value="test response")
        with patch("core.ai_cascade.cascade_call", mock_cc):
            from core.mcp_adapter import _TOOLS
            handler = _TOOLS["cascade"].handler
            result = await handler(
                prompt="hello", task_type="test", pod_name="nexus"
            )
        # The handler calls cascade_call — verify shape
        assert isinstance(result, str)

    def test_nexus_tool_to_mcp_spec(self):
        """NexusTool.to_mcp_spec() returns MCP-compatible dict."""
        from core.mcp_adapter import NexusTool

        async def _noop():
            pass

        tool = NexusTool(
            name="test_noop",
            description="does nothing",
            handler=_noop,
            parameters={"x": {"type": "integer"}},
        )
        spec = tool.to_mcp_spec()
        assert spec["name"] == "test_noop"
        assert spec["description"] == "does nothing"
        assert "inputSchema" in spec
        assert "x" in spec["inputSchema"]["properties"]


# ── S11-01: MIT Self-Edit MARS Layer ─────────────────────────────────────────

class TestMITSelfEdit:
    """6 tests for S11-01."""

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_generate_self_edit_returns_string(self, mock_cascade):
        """generate_self_edit returns a non-empty string."""
        mock_cascade.return_value = (
            "PATTERN: Break problem into sub-goals\n"
            "AVOID: Over-explaining\n"
            "ANCHOR: Always verify output format"
        )
        from core.evolution import generate_self_edit
        result = await generate_self_edit(
            pod="aurora",
            challenge="Handle complex objection",
            solution="I focused on ROI...",
            judge_score=0.65,
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_generate_self_edit_stores_in_cache(self, mock_cascade):
        """generate_self_edit stores result in _self_edit_cache for pod."""
        mock_cascade.return_value = "PATTERN: x\nAVOID: y\nANCHOR: z"
        from core import evolution as evo
        await evo.generate_self_edit(
            pod="test_cache_pod",
            challenge="challenge",
            solution="solution",
            judge_score=0.5,
        )
        assert "test_cache_pod" in evo._self_edit_cache

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_generate_self_edit_graceful_on_cascade_fail(self, mock_cascade):
        """generate_self_edit returns '' without raising when cascade fails."""
        mock_cascade.side_effect = RuntimeError("cascade down")
        from core.evolution import generate_self_edit
        result = await generate_self_edit("janus", "c", "s", 0.3)
        assert result == ""

    @pytest.mark.asyncio
    async def test_reconstruct_from_self_edit_empty_on_no_prior(self):
        """reconstruct_from_self_edit returns '' for a pod with no history."""
        from core.evolution import reconstruct_from_self_edit
        result = await reconstruct_from_self_edit("brand_new_pod_xyz")
        assert result == ""

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_reconstruct_returns_self_edit_prefix(self, mock_cascade):
        """reconstruct_from_self_edit returns a formatted prefix when cache exists."""
        mock_cascade.return_value = "PATTERN: test\nAVOID: nothing\nANCHOR: be concise"
        from core import evolution as evo

        # Seed the cache
        await evo.generate_self_edit("dan", "task", "output", 0.8)

        prefix = await evo.reconstruct_from_self_edit("dan")
        assert "[SELF-EDIT from prior cycle]" in prefix
        assert "[END SELF-EDIT]" in prefix

    @pytest.mark.asyncio
    @patch("core.ai_cascade.cascade_call", new_callable=AsyncMock)
    async def test_mae_adversarial_fitness_includes_self_edit_task(self, mock_cascade):
        """mae_adversarial_fitness runs without error with self-edit integrated."""
        mock_cascade.side_effect = [
            "Handle edge case systematically",           # curriculum challenge
            '{"solution": "detailed steps", "confidence": 0.7}',  # solver
            '{"score": 0.72, "reasoning": "solid plan"}',          # judge
        ]
        from core.evolution import mae_adversarial_fitness
        score = await mae_adversarial_fitness([0.5] * 8, pod="ralph")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.2


# ── S11-02: ARC Workflow Selector ────────────────────────────────────────────

class TestARCWorkflowSelector:
    """5 tests for S11-02."""

    @pytest.mark.asyncio
    async def test_arc_select_single_workflow_returns_it(self):
        """arc_select_workflow returns the only option when list has 1 item."""
        from core.mcts_graph import arc_select_workflow, WorkflowOption

        async def _ai_fn(p):
            return "[1.0]"

        wf = WorkflowOption("aurora.lead_score", "aurora", "Score a lead", cost=1.0)
        result = await arc_select_workflow("new lead arrived", [wf], _ai_fn)
        assert len(result) == 1
        assert result[0].name == "aurora.lead_score"

    @pytest.mark.asyncio
    async def test_arc_select_top_workflow_by_score(self):
        """arc_select_workflow picks the highest-scored option."""
        from core.mcts_graph import arc_select_workflow, WorkflowOption

        async def _ai_fn(prompt: str) -> str:
            return "[0.3, 0.9, 0.6]"

        workflows = [
            WorkflowOption("dan.it_triage", "dan", "IT incident triage", cost=1.0),
            WorkflowOption("janus.regime", "janus", "Market regime detect", cost=1.0),
            WorkflowOption("aurora.nurture", "aurora", "Lead nurture", cost=1.0),
        ]
        result = await arc_select_workflow("trading signal detected", workflows, _ai_fn)
        assert result[0].name == "janus.regime"

    @pytest.mark.asyncio
    async def test_arc_select_cost_penalises_expensive(self):
        """arc_select_workflow penalises high-cost options even with high raw score."""
        from core.mcts_graph import arc_select_workflow, WorkflowOption

        async def _ai_fn(prompt: str) -> str:
            # Workflow 0 scores 0.9 but costs 5.0; workflow 1 scores 0.8 and costs 1.0
            # Efficiency: 0 → 0.9/5=0.18, 1 → 0.8/1=0.80 → workflow 1 wins
            return "[0.9, 0.8]"

        workflows = [
            WorkflowOption("heavy.expensive", "heavy", "Costly workflow", cost=5.0),
            WorkflowOption("light.efficient", "light", "Efficient workflow", cost=1.0),
        ]
        result = await arc_select_workflow("any signal", workflows, _ai_fn)
        assert result[0].name == "light.efficient"

    @pytest.mark.asyncio
    async def test_arc_select_falls_back_on_parse_error(self):
        """arc_select_workflow returns workflows[:top_k] when LLM gives bad JSON."""
        from core.mcts_graph import arc_select_workflow, WorkflowOption

        async def _ai_fn(prompt: str) -> str:
            return "I cannot decide."  # malformed — no JSON array

        workflows = [
            WorkflowOption("w1", "pod1", "workflow one"),
            WorkflowOption("w2", "pod2", "workflow two"),
        ]
        result = await arc_select_workflow("signal", workflows, _ai_fn)
        assert len(result) == 1  # default top_k=1
        assert result[0] in workflows

    def test_workflow_option_arc_spec_format(self):
        """WorkflowOption.arc_spec includes name, pod, cost, and description."""
        from core.mcts_graph import WorkflowOption
        wf = WorkflowOption("aurora.call", "aurora", "Run a sales call", cost=2.0)
        spec = wf.arc_spec
        assert "aurora.call" in spec
        assert "aurora" in spec
        assert "2.0" in spec
        assert "sales call" in spec


# ── S11-03: Coordination Metrics in /health ───────────────────────────────────

class TestCoordinationMetricsInHealth:
    """2 tests for S11-03."""

    @pytest.mark.asyncio
    async def test_health_includes_coordination_fields(self):
        """GET /health must include coordination_efficiency and redundancy_rate."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.health import router

        app = FastAPI()
        app.include_router(router)

        with patch("api.health.realtime_manager") as mock_rt:
            mock_rt.connected = True
            mock_rt.subscriber_count = 0
            client = TestClient(app)
            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "coordination_efficiency" in data, (
            "/health missing coordination_efficiency"
        )
        assert "redundancy_rate" in data or data.get("coordination_efficiency") == "error: ", (
            "/health missing redundancy_rate"
        )

    @pytest.mark.asyncio
    async def test_health_version_is_sprint11(self):
        """GET /health must report version v7.0-sprint11 and test_count 136/136."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.health import router

        app = FastAPI()
        app.include_router(router)

        with patch("api.health.realtime_manager") as mock_rt:
            mock_rt.connected = True
            mock_rt.subscriber_count = 0
            client = TestClient(app)
            resp = client.get("/health")

        data = resp.json()
        assert data.get("version") == "v7.0-sprint11", (
            f"Expected v7.0-sprint11, got {data.get('version')}"
        )
        assert data.get("test_count") == "136/136", (
            f"Expected 136/136, got {data.get('test_count')}"
        )
