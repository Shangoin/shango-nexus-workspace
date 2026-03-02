"""
nexus/core/memory.py
Three-tier memory for all pods:
  L1: Redis hash  (hot, 1-hour TTL)
  L2: pgvector via Supabase  (warm, 30-day retention)
  L3: mem0 long-term (cold)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_REDIS_TTL = 3600          # 1 hour
_PG_RETENTION_DAYS = 30

# ── S9-04: HiMem decay rates (arXiv:2601.06377) ──────────────────────────────
# Per-day decay multiplier applied by decay_memories() (daily at 3 AM)
MEMORY_DECAY: dict[str, float] = {
    "episodic": 0.95,    # specific calls, leads — decays fast
    "semantic": 0.99,    # learned patterns — decays slowly
    "procedural": 1.0,   # RSA-signed proofs, verified facts — never decays
    "causal": 0.97,      # causal chains — medium decay
}


# ────────────────────────────────────────────────────────────────────────────
# L1: Redis
# ────────────────────────────────────────────────────────────────────────────

async def redis_store(redis_client, pod: str, key: str, value: Any, ttl: int = _REDIS_TTL) -> None:
    if redis_client is None:
        return
    try:
        ns_key = f"nexus:{pod}:{key}"
        payload = json.dumps(value) if not isinstance(value, (str, bytes)) else value
        await redis_client.set(ns_key, payload, ex=ttl)
    except Exception as exc:
        logger.warning("[memory.redis] store fail: %s", exc)


async def redis_fetch(redis_client, pod: str, key: str) -> Optional[Any]:
    if redis_client is None:
        return None
    try:
        ns_key = f"nexus:{pod}:{key}"
        raw = await redis_client.get(ns_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw.decode()
    except Exception as exc:
        logger.warning("[memory.redis] fetch fail: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
# L2: pgvector (Supabase)
# ────────────────────────────────────────────────────────────────────────────

async def _embed(text: str) -> list[float]:
    """Lightweight embedding via Gemini text-embedding-004 (768-dim)."""
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    result = await __import__("asyncio").to_thread(
        genai.embed_content,
        model="models/text-embedding-004",
        content=text,
    )
    return result["embedding"]


async def pgvector_upsert(
    supabase_client,
    pod: str,
    content: str,
    metadata: dict,
    memory_type: str = "semantic",
    initial_weight: float = 1.0,
) -> None:
    """Store content + embedding in nexus_memories table with memory_type and weight."""
    try:
        embedding = await _embed(content)
        payload = {
            "pod": pod,
            "content": content,
            "embedding": embedding,
            "metadata": {
                **(metadata or {}),
                "memory_type": memory_type,
                "weight": initial_weight,
            },
            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        }
        await __import__("asyncio").to_thread(
            lambda: supabase_client.table("nexus_memories")
            .upsert(payload)
            .execute()
        )
    except Exception as exc:
        logger.warning("[memory.pgvector] upsert fail: %s", exc)


async def pgvector_search(
    supabase_client,
    pod: str,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Semantic nearest-neighbour lookup using pgvector match_nexus_memories RPC.
    Results ranked by weighted_score = similarity * memory_weight (S9-04).
    """
    try:
        embedding = await _embed(query)
        result = await __import__("asyncio").to_thread(
            lambda: supabase_client.rpc(
                "match_nexus_memories",
                {
                    "query_embedding": embedding,
                    "match_count": top_k,
                    "filter_pod": pod,
                },
            ).execute()
        )
        candidates = result.data or []
        # S9-04: weight results by memory decay weight
        for candidate in candidates:
            similarity_score = float(candidate.get("similarity", 0.5))
            meta = candidate.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = __import__("json").loads(meta)
                except Exception:
                    meta = {}
            weight = float(meta.get("weight", 1.0))
            candidate["weighted_score"] = similarity_score * weight
        candidates.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
        return candidates[:top_k]
    except Exception as exc:
        logger.warning("[memory.pgvector] search fail: %s", exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# L3: mem0 long-term
# ────────────────────────────────────────────────────────────────────────────

class LongTermMemory:
    """Thin wrapper around mem0 for persistent pod-level memory."""

    def __init__(self, pod: str):
        self.pod = pod
        self._client = None  # lazy-init

    def _get_client(self):
        if self._client is None:
            from mem0 import Memory  # type: ignore

            self._client = Memory()
        return self._client

    def add(self, user_id: str, messages: list[dict]) -> None:
        try:
            self._get_client().add(messages, user_id=f"{self.pod}:{user_id}")
        except Exception as exc:
            logger.warning("[memory.mem0] add fail: %s", exc)

    def search(self, user_id: str, query: str) -> list[dict]:
        try:
            return self._get_client().search(query, user_id=f"{self.pod}:{user_id}")
        except Exception as exc:
            logger.warning("[memory.mem0] search fail: %s", exc)
            return []


# ────────────────────────────────────────────────────────────────────────────
# Unified facade
# ────────────────────────────────────────────────────────────────────────────

async def remember(
    redis_client,
    supabase_client,
    pod: str,
    key: str,
    content: str,
    metadata: dict | None = None,
    memory_type: str = "semantic",
    initial_weight: float = 1.0,
) -> None:
    """Write to all three tiers. S9-04: accepts memory_type and initial_weight."""
    await redis_store(redis_client, pod, key, content)
    await pgvector_upsert(
        supabase_client, pod, content,
        metadata or {},
        memory_type=memory_type,
        initial_weight=initial_weight,
    )


async def recall(
    redis_client,
    supabase_client,
    pod: str,
    key: str,
    query: str,
    top_k: int = 5,
) -> dict:
    """Read from best available tier. pgvector results ranked by weighted_score (S9-04)."""
    hot = await redis_fetch(redis_client, pod, key)
    if hot:
        return {"source": "redis", "data": hot}
    warm = await pgvector_search(supabase_client, pod, query, top_k)
    if warm:
        return {"source": "pgvector", "data": warm}
    return {"source": "none", "data": []}


async def decay_memories(supabase_client, pod: str | None = None) -> int:
    """
    S9-04: HiMem temporal decay — runs daily at 3 AM via APScheduler.
    For each memory in nexus_memories (per memory_type decay rate):
      new_weight = current_weight * MEMORY_DECAY[memory_type]
      If new_weight < 0.01: delete the row (memory expired)
      Else: update weight in metadata jsonb
    Returns count of deleted (expired) memories.

    Purpose:  Prevent memory accumulation — keep only high-relevance memories.
    Inputs:   supabase_client, optional pod filter
    Outputs:  int count of pruned memories
    Side Effects: Deletes or updates rows in nexus_memories table
    """
    if supabase_client is None:
        return 0

    pruned = 0
    try:
        query = supabase_client.table("nexus_memories").select("id, metadata")
        if pod:
            query = query.eq("pod", pod)
        result = await __import__("asyncio").to_thread(lambda: query.execute())
        rows = result.data or []

        import json as _json
        for row in rows:
            meta = row.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            memory_type = meta.get("memory_type", "semantic")
            if memory_type == "procedural":
                continue  # never decays
            decay_rate = MEMORY_DECAY.get(memory_type, 0.99)
            current_weight = float(meta.get("weight", 1.0))
            new_weight = current_weight * decay_rate

            if new_weight < 0.01:
                # Memory expired — delete
                await __import__("asyncio").to_thread(
                    lambda rid=row["id"]: supabase_client.table("nexus_memories")
                    .delete().eq("id", rid).execute()
                )
                pruned += 1
            else:
                # Update weight in metadata
                meta["weight"] = round(new_weight, 6)
                await __import__("asyncio").to_thread(
                    lambda rid=row["id"], m=meta: supabase_client.table("nexus_memories")
                    .update({"metadata": m}).eq("id", rid).execute()
                )
    except Exception as exc:
        logger.warning("[memory.decay] fail: %s", exc)

    logger.info("[memory.decay] pruned=%d pod=%s", pruned, pod or "all")
    return pruned


# ── S9-02: re-export causal_recall for pod imports ────────────────────────────────
try:
    from core.causal_graph import causal_recall as causal_recall  # noqa: F401
except ImportError:
    async def causal_recall(*args, **kwargs) -> list[dict]:  # type: ignore
        return []
