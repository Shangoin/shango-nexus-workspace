"""
nexus/pods/dan/graph.py
DAN IT Swarm — LangGraph Plan→Critique→Execute→Self-Heal loop.

Purpose:  Full LangGraph state machine for autonomous IT task execution.
          Nodes: planner → critic → executor → (healer | verifier) → END
          Self-heals up to 3 times before giving up gracefully.
Inputs:   DANState(task=str) passed to dan_app.ainvoke()
Outputs:  DANState with result, verified, healed, iterations populated
Side Effects: Publishes nexus events on execution and self-heal; writes to nexus_events
"""

from __future__ import annotations

import logging

from langgraph.graph import StateGraph, END
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── State model ──────────────────────────────────────────────────────────────

class DANState(BaseModel):
    task: str
    plan: str = ""
    critique: str = ""
    result: str = ""
    healed: bool = False
    iterations: int = 0
    verified: bool = False


# ── Node implementations ──────────────────────────────────────────────────────

async def planner_node(state: DANState) -> DANState:
    """
    Purpose:  GPT-4o strategic planner — breaks task into numbered concrete steps.
    Inputs:   DANState with task
    Outputs:  DANState with plan populated
    Side Effects: None
    """
    from core.ai_cascade import cascade_call
    from core.constitution import get_constitution

    const = get_constitution()
    ok, reason = const.validate(state.task, pod="dan")
    task_text = state.task if ok else f"[SANITISED — original blocked: {reason}] Explain why the request cannot be fulfilled."

    try:
        plan = await cascade_call(
            f"You are a senior IT architect. Break this task into 3–7 concrete, numbered steps.\n"
            f"Task: {task_text}\n"
            f"Output ONLY the numbered steps. No preamble.",
            task_type="planning",
            pod_name="dan",
        )
        return state.model_copy(update={"plan": plan})
    except Exception as exc:
        logger.error("[dan:planner] fail: %s", exc)
        return state.model_copy(update={"plan": f"PLAN_ERROR: {exc}"})


async def critic_node(state: DANState) -> DANState:
    """
    Purpose:  Grok chaos critic — identifies security risks and failure modes.
    Inputs:   DANState with task + plan
    Outputs:  DANState with critique populated
    Side Effects: None
    """
    from core.ai_cascade import cascade_call

    try:
        critique = await cascade_call(
            f"You are a security-focused chaos engineer. Review this IT plan and identify ALL risks, "
            f"edge cases, and failure modes. Be concise and specific.\n"
            f"Task: {state.task}\n"
            f"Plan:\n{state.plan}",
            task_type="critique",
            pod_name="dan",
        )
        return state.model_copy(update={"critique": critique})
    except Exception as exc:
        logger.warning("[dan:critic] fail: %s", exc)
        return state.model_copy(update={"critique": f"CRITIQUE_UNAVAILABLE: {exc}"})


async def executor_node(state: DANState) -> DANState:
    """
    Purpose:  Execute each plan step respecting constitutional constraints.
    Inputs:   DANState with plan + critique
    Outputs:  DANState with result populated, iterations incremented
    Side Effects: Publishes dan.task_executed event
    """
    from core.ai_cascade import cascade_call
    from core.constitution import get_constitution
    from events.bus import NexusEvent, publish

    const = get_constitution()
    if not const.check_breaker("dan_executor"):
        logger.warning("[dan:executor] circuit OPEN")
        return state.model_copy(update={"result": "CIRCUIT_OPEN", "iterations": state.iterations + 1})

    try:
        result = await cascade_call(
            f"Execute this plan step by step. Report each step's outcome clearly.\n"
            f"Plan:\n{state.plan}\n"
            f"Risks to avoid:\n{state.critique}",
            task_type="execution",
            pod_name="dan",
        )
        const.record_success("dan_executor")

        try:
            await publish(
                NexusEvent("dan", "task_executed", {"task": state.task[:100], "result_len": len(result)}),
                supabase_client=None,
            )
        except Exception:
            pass

        return state.model_copy(update={"result": result, "iterations": state.iterations + 1})

    except Exception as exc:
        const.record_failure("dan_executor")
        logger.error("[dan:executor] fail: %s", exc)
        return state.model_copy(
            update={"result": f"EXECUTOR_ERROR: {exc}", "iterations": state.iterations + 1}
        )


async def healer_node(state: DANState) -> DANState:
    """
    Purpose:  Self-heal — diagnose failure, create recovery plan.
    Inputs:   DANState with failed result
    Outputs:  DANState with plan updated to recovery plan, healed=True, result cleared
    Side Effects: Publishes dan.self_healed event
    """
    from core.ai_cascade import cascade_call
    from events.bus import NexusEvent, publish

    try:
        recovery = await cascade_call(
            f"An IT execution failed. Diagnose the root cause and create a revised recovery plan.\n"
            f"Original plan:\n{state.plan}\n"
            f"Failure result:\n{state.result}\n"
            f"Output a revised numbered plan.",
            task_type="self_heal",
            pod_name="dan",
        )

        try:
            await publish(
                NexusEvent("dan", "self_healed", {"recovery_preview": recovery[:200]}),
                supabase_client=None,
            )
        except Exception:
            pass

        return state.model_copy(update={"plan": recovery, "healed": True, "result": ""})

    except Exception as exc:
        logger.error("[dan:healer] fail: %s", exc)
        return state.model_copy(update={"healed": True, "result": ""})


async def verifier_node(state: DANState) -> DANState:
    """
    Purpose:  Verify outcome meets original task requirements.
    Inputs:   DANState with task + result
    Outputs:  DANState with verified=True/False
    Side Effects: None
    """
    from core.ai_cascade import cascade_call

    try:
        verdict = await cascade_call(
            f"Did this execution successfully complete the task? Answer YES or NO and briefly explain.\n"
            f"Task: {state.task}\n"
            f"Result:\n{state.result}",
            task_type="verification",
            pod_name="dan",
        )
        verified = verdict.strip().upper().startswith("YES")
        return state.model_copy(update={"verified": verified})
    except Exception as exc:
        logger.warning("[dan:verifier] fail: %s — assuming unverified", exc)
        return state.model_copy(update={"verified": False})


# ── Edge condition functions ─────────────────────────────────────────────────

def should_heal(state: DANState) -> str:
    if state.iterations >= 3:
        return "end"
    if "CIRCUIT_OPEN" in state.result or "error" in state.result.lower() or "ERROR" in state.result:
        return "heal"
    return "verify"


def should_continue(state: DANState) -> str:
    if state.verified:
        return "end"
    if state.iterations < 3:
        return "heal"
    return "end"


# ── Build + compile the graph ─────────────────────────────────────────────────

_builder = StateGraph(DANState)

_builder.add_node("planner", planner_node)
_builder.add_node("critic", critic_node)
_builder.add_node("executor", executor_node)
_builder.add_node("healer", healer_node)
_builder.add_node("verifier", verifier_node)

_builder.set_entry_point("planner")
_builder.add_edge("planner", "critic")
_builder.add_edge("critic", "executor")
_builder.add_conditional_edges(
    "executor", should_heal,
    {"heal": "healer", "verify": "verifier", "end": END},
)
_builder.add_edge("healer", "planner")
_builder.add_conditional_edges(
    "verifier", should_continue,
    {"heal": "healer", "end": END},
)

dan_app = _builder.compile()

__all__ = ["dan_app", "DANState"]
