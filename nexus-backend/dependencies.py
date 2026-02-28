"""
nexus/dependencies.py
FastAPI dependency injection: shared clients initialized in lifespan, injected per request.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

try:
    from supabase import create_client, Client
except ImportError:  # pragma: no cover
    create_client = None  # type: ignore[assignment]
    Client = object  # type: ignore[assignment,misc]

from config import get_settings

logger = logging.getLogger(__name__)

_header = APIKeyHeader(name="X-Admin-Secret", auto_error=False)


# ── Auth ──────────────────────────────────────────────────────────────────────

async def verify_admin(api_key: str = Security(_header)) -> str:
    settings = get_settings()
    if not api_key or api_key != settings.admin_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")
    return api_key


# ── Client accessors from app.state ──────────────────────────────────────────

def get_supabase(request: Request) -> Client:
    return request.app.state.supabase


def get_redis(request: Request) -> Optional[aioredis.Redis]:
    return getattr(request.app.state, "redis", None)


async def get_current_user_id(request: Request) -> str:
    """
    Extract user_id from Supabase JWT bearer token.
    Returns user_id string or raises 401.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.split(" ", 1)[1]
    supabase: Client = get_supabase(request)
    try:
        user = await __import__("asyncio").to_thread(lambda: supabase.auth.get_user(token))
        if not user or not user.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user.user.id
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[auth] token validation fail: %s", exc)
        raise HTTPException(status_code=401, detail="Token validation failed")
