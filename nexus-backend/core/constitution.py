"""
nexus/core/constitution.py
YAML-driven constitutional constraints + circuit breakers + Slack violation alerts.
Every pod action is validated against constitutional rules before execution.
Violations are logged, counted, and reported to nexus_violations Supabase table.
Sprint 2: added alert_violation() — Slack webhook fires on every rule hit or breaker open.
Sprint 9: COCOA self-evolving constitution (EMNLP 2025) — constitutions that co-evolve with the AI.

-- SQL: CREATE TABLE IF NOT EXISTS nexus_constitution_versions (
--     id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
--     pod text,
--     rule_id text,
--     rule_text text,
--     action text CHECK (action IN ('added', 'modified', 'pruned')),
--     judger_score float,
--     triggered_by_violations int,
--     created_at timestamptz DEFAULT now()
-- );
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml  # type: ignore

logger = logging.getLogger(__name__)

_CONSTITUTION_PATH = Path(__file__).parent.parent / "constitution.yaml"

# ── S9-03: violation history for COCOA evolution ──────────────────────────────
# Keys: pod names. Values: list of {rule_id, text, severity, timestamp}
_violation_history: dict[str, list[dict]] = defaultdict(list)
_COCOA_TRIGGER_THRESHOLD = 50  # violations before constitution evolution triggers


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
        """Returns (ok, violation_reason). S9-03: also tracks violations for COCOA."""
        import re
        found_violations = []
        for rule in self.rules:
            if rule.type == "content":
                violated = False
                reason = None
                for pattern in rule.patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        reason = f"Rule {rule.id}: PII pattern detected"
                        violated = True
                        break
                if not violated:
                    for phrase in rule.forbidden_phrases:
                        if phrase.lower() in text.lower():
                            reason = f"Rule {rule.id}: forbidden phrase '{phrase}'"
                            violated = True
                            break
                if violated and reason:
                    found_violations.append({"rule_id": rule.id, "severity": rule.severity, "reason": reason})

        if found_violations:
            first = found_violations[0]
            # S9-03: track all violations for COCOA evolution
            for v in found_violations:
                _violation_history[pod].append({
                    "rule_id": v["rule_id"],
                    "text": text[:200],
                    "severity": v["severity"],
                    "timestamp": datetime.utcnow().isoformat(),
                })
            # Fire Slack alert for first violation
            try:
                asyncio.get_event_loop().create_task(
                    alert_violation(first["rule_id"], pod, text[:100])
                )
            except RuntimeError:
                pass
            return False, first["reason"]
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


def validate(text: str, pod: str = "nexus") -> tuple[bool, Optional[str]]:
    """Module-level helper — delegates to singleton. Importable by pods."""
    return get_constitution().validate(text, pod)


# ── S9-03: COCOA self-evolving constitution (EMNLP 2025) ─────────────────────

def _parse_json_safe_constitution(text: str) -> dict:
    """Extract first JSON object from LLM text."""
    import json
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else {}
    except Exception:
        return {}


async def evolve_constitution(pod: str, supabase_client=None) -> dict:
    """
    COCOA Actor-Guider-Judger constitution evolution.
    Triggers when len(_violation_history[pod]) >= 50.
    Purpose:  Auto-generate and validate new constitutional rules from violation patterns.
    Inputs:   pod str, optional supabase_client
    Outputs:  dict {evolved: bool, new_rule: dict|None, judger_score: float}
    Side Effects: Appends rule to Constitution, writes nexus_constitution_versions,
                  publishes nexus.constitution_evolved, resets violation history
    """
    from core.ai_cascade import cascade_call
    from events.bus import NexusEvent, publish

    violations = _violation_history.get(pod, [])
    if len(violations) < _COCOA_TRIGGER_THRESHOLD:
        return {"evolved": False, "new_rule": None, "judger_score": 0.0}

    const = get_constitution()
    existing_rule_ids = [r.id for r in const.rules]

    try:
        # ACTOR: propose a new rule
        actor_raw = await cascade_call(
            f"You are a Constitutional ACTOR. Study these {len(violations)} violations "
            f"from the {pod} pod: {violations[-20:]}. Propose ONE new or improved constitutional "
            f"rule that would prevent the most common violation pattern. "
            f'Output JSON: {{"rule_id": "str", "rule_text": "str", "category": "str", "rationale": "str"}}',
            task_type="cocoa_actor",
            pod_name=pod,
        )
        proposed_rule = _parse_json_safe_constitution(actor_raw)
        if not proposed_rule.get("rule_id"):
            return {"evolved": False, "new_rule": None, "judger_score": 0.0}

        # GUIDER: identify related + conflicting rules
        guider_raw = await cascade_call(
            f"Select which existing rules are most relevant to this proposed new rule. "
            f"Existing rules: {existing_rule_ids}. Proposed: {proposed_rule}. "
            f'Output JSON: {{"related_rules": [], "conflicts": []}}',
            task_type="cocoa_guider",
            pod_name=pod,
        )
        guider = _parse_json_safe_constitution(guider_raw)
        conflicts = guider.get("conflicts", [])
        if conflicts:
            logger.info("[constitution] COCOA: proposed rule conflicts %s", conflicts)

        # JUDGER: score on specificity, coverage, non_regression
        judger_raw = await cascade_call(
            f"Score this proposed constitutional rule on 3 axes (each 0.0-1.0): "
            f"specificity (is it actionable?), coverage (does it prevent the violations?), "
            f"non_regression (won't it break legitimate uses?). "
            f"Violations it targets: {violations[-5:]}. Proposed rule: {proposed_rule}. "
            f'Output JSON: {{"specificity": 0.8, "coverage": 0.8, "non_regression": 0.8, '
            f'"overall": 0.8, "approve": true}}',
            task_type="cocoa_judger",
            pod_name=pod,
        )
        judger = _parse_json_safe_constitution(judger_raw)
        judger_score = float(judger.get("overall", 0.0))
        approved = judger.get("approve", False)

        if judger_score > 0.75 and approved:
            # Apply new rule to live constitution
            new_rule = ConstitutionRule(
                id=proposed_rule["rule_id"],
                description=proposed_rule.get("rule_text", ""),
                severity="medium",
                forbidden_phrases=[proposed_rule.get("rule_text", "")[:50]],
            )
            const.rules.append(new_rule)

            # Persist to Supabase
            if supabase_client:
                try:
                    import asyncio as _asyncio
                    await _asyncio.to_thread(
                        lambda: supabase_client.table("nexus_constitution_versions").insert({
                            "pod": pod,
                            "rule_id": proposed_rule["rule_id"],
                            "rule_text": proposed_rule.get("rule_text", ""),
                            "action": "added",
                            "judger_score": judger_score,
                            "triggered_by_violations": len(violations),
                        }).execute()
                    )
                except Exception as exc:
                    logger.warning("[constitution] supabase persist fail: %s", exc)

            # Publish event
            try:
                await publish(
                    NexusEvent(pod=pod, event_type="nexus.constitution_evolved",
                               payload={"rule_id": proposed_rule["rule_id"],
                                        "judger_score": judger_score}),
                    supabase_client=supabase_client,
                )
            except Exception as exc:
                logger.debug("[constitution] event publish fail: %s", exc)

            # Reset violation history
            _violation_history[pod] = []
            logger.info("[constitution] COCOA evolved pod=%s rule=%s score=%.3f",
                        pod, proposed_rule["rule_id"], judger_score)
            return {"evolved": True, "new_rule": proposed_rule, "judger_score": judger_score}

        return {"evolved": False, "new_rule": proposed_rule, "judger_score": judger_score}

    except Exception as exc:
        logger.warning("[constitution] COCOA evolve fail: %s", exc)
        return {"evolved": False, "new_rule": None, "judger_score": 0.0}


async def prune_ineffective_rules(pod: str, supabase_client=None) -> int:
    """
    COCOA prune: remove rules with 0 violations in recent history.
    Runs every 200 events via APScheduler (configured in main.py).
    Purpose:  Keep constitution lean by removing never-triggered rules.
    Inputs:   pod str, optional supabase_client
    Outputs:  int count of pruned rules
    Side Effects: Removes rules from Constitution, writes nexus_constitution_versions
    """
    from core.ai_cascade import cascade_call

    const = get_constitution()
    recent_violations = _violation_history.get(pod, [])
    triggered_rule_ids = {v["rule_id"] for v in recent_violations[-100:]}

    pruned = 0
    rules_to_keep = []
    for rule in const.rules:
        if rule.id in triggered_rule_ids or rule.severity == "critical":
            rules_to_keep.append(rule)
            continue

        # Ask LLM whether to keep or prune
        try:
            verdict_raw = await cascade_call(
                f"Is this constitutional rule preventing harm (keep) or never triggered (prune candidate)? "
                f"Rule: {rule.id} — {rule.description}. "
                f"It has NOT been triggered in the last 100 events for pod '{pod}'. "
                f'Output JSON: {{"decision": "keep", "reason": "str"}}',
                task_type="cocoa_judger",
                pod_name=pod,
            )
            verdict = _parse_json_safe_constitution(verdict_raw)
        except Exception:
            verdict = {"decision": "keep"}

        if verdict.get("decision") == "prune":
            if supabase_client:
                try:
                    import asyncio as _asyncio
                    await _asyncio.to_thread(
                        lambda r=rule: supabase_client.table("nexus_constitution_versions").insert({
                            "pod": pod,
                            "rule_id": r.id,
                            "rule_text": r.description,
                            "action": "pruned",
                            "judger_score": 0.0,
                            "triggered_by_violations": 0,
                        }).execute()
                    )
                except Exception as exc:
                    logger.warning("[constitution] prune supabase fail: %s", exc)
            pruned += 1
            logger.info("[constitution] pruned rule=%s pod=%s", rule.id, pod)
        else:
            rules_to_keep.append(rule)

    const.rules = rules_to_keep
    return pruned
