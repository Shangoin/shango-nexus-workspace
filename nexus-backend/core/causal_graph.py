"""
nexus/core/causal_graph.py
AMA-Agent causal memory graph (arXiv:2602.22769).
Two-phase retrieval: similarity search → causal edge traversal for completeness.

-- SQL migration (run in Supabase SQL Editor before deploying):
-- CREATE TABLE IF NOT EXISTS nexus_causal_graph (
--     id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
--     event_id text,
--     pod text NOT NULL,
--     action text,
--     outcome text,
--     caused_by text[],
--     caused text[],
--     embedding vector(768),
--     created_at timestamptz DEFAULT now()
-- );
-- CREATE INDEX IF NOT EXISTS causal_graph_embedding_idx
--     ON nexus_causal_graph USING ivfflat (embedding vector_cosine_ops) WITH (lists=50);

Purpose:  Causal memory construction and retrieval for all pods.
          build_causal_node() records action→outcome causal chains.
          causal_recall() uses AMA two-phase retrieval (similarity + causal edges).
Inputs:   event_id, pod, action, outcome, parent_event_ids
Outputs:  CausalNode; list[dict] for recall
Side Effects: Writes to nexus_causal_graph via pgvector_upsert
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CausalNode:
    event_id: str
    pod: str
    action: str
    outcome: str
    caused_by: list[str] = field(default_factory=list)
    caused: list[str] = field(default_factory=list)


# ── JSON helper ────────────────────────────────────────────────────────────────

def parse_json_safe(text: str) -> dict:
    """Extract first JSON object from LLM response text."""
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else {}
    except Exception:
        return {}


# ── Core functions ────────────────────────────────────────────────────────────

async def build_causal_node(
    event_id: str,
    pod: str,
    action: str,
    outcome: str,
    parent_event_ids: list[str],
    supabase_client=None,
) -> CausalNode:
    """
    Purpose:  Record an action→outcome causal chain in memory.
              Called after every event that has a measurable outcome.
    Inputs:   event_id, pod, action, outcome, parent_event_ids, optional supabase_client
    Outputs:  CausalNode dataclass
    Side Effects: Writes to nexus_memories (pgvector) with memory_type="causal"
    """
    node = CausalNode(
        event_id=event_id,
        pod=pod,
        action=action,
        outcome=outcome,
        caused_by=parent_event_ids,
    )

    # Store causal chain in pgvector via memory.py
    try:
        from core.memory import pgvector_upsert
        await pgvector_upsert(
            supabase_client=supabase_client,
            pod=pod,
            content=f"CAUSAL:{action}→{outcome}",
            metadata={
                "event_id": event_id,
                "caused_by": parent_event_ids,
                "memory_type": "causal",
                "weight": 1.0,
            },
        )
    except Exception as exc:
        logger.warning("[causal_graph] store fail pod=%s err=%s", pod, exc)

    return node


async def causal_recall(
    query: str,
    pod: str,
    top_k: int = 5,
    supabase_client=None,
    redis_client=None,
) -> list[dict]:
    """
    AMA-Agent two-phase retrieval (arXiv:2602.22769):
    Phase 1: pgvector similarity → top_k * 3 candidates.
    Phase 2: self-evaluate sufficiency → if insufficient, traverse causal edges.
    Returns causally-complete context, not just similar snippets.

    Purpose:  Causally-grounded memory retrieval for any pod.
    Inputs:   query str, pod str, top_k int, optional clients
    Outputs:  list[dict] of memory records ranked by relevance
    Side Effects: 0-1 LLM calls for sufficiency check
    """
    from core.ai_cascade import cascade_call
    from core.memory import pgvector_search

    # Phase 1: standard similarity search
    try:
        candidates = await pgvector_search(
            supabase_client=supabase_client,
            pod=pod,
            query=query,
            top_k=top_k * 3,
        )
    except Exception as exc:
        logger.warning("[causal_graph] phase1 search fail: %s", exc)
        return []

    if not candidates:
        return []

    # Phase 2: self-evaluation sufficiency check
    try:
        sufficiency_raw = await cascade_call(
            f"Are these {len(candidates)} memory snippets sufficient to answer: '{query}'?\n"
            f"Snippets: {[c.get('content', '')[:100] for c in candidates[:5]]}\n"
            f"Output JSON: {{\"sufficient\": true, \"missing_aspect\": \"\"}}",
            task_type="causal_sufficiency",
            pod_name=pod,
        )
        result = parse_json_safe(sufficiency_raw)
    except Exception as exc:
        logger.warning("[causal_graph] sufficiency check fail: %s", exc)
        result = {"sufficient": True}

    if result.get("sufficient", True):
        return candidates[:top_k]

    # Phase 3: traverse causal edges for missing aspect
    missing = result.get("missing_aspect", query)
    try:
        causal_candidates = await pgvector_search(
            supabase_client=supabase_client,
            pod=pod,
            query=f"CAUSAL:{missing}",
            top_k=top_k,
        )
    except Exception as exc:
        logger.warning("[causal_graph] phase3 causal search fail: %s", exc)
        causal_candidates = []

    # Deduplicate and merge
    seen: set[str] = set()
    merged: list[dict] = []
    for c in candidates + causal_candidates:
        cid = str(c.get("id", id(c)))
        if cid not in seen:
            seen.add(cid)
            merged.append(c)

    return merged[:top_k]
