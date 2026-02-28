"""
nexus/config.py
All settings via pydantic-settings → reads from .env
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_key: str
    supabase_service_key: str = ""

    # ── AI Providers ────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    grok_api_key: str = ""       # Groq
    cerebras_api_key: str = ""
    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Voice / Media ───────────────────────────────────────────────────────
    vapi_api_key: str = ""
    vapi_assistant_id: str = ""
    vapi_phone_number_id: str = ""
    vapi_phone_number_id_in: str = ""
    vapi_phone_number_id_us: str = ""
    vapi_phone_number_id_uk: str = ""
    vapi_phone_number_id_global: str = ""
    elevenlabs_api_key: str = ""

    # ── Trading ─────────────────────────────────────────────────────────────
    polygon_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # ── Redis ───────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Payments ────────────────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # ── Observability ───────────────────────────────────────────────────────
    langsmith_api_key: str = ""
    agentops_api_key: str = ""

    # ── Sprint 2+3 additions ─────────────────────────────────────────────────
    slack_webhook_url: str = ""          # Constitution violation alerts
    finnhub_api_key: str = ""            # Janus live market sentiment
    serper_api_key: str = ""             # Aurora proactive scout (Google search)

    # ── App ─────────────────────────────────────────────────────────────────
    environment: str = "development"
    admin_secret: str = "nexus-admin-change-me"
    webhook_base_url: str = "https://nexus.shango.in"
    cors_origins: list[str] = ["https://shango.in", "http://localhost:3000", "http://localhost:5173"]

    # ── FAL (video generation) ───────────────────────────────────────────────
    fal_api_key: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
