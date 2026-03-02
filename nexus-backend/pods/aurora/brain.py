"""
nexus/pods/aurora/brain.py
Dual-brain architecture for per-call strategic preparation.

Purpose:  DeepMind Gemini Robotics ER+VLA two-brain separation.
          Brain 1 (strategic) reasons over lead signals BEFORE the call.
          Brain 2 (tactical) converts the brief into a deployable Vapi prompt.
Inputs:   lead dict, optional call_history list, optional base_prompt str
Outputs:  Brain 1 → strategic brief str; Brain 2 → complete Vapi system prompt str
Side Effects: None (read-only reasoning; Vapi injection happens in router.py)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_BASE_PROMPT = (
    "You are ARIA, Shango's AI sales agent. "
    "You are warm, direct, and never pushy. "
    "Your goal is to book a discovery call. "
    "Always end with a specific time offer."
)


async def generate_strategic_brief(lead: dict, call_history: list | None = None) -> str:
    """
    Purpose:  Brain 1 — strategic reasoning BEFORE every call.
    Inputs:   lead dict; call_history (optional list of past call summaries)
    Outputs:  Complete strategic brief string for injection into Brain 2
    Side Effects: None (read-only)
    """
    from pods.aurora.reconstructive_memory import reconstruct_prospect_persona
    from core.ai_cascade import cascade_call
    from core.memory import causal_recall

    call_history = call_history or []
    persona = await reconstruct_prospect_persona(lead)

    # S9-02: AMA causal recall — retrieve causally-grounded winning patterns
    industry = lead.get("industry", lead.get("company", "unknown"))
    try:
        relevant_memories = await causal_recall(
            f"CAUSAL: actions that caused meeting_booked=True for {industry} prospects",
            pod="aurora",
            top_k=5,
        )
        causal_context = "\n".join(
            m.get("content", "") for m in relevant_memories[:3]
        ) if relevant_memories else "No prior causal patterns available."
    except Exception:
        causal_context = ""

    try:
        brief = await cascade_call(
            f"""You are Aurora's strategic sales advisor. Prepare a pre-call brief for ARIA.

Lead data: {lead}
Reconstructed persona: {persona}
Call history (most recent 3): {call_history[-3:] if call_history else "First contact"}
Causal patterns from similar wins: {causal_context}

Output a precise strategic brief with these 7 sections:
1. Buying stage: awareness / consideration / decision
2. Primary pain to lead with (one sentence)
3. Expected #1 objection + exact reframe script (2-3 sentences)
4. Opening line (specific to THIS person — reference company/role/pain)
5. Closing ask (specific day + time: e.g. "Tuesday at 3 PM works?")
6. Tone recommendation: formal / casual / urgent
7. Red flags to avoid (based on similar prospect failures)

Be concise. No fluff. This brief feeds directly into ARIA's system prompt.""",
            task_type="strategic_brief",
            pod_name="aurora",
        )
        return brief
    except Exception as exc:
        logger.error("[brain] strategic_brief fail: %s", exc)
        return f"No brief available — use default ARIA script. Lead: {lead.get('company', 'unknown')}"


async def generate_tactical_prompt(
    strategic_brief: str,
    lead: dict,
    base_prompt: str = _DEFAULT_BASE_PROMPT,
) -> str:
    """
    Purpose:  Brain 2 — tactical Vapi system prompt incorporating Brain 1 brief.
    Inputs:   strategic_brief from Brain 1; lead data; current active base prompt
    Outputs:  Complete Vapi system prompt for THIS CALL ONLY (≤800 words)
    Side Effects: None (pure generation; caller does the PATCH)
    """
    from core.encompass import encompass_branch  # S10-01: 2-branch parallel

    tactical_prompt_text = (
        f"You are generating a Vapi voice-agent system prompt for a single outbound sales call.\n\n"
        f"Base prompt template:\n{base_prompt}\n\n"
        f"Strategic brief for this call:\n{strategic_brief}\n\n"
        f"Lead: {lead.get('name', 'the prospect')} at {lead.get('company', 'their company')}\n"
        f"Country: {lead.get('country_code', 'IN')}\n\n"
        f"Generate a complete, deployable Vapi system prompt that:\n"
        f"- Incorporates the strategic brief naturally into ARIA's personality\n"
        f"- Has specific word-for-word scripts for the expected objection\n"
        f"- References the prospect by name at least twice\n"
        f"- Ends with the exact time-anchored closing ask from the brief\n"
        f"- Is under 800 words\n"
        f"- Does NOT mention \"strategic brief\" or \"AI\" — just natural conversation"
    )
    try:
        # S10-01: EnCompass 2-branch — pick best Vapi prompt (cost-conscious)
        enc_result = await encompass_branch(
            prompt=tactical_prompt_text,
            task_type="tactical_prompt",
            pod_name="aurora",
            state={"lead": lead},
            max_branches=2,
        )
        return enc_result.output
    except Exception as exc:
        logger.error("[brain] tactical_prompt fail: %s", exc)
        return base_prompt  # Fall back to base — never fail silently on Vapi
