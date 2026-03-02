"""
nexus/core/ai_cascade.py
6-LLM cascade with Gemini 3 Pro as primary (S10-00: upgraded from gemini-2.5-flash).
Priority: gemini-3-pro → groq-llama3.3-70b → cerebras → mistral-small → deepseek-v3 → gpt-4o-mini
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
    "gemini-3-pro",      # S10-00: upgraded from gemini-2.5-flash
    "groq-llama3.3-70b",
    "cerebras",
    "mistral-small",
    "deepseek-v3",
    "gpt-4o-mini",
]

# ── S9-05: ID-RAG stable agent identities (arXiv:2509.25299) ──────────────────────
POD_IDENTITIES: dict[str, dict] = {
    "aurora": {
        "name": "ARIA",
        "role": "Elite AI Sales Representative for Shango India",
        "traits": ["empathetic", "direct", "never pushy", "mirrors vocabulary"],
        "voice": "confident but warm; always specific, never vague",
        "memory_anchor": "aria_behavioral_identity",
    },
    "dan": {
        "name": "DAN",
        "role": "Autonomous IT Problem Solver — Plan, Execute, Self-Heal",
        "traits": ["methodical", "safety-first", "explains every step", "transparent"],
        "voice": "technical precision; never assumes, always verifies",
        "memory_anchor": "dan_behavioral_identity",
    },
    "janus": {
        "name": "JANUS",
        "role": "Autonomous Market Intelligence Agent — regime detection and signal generation",
        "traits": ["analytical", "probabilistic", "risk-aware", "never emotional"],
        "voice": "data-driven; cites confidence scores on every claim",
        "memory_anchor": "janus_behavioral_identity",
    },
    "syntropy": {
        "name": "SAGE",
        "role": "Adaptive AI Tutor — JEE/NEET/SAT specialist, Kolkata focus",
        "traits": ["encouraging", "Socratic", "curriculum-aware", "patient"],
        "voice": "uses student's own words; never talks down",
        "memory_anchor": "sage_behavioral_identity",
    },
    "syntropy_war_room": {
        "name": "SAGE-WARROOM",
        "role": "High-Stakes Exam Strategist — ERS scoring, Prophet predictions",
        "traits": ["intense", "data-driven", "competitive", "honest about gaps"],
        "voice": "direct feedback; uses ERS score in every assessment",
        "memory_anchor": "sage_warroom_identity",
    },
    "sentinel_prime": {
        "name": "SENTINEL",
        "role": "Document Intelligence and Compliance Guardian",
        "traits": ["precise", "conservative", "cites evidence", "PII-aware"],
        "voice": "formal and thorough; never guesses on compliance questions",
        "memory_anchor": "sentinel_identity",
    },
    "sentinel_researcher": {
        "name": "ORACLE",
        "role": "Research Intelligence Agent — MIT/arXiv/DeepMind aggregator",
        "traits": ["curious", "cross-domain", "cites sources", "actionable"],
        "voice": "brief summaries with implementation angles",
        "memory_anchor": "oracle_identity",
    },
    "ralph": {
        "name": "RALPH",
        "role": "Autonomous PRD Forge — spawns Amp agents until all stories pass",
        "traits": ["systematic", "completion-focused", "quality-gated"],
        "voice": "progress-oriented; always states current story + remaining count",
        "memory_anchor": "ralph_identity",
    },
    "shango_automation": {
        "name": "NEXUS-AUTO",
        "role": "Webhook Automation Engine — lead-gen, content, support",
        "traits": ["fast", "graceful-degradation", "idempotent", "silent on success"],
        "voice": "structured JSON responses only",
        "memory_anchor": "auto_identity",
    },
    "viral_music": {
        "name": "MUSE",
        "role": "AI Creative Director — beat-synced video and music generation",
        "traits": ["bold", "trend-aware", "visual", "viral-optimized"],
        "voice": "creative and energetic; speaks in production terms",
        "memory_anchor": "muse_identity",
    },
}

DEFAULT_IDENTITY: dict = {
    "name": "NEXUS",
    "role": "Shango Nexus Autonomous Intelligence",
    "traits": ["helpful", "accurate", "honest"],
    "voice": "clear and direct",
    "memory_anchor": "nexus_identity",
}

# Task types that are meta-system calls — skip identity injection
_META_TASK_PREFIXES = ("mae_", "cocoa_", "causal_")

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
    # S10-00: gemini-3-pro; falls back to preview tag format on auth error
    try:
        model = genai.GenerativeModel("gemini-3-pro")
        response = await asyncio.to_thread(model.generate_content, prompt)
    except Exception:
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
    "gemini-3-pro": _call_gemini,      # S10-00
    "gemini-2.5-flash": _call_gemini,  # legacy alias kept for safety
    "groq-llama3.3-70b": _call_groq,
    "cerebras": _call_cerebras,
    "mistral-small": _call_mistral,
    "deepseek-v3": _call_deepseek,
    "gpt-4o-mini": _call_openai,
}


async def get_identity_context(pod: str, redis_client: Any = None) -> str:
    """
    S9-05: Retrieve last 3 behavioral examples for pod identity anchoring.
    Cached in Redis with 300s TTL to avoid repeated DB lookups.
    Purpose:  Provide stable identity context for ID-RAG persona injection.
    Inputs:   pod str, optional redis_client
    Outputs:  str of formatted past behavioral examples (empty string if none)
    Side Effects: Redis read/write
    """
    cache_key = f"nexus:identity:{pod}"
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached
        except Exception:
            pass

    # No cached identity — return empty (first call bootstraps naturally)
    identity_ctx = ""
    if redis_client is not None:
        try:
            await redis_client.set(cache_key, identity_ctx, ex=300)
        except Exception:
            pass
    return identity_ctx


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
    Sprint 9 S9-05: Injects stable pod identity prefix (ID-RAG) unless meta task.
    """
    # ── AgentOps session start ───────────────────────────────────────────────
    _ao_session = None
    if _agentops_enabled:
        try:
            _ao_session = agentops.start_session(tags=[pod_name, task_type])  # type: ignore
        except Exception:
            pass

    # ── S9-05: ID-RAG identity injection ─────────────────────────────────────
    is_meta_task = task_type.startswith(_META_TASK_PREFIXES)
    if pod_name and pod_name != "nexus" and not is_meta_task:
        identity = POD_IDENTITIES.get(pod_name, DEFAULT_IDENTITY)
        identity_context = await get_identity_context(pod_name, redis_client)
        id_prefix_parts = [
            f"You are {identity['name']}.",
            f"Role: {identity['role']}",
            f"Core traits: {', '.join(identity['traits'])}",
            f"Voice: {identity['voice']}",
        ]
        if identity_context:
            id_prefix_parts.append(identity_context)
        id_prefix_parts.append("---")
        injected_prompt = "\n".join(id_prefix_parts) + "\n" + prompt
    else:
        injected_prompt = prompt

    try:
        result = await _cascade_call_core(
            prompt=injected_prompt,
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


async def deep_think_call(prompt: str, pod_name: str,
                          thinking_budget: int = 8000) -> tuple[str, str]:
    """
    S10-05: Gemini 3 Deep Think mode — extended reasoning for complex analysis.
    Purpose:  Use Gemini's ThinkingConfig for MARS meta-analysis, DEAP review,
              and COCOA judging that deserve long chain-of-thought reasoning.
    Inputs:   prompt str, pod_name str, thinking_budget int (default 8000)
    Outputs:  (final_answer: str, thinking_trace: str)
    Side Effects: One Gemini API call (no cache — deep analysis is unique per call)
    DO NOT use on every-call paths (slow and expensive).
    """
    try:
        import google.generativeai as genai  # optional SDK — fall back gracefully if missing
    except ImportError:
        logger.warning("[deep_think] google-generativeai not installed — falling back to cascade")
        result = await cascade_call(prompt, task_type="deep_think_fallback", pod_name=pod_name)
        return result, ""

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        result = await cascade_call(prompt, task_type="deep_think_fallback", pod_name=pod_name)
        return result, ""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3-pro")
        generation_config = {"max_output_tokens": 8192}
        # Try ThinkingConfig (available in Gemini 3 Deep Think)
        try:
            config = genai.types.GenerationConfig(
                **generation_config,
                thinking_config=genai.types.ThinkingConfig(thinking_budget=thinking_budget),
            )
        except (AttributeError, TypeError):
            # SDK version doesn't support ThinkingConfig yet — use plain config
            config = genai.types.GenerationConfig(**generation_config)

        response = await asyncio.to_thread(model.generate_content, prompt,
                                           generation_config=config)
        thinking_trace = ""
        final_answer = ""
        try:
            for part in response.candidates[0].content.parts:
                if getattr(part, "thought", False):
                    thinking_trace = part.text
                else:
                    final_answer += part.text
        except Exception:
            # Older SDK format — just use response.text
            final_answer = response.text
        return final_answer or response.text, thinking_trace

    except Exception as exc:
        logger.warning("[deep_think] Gemini Deep Think failed, falling back: %s", exc)
        result = await cascade_call(prompt, task_type="deep_think_fallback", pod_name=pod_name)
        return result, ""
