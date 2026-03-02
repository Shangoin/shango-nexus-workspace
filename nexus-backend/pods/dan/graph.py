"""nexus/pods/dan/graph.py
DAN IT Swarm — LangGraph Plan→Critique→Execute→(ConstitutionGuard)→Self-Heal loop.

Purpose:  Full LangGraph state machine for autonomous IT task execution.
          Nodes: planner → critic → executor → constitution_guard → (healer | verifier) → END
          Constitutional guard checks generated plans for dangerous patterns before execution.
          Self-heals up to 3 times before giving up gracefully.
Inputs:   DANState(task=str) passed to dan_app.ainvoke()
Outputs:  DANState with result, verified, healed, iterations, constitutional_violations populated
Side Effects: Publishes nexus events on execution and self-heal; writes to nexus_events
"""

from __future__ import annotations

import logging
import re

from langgraph.graph import StateGraph, END
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── S9-06: DAN code constitution (arXiv:2602.02584) ────────────────────────────
DAN_CODE_CONSTITUTION: list[dict] = [
    {"id": "D1", "pattern": r"rm\s+-rf\s+/", "label": "destructive_root_delete"},
    {"id": "D2", "pattern": r"(password|secret|api_key)\s*=\s*['\"][^'\"]{4,}['\"]\s*", "label": "hardcoded_credential"},
    {"id": "D3", "pattern": r"curl\s+http[s]?://(?!localhost|127\.0\.0\.1)", "label": "network_exfiltration"},
    {"id": "D4", "pattern": r"sudo\b(?!.*#\s*REASON:)", "label": "sudo_without_justification"},
    {"id": "D5", "pattern": r"DROP\s+TABLE|TRUNCATE\s+TABLE", "label": "destructive_db_operation"},
]


def check_code_constitution(code_or_command: str) -> list[dict]:
    """
    S9-06: Scan generated code/plan against DAN constitutional rules.
    Purpose:  Prevent dangerous code from executing (synchronous regex check).
    Inputs:   code_or_command str — generated plan or code snippet
    Outputs:  list[dict] of violations: [{rule_id, label, match_snippet}]
    Side Effects: None (pure regex, no LLM)
    """
    violations = []
    for rule in DAN_CODE_CONSTITUTION:
        match = re.search(rule["pattern"], code_or_command, re.IGNORECASE)
        if match:
            violations.append({
                "rule_id": rule["id"],
                "label": rule["label"],
                "match_snippet": match.group()[:80],
            })
    return violations


# ── State model ──────────────────────────────────────────────────────────────

class DANState(BaseModel):
    task: str
    plan: str = ""
    critique: str = ""
    result: str = ""
    status: str = ""  # S9-06: "REPLAN" | "HALTED_CONSTITUTION" | ""
    healed: bool = False
    iterations: int = 0
    verified: bool = False
    constitutional_violations: int = 0  # S9-06: cumulative violation count
    encompass_winning_branch: int = 0   # S10-01: which branch won
    encompass_best_score: float = 0.0   # S10-01: best branch score


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
    from core.ai_cascade import cascade_call  # noqa: F401 — kept for healer/verifier
    from core.encompass import encompass_branch  # S10-01
    from core.constitution import get_constitution
    from events.bus import NexusEvent, publish

    const = get_constitution()
    if not const.check_breaker("dan_executor"):
        logger.warning("[dan:executor] circuit OPEN")
        return state.model_copy(update={"result": "CIRCUIT_OPEN", "iterations": state.iterations + 1})

    executor_prompt = (
        f"Execute this plan step by step. Report each step's outcome clearly.\n"
        f"Plan:\n{state.plan}\n"
        f"Risks to avoid:\n{state.critique}"
    )
    try:
        # S10-01: EnCompass branching — explore 3 execution paths, pick best
        enc_result = await encompass_branch(
            prompt=executor_prompt,
            task_type="dan_executor",
            pod_name="dan",
            state={"plan": state.plan, "task": state.task},
            max_branches=3,
        )
        result = enc_result.output
        const.record_success("dan_executor")

        try:
            await publish(
                NexusEvent("dan", "task_executed", {
                    "task": state.task[:100],
                    "result_len": len(result),
                    "winning_branch": enc_result.winning_branch,
                    "best_score": enc_result.best_score,
                }),
                supabase_client=None,
            )
        except Exception:
            pass

        return state.model_copy(update={
            "result": result,
            "iterations": state.iterations + 1,
            "encompass_winning_branch": enc_result.winning_branch,
            "encompass_best_score": enc_result.best_score,
        })

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
        return state.model_copy(update={"verified": verified, "status": ""})
    except Exception as exc:
        logger.warning("[dan:verifier] fail: %s — assuming unverified", exc)
        return state.model_copy(update={"verified": False, "status": ""})


async def constitution_guard_node(state: DANState) -> DANState:
    """
    S9-06: Security by Construction (arXiv:2602.02584) constitutional guard.
    Runs AFTER executor_node, BEFORE verifier_node.
    Purpose:  Block dangerous plans; route to replanning or halt.
    Inputs:   DANState with plan + result
    Outputs:  DANState with status updated ("REPLAN"|"HALTED_CONSTITUTION"|"")
    Side Effects: Publishes dan.constitutional_halt event on HALT
    """
    from events.bus import NexusEvent, publish

    text_to_check = state.plan + "\n" + state.result
    violations = check_code_constitution(text_to_check)

    if not violations:
        return state.model_copy(update={"status": ""})

    total_violations = state.constitutional_violations + len(violations)

    if total_violations >= 3:
        try:
            await publish(
                NexusEvent("dan", "dan.constitutional_halt",
                           {"violations": violations, "iteration": state.iterations}),
                supabase_client=None,
            )
        except Exception:
            pass
        logger.error("[dan:guard] HALTED after %d violations: %s", total_violations, violations)
        return state.model_copy(update={
            "status": "HALTED_CONSTITUTION",
            "constitutional_violations": total_violations,
        })
    else:
        violation_notes = "\n".join(
            f"- [{v['rule_id']}] {v['label']}: {v['match_snippet']}" for v in violations
        )
        updated_plan = (
            state.plan
            + f"\n\nCONSTITUTION VIOLATIONS DETECTED:\n{violation_notes}\n"
            + "Replanning required. Address each violation explicitly."
        )
        logger.warning("[dan:guard] REPLAN violations=%d", len(violations))
        return state.model_copy(update={
            "plan": updated_plan,
            "status": "REPLAN",
            "constitutional_violations": total_violations,
        })


# ── Edge condition functions ─────────────────────────────────────────────────

def should_heal(state: DANState) -> str:
    if state.iterations >= 3:
        return "end"
    if "CIRCUIT_OPEN" in state.result or "error" in state.result.lower() or "ERROR" in state.result:
        return "heal"
    return "verify"


def guard_route(state: DANState) -> str:
    """S9-06: Route from constitution_guard_node."""
    if state.status == "HALTED_CONSTITUTION":
        return "end"
    if state.status == "REPLAN":
        return "planner"
    # No constitutional issues — use existing heal/verify logic
    return should_heal(state)


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
_builder.add_node("constitution_guard", constitution_guard_node)
_builder.add_node("healer", healer_node)
_builder.add_node("verifier", verifier_node)

_builder.set_entry_point("planner")
_builder.add_edge("planner", "critic")
_builder.add_edge("critic", "executor")
_builder.add_edge("executor", "constitution_guard")  # S9-06: always go through guard
_builder.add_conditional_edges(
    "constitution_guard", guard_route,
    {"planner": "planner", "heal": "healer", "verify": "verifier", "end": END},
)
_builder.add_edge("healer", "planner")
_builder.add_conditional_edges(
    "verifier", should_continue,
    {"heal": "healer", "end": END},
)

dan_app = _builder.compile()

__all__ = ["dan_app", "DANState", "check_code_constitution", "DAN_CODE_CONSTITUTION"]
