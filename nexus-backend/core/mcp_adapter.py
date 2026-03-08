"""
nexus/core/mcp_adapter.py
Model Context Protocol (MCP) adapter — standardised tool registry for all pods.

Sprint 11 — S11-00: MCP Integration (ArXiv 2026, industry-wide adoption).

Purpose:
  Replace per-pod bespoke tool-calling logic with a single, uniform interface.
  Every pod calls mcp_call("tool_name", **kwargs) instead of writing custom
  Supabase, Redis, or HTTP code.  New pods wire up in hours, not days.

Interface contract (MCP-compatible):
  - Tools are registered with a name, description, and async handler.
  - mcp_call() routes to the correct handler, logs timing, and returns the result.
  - list_tools() returns the full tool catalogue for introspection or LLM tool-use.

Pre-registered tools (no new API keys):
  supabase_query     — SELECT rows from any Supabase table
  supabase_insert    — INSERT a row into any Supabase table
  supabase_upsert    — UPSERT a row (on_conflict by id)
  redis_get          — GET a key from Redis (returns None on miss)
  redis_set          — SET a key in Redis with optional TTL
  cascade            — Run cascade_call (LLM) through the MCP interface
  publish_event      — Publish a NexusEvent through the event bus

Usage:
  from core.mcp_adapter import mcp_call, register_tool

  # In any pod router:
  data = await mcp_call("supabase_query", table="aurora_leads", filters={"tier": "A"})
  await mcp_call("supabase_insert", table="nexus_events", data={...})
  text = await mcp_call("cascade", prompt="Summarise this call", pod_name="aurora")
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ── Tool schema ───────────────────────────────────────────────────────────────

@dataclass
class NexusTool:
    """MCP-compatible tool definition."""
    name: str
    description: str
    handler: Callable[..., Coroutine]
    parameters: dict = field(default_factory=dict)  # JSON Schema for params

    def to_mcp_spec(self) -> dict:
        """Return MCP-compatible tool specification (for LLM tool-use prompts)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.parameters},
        }


# ── Tool registry ─────────────────────────────────────────────────────────────

_TOOLS: dict[str, NexusTool] = {}


def register_tool(
    name: str,
    handler: Callable[..., Coroutine],
    description: str = "",
    parameters: dict | None = None,
) -> None:
    """
    Register a tool in the MCP registry.
    Pods and integrations can add custom tools at startup.
    """
    _TOOLS[name] = NexusTool(
        name=name,
        description=description,
        handler=handler,
        parameters=parameters or {},
    )
    logger.debug("[mcp] registered tool=%s", name)


def list_tools() -> list[dict]:
    """Return MCP-compatible tool catalogue (for LLM tool-use prompts)."""
    return [t.to_mcp_spec() for t in _TOOLS.values()]


async def mcp_call(tool_name: str, **kwargs: Any) -> Any:
    """
    Invoke a registered MCP tool by name.
    All pod tool calls should go through this function.

    Purpose:  Unified tool dispatch — replaces scattered bespoke calls.
    Inputs:   tool_name str, **kwargs passed to the tool handler
    Outputs:  Whatever the tool handler returns
    Side Effects: Logs timing; raises MCPToolError on unknown tool or handler error
    """
    tool = _TOOLS.get(tool_name)
    if tool is None:
        available = ", ".join(_TOOLS.keys()) or "none"
        raise MCPToolError(f"Unknown MCP tool '{tool_name}'. Available: {available}")

    t0 = time.monotonic()
    try:
        result = await tool.handler(**kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        logger.debug("[mcp] tool=%s ok elapsed=%.1fms", tool_name, elapsed)
        return result
    except MCPToolError:
        raise
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        logger.warning("[mcp] tool=%s error elapsed=%.1fms err=%s", tool_name, elapsed, exc)
        raise MCPToolError(f"Tool '{tool_name}' failed: {exc}") from exc


class MCPToolError(RuntimeError):
    """Raised when an MCP tool call fails or the tool is not registered."""


# ── Pre-registered tools ──────────────────────────────────────────────────────

async def _supabase_query(
    table: str,
    filters: dict | None = None,
    select: str = "*",
    limit: int = 100,
    supabase_client: Any = None,
) -> list[dict]:
    """
    SELECT rows from a Supabase table.
    filters: dict of {column: value} equality checks.
    Returns list of row dicts.
    """
    if supabase_client is None:
        try:
            from dependencies import get_supabase
            supabase_client = get_supabase()
        except Exception:
            return []

    try:
        query = supabase_client.table(table).select(select).limit(limit)
        for col, val in (filters or {}).items():
            query = query.eq(col, val)
        response = await asyncio.to_thread(lambda: query.execute())
        return response.data or []
    except Exception as exc:
        logger.warning("[mcp.supabase_query] table=%s err=%s", table, exc)
        return []


async def _supabase_insert(
    table: str,
    data: dict,
    supabase_client: Any = None,
) -> dict:
    """
    INSERT a single row into a Supabase table.
    Returns inserted row dict (or empty dict on error).
    """
    if supabase_client is None:
        try:
            from dependencies import get_supabase
            supabase_client = get_supabase()
        except Exception:
            return {}

    try:
        response = await asyncio.to_thread(
            lambda: supabase_client.table(table).insert(data).execute()
        )
        rows = response.data or []
        return rows[0] if rows else {}
    except Exception as exc:
        logger.warning("[mcp.supabase_insert] table=%s err=%s", table, exc)
        return {}


async def _supabase_upsert(
    table: str,
    data: dict,
    on_conflict: str = "id",
    supabase_client: Any = None,
) -> dict:
    """
    UPSERT a row in a Supabase table.
    Returns upserted row dict (or empty dict on error).
    """
    if supabase_client is None:
        try:
            from dependencies import get_supabase
            supabase_client = get_supabase()
        except Exception:
            return {}

    try:
        response = await asyncio.to_thread(
            lambda: supabase_client.table(table).upsert(
                data, on_conflict=on_conflict
            ).execute()
        )
        rows = response.data or []
        return rows[0] if rows else {}
    except Exception as exc:
        logger.warning("[mcp.supabase_upsert] table=%s err=%s", table, exc)
        return {}


async def _redis_get(key: str, redis_client: Any = None) -> str | None:
    """
    GET a key from Redis.  Returns None on miss or if Redis unavailable.
    """
    if redis_client is None:
        try:
            from dependencies import get_redis
            redis_client = get_redis()
        except Exception:
            return None

    if redis_client is None:
        return None

    try:
        val = await redis_client.get(key)
        return val.decode() if isinstance(val, bytes) else val
    except Exception:
        return None


async def _redis_set(
    key: str,
    value: str,
    ttl: int = 3600,
    redis_client: Any = None,
) -> bool:
    """
    SET a key in Redis with optional TTL (seconds).  Returns True on success.
    """
    if redis_client is None:
        try:
            from dependencies import get_redis
            redis_client = get_redis()
        except Exception:
            return False

    if redis_client is None:
        return False

    try:
        await redis_client.set(key, value, ex=ttl)
        return True
    except Exception:
        return False


async def _cascade_tool(
    prompt: str,
    task_type: str = "general",
    pod_name: str = "nexus",
    redis_client: Any = None,
    skip_cache: bool = False,
) -> str:
    """
    Run cascade_call through the MCP interface.
    Pods that want LLM output call mcp_call("cascade", prompt=..., pod_name=...).
    """
    from core.ai_cascade import cascade_call
    return await cascade_call(
        prompt=prompt,
        task_type=task_type,
        redis_client=redis_client,
        skip_cache=skip_cache,
        pod_name=pod_name,
    )


async def _publish_event_tool(
    pod: str,
    event_type: str,
    payload: dict,
    supabase_client: Any = None,
) -> None:
    """
    Publish a NexusEvent through the event bus via MCP.
    """
    from events.bus import NexusEvent, publish
    event = NexusEvent(pod=pod, event_type=event_type, payload=payload)
    await publish(event, supabase_client=supabase_client)


# ── Register all pre-built tools at import time ───────────────────────────────

register_tool(
    name="supabase_query",
    handler=_supabase_query,
    description="SELECT rows from any Supabase table with optional equality filters.",
    parameters={
        "table": {"type": "string"},
        "filters": {"type": "object"},
        "select": {"type": "string"},
        "limit": {"type": "integer"},
    },
)

register_tool(
    name="supabase_insert",
    handler=_supabase_insert,
    description="INSERT a single row into a Supabase table.",
    parameters={
        "table": {"type": "string"},
        "data": {"type": "object"},
    },
)

register_tool(
    name="supabase_upsert",
    handler=_supabase_upsert,
    description="UPSERT a row in a Supabase table (on_conflict by id by default).",
    parameters={
        "table": {"type": "string"},
        "data": {"type": "object"},
        "on_conflict": {"type": "string"},
    },
)

register_tool(
    name="redis_get",
    handler=_redis_get,
    description="GET a key from Redis. Returns None on miss.",
    parameters={
        "key": {"type": "string"},
    },
)

register_tool(
    name="redis_set",
    handler=_redis_set,
    description="SET a key in Redis with optional TTL (seconds, default 3600).",
    parameters={
        "key": {"type": "string"},
        "value": {"type": "string"},
        "ttl": {"type": "integer"},
    },
)

register_tool(
    name="cascade",
    handler=_cascade_tool,
    description="Call the 6-LLM cascade. Returns humanized, PII-scrubbed text.",
    parameters={
        "prompt": {"type": "string"},
        "task_type": {"type": "string"},
        "pod_name": {"type": "string"},
    },
)

register_tool(
    name="publish_event",
    handler=_publish_event_tool,
    description="Publish a NexusEvent to the in-process bus and Supabase.",
    parameters={
        "pod": {"type": "string"},
        "event_type": {"type": "string"},
        "payload": {"type": "object"},
    },
)
