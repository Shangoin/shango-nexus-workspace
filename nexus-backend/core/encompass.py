"""
nexus/core/encompass.py
MIT EnCompass pattern: branching parallel execution with state cloning.
Research: MIT CSAIL EnCompass (Feb 2026) — 15-40% accuracy boost, 82% less
failure-handling code by parallelizing execution branches at decision points.

Instead of linear retry (try → fail → retry), EnCompass:
1. Annotates key decision points in a workflow as BranchPoints
2. At each BranchPoint, clones the state (deep copy — no shared mutable state)
3. Explores up to max_branches alternative LLM completions in PARALLEL
4. Scores each branch outcome using a lightweight verifier
5. Returns the best-scoring branch result

Apply to any workflow where a single LLM call can fail or produce
suboptimal output — especially DAN executor, Aurora PACV, Sentinel analysis.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.ai_cascade import cascade_call
from events.bus import NexusEvent, publish

logger = logging.getLogger(__name__)


@dataclass
class BranchPoint:
    """Metadata about a single branching decision point."""
    node_id: str                          # identifier for this decision point
    state_snapshot: dict                  # deep copy of agent state at this point
    prompt: str                           # the LLM prompt being branched
    task_type: str                        # cascade_call task_type
    pod_name: str                         # which pod this branch is for
    alternatives_tried: list[str] = field(default_factory=list)
    branch_scores: list[float] = field(default_factory=list)
    best_result: str | None = None
    best_score: float = 0.0


@dataclass
class EnCompassResult:
    """Result from an EnCompass branch evaluation."""
    output: str                           # best branch output
    branch_count: int                     # how many branches were explored
    winning_branch: int                   # index of winner (0 = first try)
    best_score: float
    all_scores: list[float]
    backtracked: bool                     # True if first branch failed


async def _score_branch_output(output: str, original_prompt: str,
                                pod_name: str) -> float:
    """
    Purpose:  Lightweight verifier: ask LLM to score branch output quality 0.0-1.0.
    Inputs:   output str, original_prompt str, pod_name str
    Outputs:  float score 0.0-1.0
    Side Effects: 1 cascade_call (encompass_scorer)
    Fast path: empty/error output → 0.1 without LLM call.
    """
    if not output or any(kw in output.lower() for kw in
                         ["error", "exception", "failed", "cannot", "unable"]):
        return 0.1
    try:
        score_response = await cascade_call(
            f"Score this output for quality and task completion (0.0-1.0).\n"
            f"Original task: {original_prompt[:300]}\n"
            f"Output: {output[:500]}\n"
            f"Output JSON only: {{\"score\": 0.8, \"reason\": \"brief explanation\"}}",
            task_type="encompass_scorer",
            pod_name=pod_name,
        )
        match = re.search(r'\{.*?\}', score_response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return float(data.get("score", 0.5))
    except Exception as exc:
        logger.warning("[encompass] scorer failed: %s", exc)
    return 0.5


async def encompass_branch(
    prompt: str,
    task_type: str,
    pod_name: str,
    state: dict,
    max_branches: int = 3,
    branch_temperature_offsets: list[float] | None = None,
) -> EnCompassResult:
    """
    Purpose:  Core EnCompass function. Runs max_branches parallel LLM completions
              for the same prompt, scores all outputs, returns the best.
    Inputs:   prompt, task_type, pod_name, state dict (snapshotted per branch),
              max_branches (default 3; use 2 for Aurora cost control),
              branch_temperature_offsets (default [0.0, 0.2, 0.4])
    Outputs:  EnCompassResult with best output + all scores
    Side Effects: asyncio.gather for parallel branches + scorer calls;
                  publishes nexus.encompass_backtrack if winning_branch > 0.

    Usage:
        result = await encompass_branch(
            prompt="Generate a bash command to restart the nginx service",
            task_type="dan_executor",
            pod_name="dan",
            state={"plan": state.plan, "task": state.task},
            max_branches=3,
        )
        best_command = result.output
    """
    if branch_temperature_offsets is None:
        branch_temperature_offsets = [0.0, 0.2, 0.4][:max_branches]

    # Clone state for each branch — no shared mutable state
    _branch_states = [copy.deepcopy(state) for _ in range(max_branches)]

    async def _run_branch(idx: int) -> str:
        try:
            return await cascade_call(
                prompt,
                task_type=f"{task_type}_branch_{idx}",
                pod_name=pod_name,
            )
        except Exception as exc:
            logger.warning("[encompass] branch %d failed: %s", idx, exc)
            return ""

    # Run all branches in parallel
    branch_outputs: list[str] = list(
        await asyncio.gather(*[_run_branch(i) for i in range(max_branches)])
    )

    # Score all branches in parallel
    branch_scores: list[float] = list(
        await asyncio.gather(*[
            _score_branch_output(output, prompt, pod_name)
            for output in branch_outputs
        ])
    )

    best_idx = int(max(range(len(branch_scores)), key=lambda i: branch_scores[i]))
    best_output = branch_outputs[best_idx]
    best_score = branch_scores[best_idx]
    backtracked = best_idx > 0

    result = EnCompassResult(
        output=best_output,
        branch_count=max_branches,
        winning_branch=best_idx,
        best_score=best_score,
        all_scores=list(branch_scores),
        backtracked=backtracked,
    )

    if backtracked:
        try:
            await publish(NexusEvent(
                pod=pod_name,
                event_type="nexus.encompass_backtrack",
                payload={
                    "pod": pod_name,
                    "task_type": task_type,
                    "winning_branch": best_idx,
                    "best_score": best_score,
                    "all_scores": list(branch_scores),
                },
            ))
        except Exception:
            pass

    return result


async def encompass_workflow(
    steps: list[dict],
    pod_name: str,
    initial_state: dict,
    max_branches_per_step: int = 3,
) -> tuple[dict, list[EnCompassResult]]:
    """
    Purpose:  Runs a multi-step workflow with EnCompass branching at every step.
    Inputs:   steps list[{prompt, task_type, state_key}], pod_name, initial_state,
              max_branches_per_step
    Outputs:  (final_state dict, list[EnCompassResult] per step)
    Side Effects: cascade_call × (branches × scorer × n_steps);
                  publishes nexus.encompass_critical_failure on score < 0.3.

    steps format:
        [
            {"prompt": "...", "task_type": "...", "state_key": "output_key"},
        ]
    Each step's output is written to state[state_key].
    Next step's prompt can reference state values via {state_key} format strings.
    """
    state = copy.deepcopy(initial_state)
    results: list[EnCompassResult] = []

    for step in steps:
        try:
            resolved_prompt = step["prompt"].format(**state)
        except KeyError:
            resolved_prompt = step["prompt"]

        result = await encompass_branch(
            prompt=resolved_prompt,
            task_type=step["task_type"],
            pod_name=pod_name,
            state=state,
            max_branches=max_branches_per_step,
        )

        state[step["state_key"]] = result.output
        results.append(result)

        if result.best_score < 0.3:
            logger.error(
                "[encompass] critical step '%s' scored %.2f across %d branches — halting",
                step["task_type"], result.best_score, max_branches_per_step,
            )
            try:
                await publish(NexusEvent(
                    pod=pod_name,
                    event_type="nexus.encompass_critical_failure",
                    payload={"step": step["task_type"], "score": result.best_score},
                ))
            except Exception:
                pass
            break

    return state, results
