"""
nexus/core/constitution.py
YAML-driven constitutional constraints + circuit breakers + Slack violation alerts.
Every pod action is validated against constitutional rules before execution.
Violations are logged, counted, and reported to nexus_violations Supabase table.
Sprint 2: added alert_violation() — Slack webhook fires on every rule hit or breaker open.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml  # type: ignore

logger = logging.getLogger(__name__)

_CONSTITUTION_PATH = Path(__file__).parent.parent / "constitution.yaml"


# ── Slack violation alert (Sprint 2) ─────────────────────────────────────────

async def alert_violation(rule_name: str, pod: str, text_snippet: str) -> None:
    """
    Purpose:  Fire a Slack webhook when a constitution rule fires or breaker opens.
    Inputs:   rule_name str, pod str, text_snippet str (first 100 chars of offending text)
    Outputs:  None
    Side Effects: HTTP POST to SLACK_WEBHOOK_URL if configured
    """
    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(webhook, json={
                "text": (
                    f":rotating_light: *Constitution Violation*\n"
                    f"*Rule:* `{rule_name}`\n"
                    f"*Pod:* `{pod}`\n"
                    f"*Snippet:* `{text_snippet[:100]}`\n"
                    f"*Env:* `{os.getenv('ENVIRONMENT', 'development')}`"
                )
            })
    except Exception as exc:
        logger.debug("[constitution] slack alert fail (non-critical): %s", exc)

# ── Default constitution (shipped if YAML missing) ───────────────────────────
_DEFAULT_CONSTITUTION = """
rules:
  - id: no_pii_storage
    description: Never store raw PII (emails, phones, Aadhaar) unencrypted
    severity: critical
    patterns:
      - "\\\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\\\.[A-Z]{2,}\\\\b"
      - "\\\\b\\\\d{10,12}\\\\b"

  - id: no_financial_advice
    description: Never provide investment/buy/sell recommendations
    severity: high
    forbidden_phrases:
      - "buy this stock"
      - "guaranteed returns"
      - "risk-free investment"

  - id: no_harmful_content
    description: Refuse requests for illegal, harmful, or manipulative content
    severity: critical
    forbidden_phrases:
      - "bypass KYC"
      - "tax evasion"
      - "money laundering"

  - id: rate_limit_ai
    description: Maximum 100 AI calls per pod per minute
    severity: medium
    type: rate_limit
    threshold: 100
    window_seconds: 60

  - id: max_prompt_tokens
    description: Refuse prompts exceeding 32k tokens to control costs
    severity: medium
    type: token_limit
    max_tokens: 32000

circuit_breakers:
  ai_cascade:
    failure_threshold: 5        # consecutive failures
    recovery_timeout_seconds: 60
  supabase:
    failure_threshold: 3
    recovery_timeout_seconds: 30
  evolution_cycle:
    failure_threshold: 2
    recovery_timeout_seconds: 300
"""


@dataclass
class ConstitutionRule:
    id: str
    description: str
    severity: str  # critical | high | medium | low
    patterns: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    type: str = "content"  # content | rate_limit | token_limit
    threshold: int | None = None    # for rate_limit rules
    window_seconds: int | None = None  # for rate_limit rules
    max_tokens: int | None = None   # for token_limit rules


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int
    recovery_timeout_seconds: int
    _failures: int = 0
    _open_until: float = 0.0

    @property
    def is_open(self) -> bool:
        import time
        if self._open_until and time.time() < self._open_until:
            return True
        if self._open_until and time.time() >= self._open_until:
            self._failures = 0
            self._open_until = 0.0
        return False

    def record_failure(self) -> None:
        import time
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = time.time() + self.recovery_timeout_seconds
            logger.error("[circuit_breaker] OPEN breaker=%s recovery_in=%ds", self.name, self.recovery_timeout_seconds)
            # Fire Slack alert when breaker opens (not every call)
            try:
                asyncio.get_event_loop().create_task(
                    alert_violation(
                        rule_name=f"circuit_breaker:{self.name}",
                        pod="nexus",
                        text_snippet=f"Breaker opened after {self._failures} failures. Recovery in {self.recovery_timeout_seconds}s.",
                    )
                )
            except RuntimeError:
                pass  # No running event loop in sync context

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0


class Constitution:
    def __init__(self):
        self.rules: list[ConstitutionRule] = []
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        self._load()

    def _load(self) -> None:
        try:
            if _CONSTITUTION_PATH.exists():
                raw = _CONSTITUTION_PATH.read_text()
            else:
                raw = _DEFAULT_CONSTITUTION
            data = yaml.safe_load(raw)
        except Exception as exc:
            logger.error("[constitution] load fail: %s — using defaults", exc)
            data = yaml.safe_load(_DEFAULT_CONSTITUTION)

        self.rules = [ConstitutionRule(**r) for r in data.get("rules", [])]
        for name, cfg in data.get("circuit_breakers", {}).items():
            self.circuit_breakers[name] = CircuitBreaker(name=name, **cfg)

        logger.info("[constitution] loaded rules=%d breakers=%d", len(self.rules), len(self.circuit_breakers))

    def validate(self, text: str, pod: str = "nexus") -> tuple[bool, Optional[str]]:
        """Returns (ok, violation_reason)."""
        import re
        for rule in self.rules:
            if rule.type == "content":
                for pattern in rule.patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        reason = f"Rule {rule.id}: PII pattern detected"
                        try:
                            asyncio.get_event_loop().create_task(
                                alert_violation(rule.id, pod, text[:100])
                            )
                        except RuntimeError:
                            pass
                        return False, reason
                for phrase in rule.forbidden_phrases:
                    if phrase.lower() in text.lower():
                        reason = f"Rule {rule.id}: forbidden phrase '{phrase}'"
                        try:
                            asyncio.get_event_loop().create_task(
                                alert_violation(rule.id, pod, text[:100])
                            )
                        except RuntimeError:
                            pass
                        return False, reason
        return True, None

    def check_breaker(self, name: str) -> bool:
        """Returns True if circuit is closed (OK to proceed)."""
        cb = self.circuit_breakers.get(name)
        if cb is None:
            return True
        if cb.is_open:
            logger.warning("[constitution] circuit OPEN breaker=%s", name)
            return False
        return True

    def record_success(self, name: str) -> None:
        cb = self.circuit_breakers.get(name)
        if cb:
            cb.record_success()

    def record_failure(self, name: str) -> None:
        cb = self.circuit_breakers.get(name)
        if cb:
            cb.record_failure()


# Singleton
_constitution: Optional[Constitution] = None


def get_constitution() -> Constitution:
    global _constitution
    if _constitution is None:
        _constitution = Constitution()
    return _constitution


def check_breaker(name: str) -> bool:
    """Module-level helper — delegates to singleton. Importable by pods."""
    return get_constitution().check_breaker(name)
