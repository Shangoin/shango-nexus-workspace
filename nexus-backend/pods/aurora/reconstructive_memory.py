"""
nexus/pods/aurora/reconstructive_memory.py
Semantic search over past calls → reconstruct buyer persona before each call.

Purpose:  DeepMind-style hybrid neural-cognitive memory reconstruction.
          Retrieves 5 semantically similar past calls and synthesises a
          prospect persona: likely objections, winning openers, buying stage.
Inputs:   lead dict (company, name, pain_point, tier, country_code, message)
Outputs:  dict with reconstructed_persona, likely_objections, proven_openers,
          buying_stage, recommended_close
Side Effects: Writes reconstruction to L1 Redis memory via remember()
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def reconstruct_prospect_persona(lead: dict) -> dict:
    """
    Purpose:  Semantic search past calls → reconstruct persona for THIS lead.
    Inputs:   lead dict
    Outputs:  dict with reconstructed_persona, likely_objections, proven_openers,
              buying_stage, recommended_close
    Side Effects: Writes reconstruction to L1 Redis memory
    """
    from core.memory import recall, remember
    from core.ai_cascade import cascade_call

    # Build semantic query from lead signals
    query = " ".join(filter(None, [
        lead.get("company", ""),
        lead.get("pain_point", ""),
        lead.get("tier", ""),
        lead.get("country_code", ""),
        lead.get("message", ""),
    ]))

    empty_result = {
        "reconstructed_persona": "No prior data — treat as cold prospect",
        "likely_objections": [],
        "proven_openers": [],
        "buying_stage": "awareness",
        "recommended_close": "Can we schedule a quick 15-min call this week?",
    }

    try:
        similar_calls = await recall(query=query, pod="aurora", top_k=5)
    except Exception as exc:
        logger.warning("[reconstructive_memory] recall fail: %s", exc)
        return empty_result

    if not similar_calls:
        return empty_result

    try:
        raw = await cascade_call(
            f"""You are reconstructing a sales prospect persona from similar past calls.
Lead context: {json.dumps(lead, default=str)}
Similar past calls (transcripts + outcomes): {similar_calls}

Output ONLY valid JSON with these exact keys:
- reconstructed_persona (str): What this type of prospect values and fears
- likely_objections (list[str]): Top 3 objections this prospect will raise
- proven_openers (list[str]): Top 2 opening lines that worked with similar prospects
- buying_stage (str): one of awareness/consideration/decision
- recommended_close (str): Specific time-anchored closing ask

No extra keys. No markdown fences. Raw JSON only.""",
            task_type="memory_reconstruction",
            pod_name="aurora",
        )

        # Parse JSON
        result: dict = json.loads(raw.strip())

        # Cache reconstruction in L1 for this session
        try:
            await remember(
                content=result,
                pod="aurora",
                metadata={"type": "persona_reconstruction", "company": lead.get("company", "")},
            )
        except Exception:
            pass  # Cache failure is non-critical

        return result

    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("[reconstructive_memory] parse fail: %s", exc)
        return empty_result
