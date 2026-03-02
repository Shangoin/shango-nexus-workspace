"""
nexus/core/mem1_state.py
MEM1: Memory-Reasoning Unified Internal State
Research: MEM1 (arXiv:2506.15841) — consolidated internal state beats
separate retrieve→reason→act on long-horizon tasks with constant memory usage.

Instead of 3 sequential LLM calls (retrieve → reason → act),
MEM1 does all three in ONE call by maintaining a structured
internal state <IS> that is updated every turn.

The agent produces <IS_t> at each turn:
- Summarizes what matters from new input
- Retains only still-relevant prior state
- Reasons about next action
- Outputs the action

This produces constant memory usage (only last <IS> retained)
while outperforming ReAct on long-horizon tasks.

Apply to:
  aurora/brain.py    — turn-by-turn call context (replaces brain1+brain2 sequential calls)
  dan/graph.py       — multi-step execution state (replaces plan→execute→verify)
  sentinel_researcher — research pipeline (accumulates findings across sources)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from core.ai_cascade import cascade_call
from core.memory import remember

logger = logging.getLogger(__name__)

# XML-style state tags
MEM1_IS_OPEN = "<IS>"
MEM1_IS_CLOSE = "</IS>"
MEM1_IS_NEW_OPEN = "<IS_new>"
MEM1_IS_NEW_CLOSE = "</IS_new>"
MEM1_ACTION_OPEN = "<action>"
MEM1_ACTION_CLOSE = "</action>"

# Redis/memory TTL for internal state (1 hour — active session window)
MEM1_STATE_TTL_SECONDS = 3600


@dataclass
class MEM1State:
    """Persistent internal state for a single agent session."""
    pod: str
    session_id: str
    internal_state: str = ""          # current <IS> — replaced each turn
    turn_count: int = 0
    action_history: list[str] = field(default_factory=list)


def _extract_tag(text: str, open_tag: str, close_tag: str) -> str:
    """Extract content between XML-style tags. Returns empty string if not found."""
    pattern = re.escape(open_tag) + r"(.*?)" + re.escape(close_tag)
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _build_mem1_prompt(query: str, prior_is: str, pod: str,
                        pod_role: str = "") -> str:
    """
    Constructs the MEM1 unified prompt that updates memory AND reasons AND acts
    in a single LLM call.
    """
    prior_block = f"{MEM1_IS_OPEN}\n{prior_is}\n{MEM1_IS_CLOSE}\n\n" if prior_is else ""
    role_block = f"Your role: {pod_role}\n\n" if pod_role else ""

    return (
        f"{role_block}"
        f"Prior internal state:\n{prior_block}"
        f"New input: {query}\n\n"
        f"Instructions (follow exactly):\n"
        f"1. UPDATE your internal state: extract what matters from the new input. "
        f"Keep only still-relevant prior context. Discard what is no longer needed. "
        f"Be concise — maximum 200 words.\n"
        f"2. REASON about the best next action given the updated state.\n"
        f"3. OUTPUT in this exact format:\n"
        f"{MEM1_IS_NEW_OPEN}your updated internal state{MEM1_IS_NEW_CLOSE}\n"
        f"{MEM1_ACTION_OPEN}your response or next action{MEM1_ACTION_CLOSE}"
    )


async def mem1_step(
    query: str,
    pod: str,
    session_id: str,
    prior_state: Optional[MEM1State] = None,
    pod_role: str = "",
) -> tuple[str, MEM1State]:
    """
    Purpose:  Single MEM1 reasoning step — update IS + reason + act in one call.
    Inputs:   query str, pod str, session_id str, prior_state (optional),
              pod_role str for identity anchoring
    Outputs:  (action_output: str, updated_MEM1State)
    Side Effects: 1 cascade_call (mem1_unified_step); remembers new IS to memory.

    Usage in Aurora brain.py:
        action, state = await mem1_step(
            query=f"Lead: {lead_data}. Generate strategic call brief.",
            pod="aurora",
            session_id=lead_id,
            pod_role="Elite AI Sales Representative for Shango India",
        )
        vapi_system_prompt = action

    Usage in DAN graph.py:
        action, state = await mem1_step(
            query=f"Execute plan step: {current_step}. Prior results: {results}",
            pod="dan",
            session_id=job_id,
            prior_state=dan_session_state,
            pod_role="Autonomous IT Problem Solver",
        )
    """
    prior_is = prior_state.internal_state if prior_state else ""

    prompt = _build_mem1_prompt(query, prior_is, pod, pod_role)

    try:
        response = await cascade_call(
            prompt,
            task_type="mem1_unified_step",
            pod_name=pod,
        )
    except Exception as exc:
        logger.warning("[mem1] step failed for %s/%s: %s", pod, session_id, exc)
        # Fail-open: return query as action, preserve prior state
        return query, prior_state or MEM1State(pod=pod, session_id=session_id)

    # Extract new internal state and action from tagged response
    new_is = _extract_tag(response, MEM1_IS_NEW_OPEN, MEM1_IS_NEW_CLOSE)
    action = _extract_tag(response, MEM1_ACTION_OPEN, MEM1_ACTION_CLOSE)

    # Fallback: if XML tags not found, use full response as action
    if not action:
        action = response
    if not new_is:
        new_is = prior_is  # preserve prior state if update parsing failed

    # Build updated state
    turn = (prior_state.turn_count + 1) if prior_state else 1
    history = list(prior_state.action_history) if prior_state else []
    history.append(action[:100])
    if len(history) > 20:
        history = history[-20:]

    updated_state = MEM1State(
        pod=pod,
        session_id=session_id,
        internal_state=new_is,
        turn_count=turn,
        action_history=history,
    )

    # Persist new internal state to memory (procedural — no decay, survives 1 hour)
    try:
        await remember(
            content=new_is,
            pod=pod,
            memory_type="procedural",
            metadata={"session_id": session_id, "turn": turn,
                      "key": f"mem1_state:{session_id}"},
        )
    except Exception as exc:
        logger.debug("[mem1] state persist non-critical fail: %s", exc)

    return action, updated_state


async def mem1_multi_turn(
    queries: list[str],
    pod: str,
    session_id: str,
    pod_role: str = "",
) -> list[tuple[str, MEM1State]]:
    """
    Purpose:  Convenience function — runs multiple MEM1 steps sequentially,
              threading internal state automatically across all queries.
    Inputs:   queries list[str], pod str, session_id str, pod_role str
    Outputs:  list of (action: str, MEM1State) tuples, one per query
    Side Effects: N cascade_calls (one per query); N memory writes.

    Usage for Sentinel Researcher pipeline:
        results = await mem1_multi_turn(
            queries=[
                f"Fetch and summarize: {source1}",
                f"Cross-reference with: {source2}",
                "Generate actionable insights from all sources",
            ],
            pod="sentinel_researcher",
            session_id=research_session_id,
            pod_role="Research Intelligence Agent",
        )
        final_insights, _ = results[-1]
    """
    results: list[tuple[str, MEM1State]] = []
    state: Optional[MEM1State] = None
    for query in queries:
        action, state = await mem1_step(
            query=query,
            pod=pod,
            session_id=session_id,
            prior_state=state,
            pod_role=pod_role,
        )
        results.append((action, state))
    return results
