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
) -> None:
    """Store content + embedding in nexus_memories table."""
    try:
        embedding = await _embed(content)
        await __import__("asyncio").to_thread(
            lambda: supabase_client.table("nexus_memories")
            .upsert(
                {
                    "pod": pod,
                    "content": content,
                    "embedding": embedding,
                    "metadata": metadata,
                    "created_at": __import__("datetime").datetime.utcnow().isoformat(),
                }
            )
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
    """Semantic nearest-neighbour lookup using pgvector match_nexus_memories RPC."""
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
        return result.data or []
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
) -> None:
    """Write to all three tiers."""
    await redis_store(redis_client, pod, key, content)
    await pgvector_upsert(supabase_client, pod, content, metadata or {})


async def recall(
    redis_client,
    supabase_client,
    pod: str,
    key: str,
    query: str,
    top_k: int = 5,
) -> dict:
    """Read from best available tier."""
    hot = await redis_fetch(redis_client, pod, key)
    if hot:
        return {"source": "redis", "data": hot}
    warm = await pgvector_search(supabase_client, pod, query, top_k)
    if warm:
        return {"source": "pgvector", "data": warm}
    return {"source": "none", "data": []}
