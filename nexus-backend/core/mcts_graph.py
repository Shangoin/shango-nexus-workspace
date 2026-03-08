"""
nexus/core/mcts_graph.py
LangGraph-based PACV (Plan-Act-Critique-Verify) kernel with MCTS planning.
Used by all pods for structured multi-step reasoning.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── MCTS node ────────────────────────────────────────────────────────────────

@dataclass
class MCTSNode:
    action: str
    parent: Optional["MCTSNode"] = None
    children: list["MCTSNode"] = field(default_factory=list)
    visits: int = 0
    value: float = 0.0
    compute_cost: float = 1.0   # estimated cost (tokens / time)

    @property
    def ucb1(self) -> float:
        if self.visits == 0:
            return float("inf")
        c = 1.41421  # sqrt(2)
        parent_visits = self.parent.visits if self.parent else self.visits
        return (self.value / self.visits) + c * math.sqrt(math.log(parent_visits) / self.visits)

    @property
    def reward_per_cost(self) -> float:
        if self.compute_cost <= 0:
            return 0.0
        return (self.value / max(self.visits, 1)) / self.compute_cost


async def mcts_plan(
    goal: str,
    possible_actions: list[str],
    simulation_fn,  # async fn(action: str) -> float
    budget: int = 50,
) -> list[MCTSNode]:
    """
    Run MCTS over possible_actions to find best action sequence.
    simulation_fn evaluates an action string and returns a reward [0, 1].
    Returns sorted list of MCTSNodes by reward_per_cost.
    """
    root = MCTSNode(action="root")

    for action in possible_actions:
        child = MCTSNode(action=action, parent=root)
        root.children.append(child)

    for _ in range(budget):
        # Selection: pick unvisited or highest UCB1
        candidates = [c for c in root.children if c.visits == 0]
        if not candidates:
            candidates = root.children
        node = max(candidates, key=lambda n: n.ucb1)

        # Simulation
        try:
            reward = await simulation_fn(node.action)
            node.visits += 1
            node.value += reward
            root.visits += 1
        except Exception as exc:
            logger.warning("[mcts] sim fail action=%s: %s", node.action, exc)
            node.visits += 1  # still count visit

    sorted_nodes = sorted(root.children, key=lambda n: n.reward_per_cost, reverse=True)
    logger.info("[mcts] done budget=%d top=%s score=%.3f", budget, sorted_nodes[0].action if sorted_nodes else "none", sorted_nodes[0].reward_per_cost if sorted_nodes else 0)
    return sorted_nodes


# ── PACV Kernel (LangGraph-style state machine) ──────────────────────────────

@dataclass
class PACVState:
    goal: str
    plan: str = ""
    action_taken: str = ""
    critique: str = ""
    verified: bool = False
    result: str = ""
    iterations: int = 0
    max_iterations: int = 3


async def pacv_loop(
    goal: str,
    ai_fn,  # async fn(prompt: str) -> str
    max_iterations: int = 3,
) -> PACVState:
    """
    Plan → Act → Critique → Verify loop.
    ai_fn is usually cascade_call from ai_cascade.py.
    """
    state = PACVState(goal=goal, max_iterations=max_iterations)

    while state.iterations < state.max_iterations and not state.verified:
        state.iterations += 1
        logger.debug("[pacv] iter=%d goal=%s", state.iterations, goal[:60])

        # PLAN
        plan_prompt = f"""You are a precise planning assistant.
Goal: {goal}
Previous attempt: {state.action_taken or 'none'}
Previous critique: {state.critique or 'none'}
Output a concise step-by-step plan (max 5 steps)."""
        state.plan = await ai_fn(plan_prompt)

        # ACT
        act_prompt = f"""Execute this plan and produce the result.
Goal: {goal}
Plan: {state.plan}
Produce the final output directly."""
        state.action_taken = await ai_fn(act_prompt)

        # CRITIQUE
        critique_prompt = f"""Critique this output against the original goal.
Goal: {goal}
Output: {state.action_taken}
Identify specific gaps, errors, or improvements needed. Be brief."""
        state.critique = await ai_fn(critique_prompt)

        # VERIFY
        verify_prompt = f"""Is this output satisfactory for the goal? Answer only YES or NO.
Goal: {goal}
Output: {state.action_taken}
Critique: {state.critique}"""
        verdict = await ai_fn(verify_prompt)
        state.verified = verdict.strip().upper().startswith("YES")

    state.result = state.action_taken
    logger.info("[pacv] done iterations=%d verified=%s", state.iterations, state.verified)
    return state


# ── ARC Workflow Selector (S11-02) ────────────────────────────────────────────
# "Learning to Configure Agentic AI Systems" — ArXiv 2602.11574
#
# ARC adds a hierarchical layer *above* MCTS:
#   High-level policy:  which pod + workflow to invoke for a given signal
#   Low-level policy:   what to do within that workflow (handled by mcts_plan/pacv)
#
# Implementation:
#   1. Given a signal and a list of WorkflowOptions, ask the LLM to score each.
#   2. Parse scores, sort, return top_k options.
#   3. Caller then passes the winning workflow into mcts_plan / pacv_loop.
#
# This replaces the previous behaviour where MCTS only searched *within* a single
# workflow — now the system first selects *which* workflow to run.


@dataclass
class WorkflowOption:
    """
    Describes one pod+workflow combination that the ARC selector can choose.
    cost: relative compute cost (1.0 = normal, 2.0 = expensive, 0.5 = cheap).
    """
    name: str          # unique identifier, e.g. "aurora.lead_score"
    pod: str           # pod that owns this workflow
    description: str   # plain-language description of what it does
    cost: float = 1.0  # relative compute cost

    @property
    def arc_spec(self) -> str:
        """One-line spec used in the LLM scoring prompt."""
        return f"{self.name} (pod={self.pod}, cost={self.cost:.1f}): {self.description}"


async def arc_select_workflow(
    signal: str,
    workflows: list[WorkflowOption],
    ai_fn,               # async fn(prompt: str) -> str  (usually cascade_call)
    top_k: int = 1,
) -> list[WorkflowOption]:
    """
    S11-02: ARC hierarchical policy — select the best pod+workflow for a signal.

    Purpose:  Given an incoming signal and a list of available workflows, score
              each option using the LLM and return the top_k best matches.
              This is the "high-level policy" layer in ARC — it sits above MCTS.
    Inputs:
              signal     str  — the incoming signal / task description
              workflows  list[WorkflowOption] — candidates to choose from
              ai_fn      async fn(str) -> str — LLM scoring function
              top_k      int — number of top workflows to return (default 1)
    Outputs:  list[WorkflowOption] sorted by ARC score descending (length = top_k)
    Side Effects: 1 LLM call via ai_fn
    Errors:   Returns workflows[0:top_k] unranked on any LLM/parse failure.
    """
    if not workflows:
        return []

    if len(workflows) == 1:
        return workflows[:top_k]

    # Build scoring prompt
    options_block = "\n".join(
        f"  [{i}] {w.arc_spec}" for i, w in enumerate(workflows)
    )
    scoring_prompt = (
        f"You are an ARC Workflow Selector — a high-level routing policy.\n"
        f"Signal: {signal}\n\n"
        f"Available workflows:\n{options_block}\n\n"
        f"Score each workflow for fit to the signal on a scale 0.0–1.0.\n"
        f"Consider: relevance, cost-efficiency, and whether the task is "
        f"sequential (prefer single-agent) or parallelisable (prefer multi-pod).\n"
        f"Output ONLY a JSON array of scores in the same index order, e.g. "
        f"[0.9, 0.3, 0.7].  No explanation."
    )

    try:
        raw = await ai_fn(scoring_prompt)
        raw = raw.strip()

        # Parse JSON array from response
        import json
        import re as _re
        match = _re.search(r'\[[\d.,\s]+\]', raw)
        if not match:
            raise ValueError("No score array found in response")
        scores: list[float] = json.loads(match.group())

        if len(scores) != len(workflows):
            raise ValueError(
                f"Score count mismatch: got {len(scores)}, expected {len(workflows)}"
            )

        # Cost-adjust: divide raw score by cost to get efficiency score
        efficiency = [s / max(w.cost, 0.1) for s, w in zip(scores, workflows)]
        ranked = sorted(
            zip(efficiency, workflows), key=lambda x: x[0], reverse=True
        )

        selected = [w for _, w in ranked[:top_k]]
        logger.info(
            "[arc] signal='%s...' top=%s score=%.2f",
            signal[:50], selected[0].name, ranked[0][0] if ranked else 0,
        )
        return selected

    except Exception as exc:
        logger.warning("[arc] workflow selection failed, returning default: %s", exc)
        return workflows[:top_k]
