"""
nexus/core/ai_cascade.py
6-LLM cascade with Gemini 2.5 Flash as primary.
Priority: gemini-2.5-flash → groq-llama3.3-70b → cerebras → mistral-small → deepseek-v3 → gpt-4o-mini
Cache: Redis (1h TTL) → in-memory LRU fallback.
PII scrub applied before every external call.
Sprint 3: AgentOps session tracing wraps every cascade invocation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── AgentOps init (Sprint 3) ─────────────────────────────────────────────────
_agentops_enabled = False
try:
    import agentops  # type: ignore
    _ao_key = os.getenv("AGENTOPS_API_KEY", "")
    if _ao_key:
        agentops.init(_ao_key, default_tags=["shango-nexus"])
        _agentops_enabled = True
        logger.info("[ai_cascade] AgentOps tracing enabled")
    else:
        logger.debug("[ai_cascade] AGENTOPS_API_KEY not set — tracing disabled")
except ImportError:
    logger.debug("[ai_cascade] agentops not installed — tracing disabled")

# ── Humanizer blocklist ──────────────────────────────────────────────────────
_BANNED = {
    "utilize": "use",
    "leverage": "use",
    "groundbreaking": "notable",
    "revolutionary": "new",
    "paradigm": "approach",
    "robust": "solid",
    "seamless": "smooth",
    "empower": "help",
    "delve": "look",
    "multifaceted": "complex",
}

_PII_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    r"|\b\d{10,12}\b"          # phone
    r"|\b\d{12}\b",            # Aadhaar
    re.IGNORECASE,
)

PROVIDERS = [
    "gemini-2.5-flash",
    "groq-llama3.3-70b",
    "cerebras",
    "mistral-small",
    "deepseek-v3",
    "gpt-4o-mini",
]

# ── In-memory LRU cache (fallback when Redis not available) ─────────────────
_MEM_CACHE: dict[str, tuple[str, float]] = {}
_MEM_CACHE_TTL = 3600  # 1 hour


def _cache_key(prompt: str, task_type: str) -> str:
    raw = f"{task_type}::{prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()


def scrub_pii(text: str) -> str:
    return _PII_PATTERN.sub("[REDACTED]", text)


def humanize(text: str) -> str:
    for banned, replacement in _BANNED.items():
        text = re.sub(rf"\b{banned}\b", replacement, text, flags=re.IGNORECASE)
    text = text.replace("—", ", ").replace("–", "-")
    return text


async def _mem_cache_get(key: str) -> Optional[str]:
    entry = _MEM_CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    _MEM_CACHE.pop(key, None)
    return None


async def _mem_cache_set(key: str, value: str) -> None:
    _MEM_CACHE[key] = (value, time.time() + _MEM_CACHE_TTL)
    # Evict oldest if over 1000 entries
    if len(_MEM_CACHE) > 1000:
        oldest = sorted(_MEM_CACHE, key=lambda k: _MEM_CACHE[k][1])[:100]
        for k in oldest:
            _MEM_CACHE.pop(k, None)


async def _redis_get(redis_client, key: str) -> Optional[str]:
    if redis_client is None:
        return None
    try:
        val = await redis_client.get(key)
        return val.decode() if val else None
    except Exception:
        return None


async def _redis_set(redis_client, key: str, value: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(key, value, ex=_MEM_CACHE_TTL)
    except Exception:
        pass


async def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash-preview-04-17")
    response = await asyncio.to_thread(model.generate_content, prompt)
    return response.text


async def _call_groq(prompt: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=os.environ["GROK_API_KEY"])
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    return resp.choices[0].message.content


async def _call_cerebras(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['CEREBRAS_API_KEY']}"},
            json={
                "model": "llama3.1-70b",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_mistral(prompt: str) -> str:
    from mistralai import Mistral

    client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
    resp = await client.chat.complete_async(
        model="mistral-small-latest",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


async def _call_deepseek(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "HTTP-Referer": "https://shango.in",
            },
            json={
                "model": "deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_openai(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    return resp.choices[0].message.content


_PROVIDER_FNS = {
    "gemini-2.5-flash": _call_gemini,
    "groq-llama3.3-70b": _call_groq,
    "cerebras": _call_cerebras,
    "mistral-small": _call_mistral,
    "deepseek-v3": _call_deepseek,
    "gpt-4o-mini": _call_openai,
}


async def cascade_call(
    prompt: str,
    task_type: str = "general",
    redis_client: Any = None,
    skip_cache: bool = False,
    pod_name: str = "nexus",
) -> str:
    """
    Try each LLM in cascade order.  Returns humanized, PII-scrubbed text.
    Caches result in Redis (if available) and in-memory LRU.
    Sprint 3: Wrapped with AgentOps session tracing when key is configured.
    """
    # ── AgentOps session start ───────────────────────────────────────────────
    _ao_session = None
    if _agentops_enabled:
        try:
            _ao_session = agentops.start_session(tags=[pod_name, task_type])  # type: ignore
        except Exception:
            pass

    try:
        result = await _cascade_call_core(
            prompt=prompt,
            task_type=task_type,
            redis_client=redis_client,
            skip_cache=skip_cache,
            pod_name=pod_name,
        )
        if _ao_session:
            try:
                _ao_session.end_session("Success")
            except Exception:
                pass
        return result
    except Exception as exc:
        if _ao_session:
            try:
                _ao_session.end_session("Fail", end_state_reason=str(exc))
            except Exception:
                pass
        raise


async def _cascade_call_core(
    prompt: str,
    task_type: str = "general",
    redis_client: Any = None,
    skip_cache: bool = False,
    pod_name: str = "nexus",
) -> str:
    """
    Internal cascade implementation.  Returns humanized, PII-scrubbed text.
    """
    clean_prompt = scrub_pii(prompt)
    key = _cache_key(clean_prompt, task_type)

    if not skip_cache:
        cached = await _redis_get(redis_client, key) or await _mem_cache_get(key)
        if cached:
            logger.debug("[cascade] cache hit pod=%s task=%s", pod_name, task_type)
            return cached

    last_err: Exception = RuntimeError("No providers available")
    for provider in PROVIDERS:
        fn = _PROVIDER_FNS.get(provider)
        if fn is None:
            continue
        try:
            raw = await fn(clean_prompt)
            result = humanize(raw)
            await _redis_set(redis_client, key, result)
            await _mem_cache_set(key, result)
            logger.info("[cascade] ok provider=%s pod=%s task=%s", provider, pod_name, task_type)
            return result
        except Exception as exc:
            logger.warning("[cascade] fail provider=%s err=%s", provider, exc)
            last_err = exc

    raise last_err
