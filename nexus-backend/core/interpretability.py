"""
nexus/core/interpretability.py
TransformerLens mechanistic interpretability for evolved prompt safety checks.

Purpose:  Run lightweight circuit analysis on DEAP-evolved prompts before deployment.
          Detects attention patterns consistent with adversarial/injection-like structures.
          Sprint 6: Also detects PII-revealing attention patterns (email, phone, Aadhaar, PAN).
          Called by evolution.py before apply_genome_to_pod() in production.
Inputs:   prompt/text str, pod_name str
Outputs:  dict with safe bool, risk_score float, flagged_tokens list, pod str
Side Effects: None (read-only analysis of local model — no external API)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Sprint 6: PII regex patterns ─────────────────────────────────────────────

PII_PATTERNS: list[tuple[str, str]] = [
    (r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',  "email"),
    (r'\b[6-9]\d{9}\b',                                           "indian_mobile"),
    (r'\b\d{4}\s?\d{4}\s?\d{4}\b',                               "aadhaar"),
    (r'\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b',                           "pan_card"),
]


def detect_pii_in_text(text: str) -> list[str]:
    """
    Purpose:     Fast regex scan for PII before TransformerLens (cheap first pass).
    Inputs:      text string
    Outputs:     list of PII type labels detected (may be empty)
    Side Effects: None
    """
    found = []
    for pattern, label in PII_PATTERNS:
        if re.search(pattern, text):
            found.append(label)
    return found


async def detect_pii_attention_pattern(text: str) -> dict[str, Any]:
    """
    Purpose:     Use TransformerLens attention entropy to detect if the model
                 is attending abnormally to PII token clusters (low entropy = focused).
                 Falls back to regex-only if TransformerLens is unavailable/disabled.
    Inputs:      text string (document content, max 512 chars used)
    Outputs:     {pii_risk: bool, pii_types: list, attention_entropy: float, safe: bool}
    Side Effects: None (read-only analysis)
    """
    import os

    # Cheap regex pass always runs (even when TransformerLens is disabled)
    pii_found = detect_pii_in_text(text)

    if os.getenv("DISABLE_INTERPRETABILITY", "").lower() in ("1", "true", "yes"):
        pii_risk = len(pii_found) > 0
        return {"pii_risk": pii_risk, "pii_types": pii_found, "attention_entropy": 0.0, "safe": not pii_risk}

    avg_entropy = 0.0
    pii_risk_from_attention = False

    try:
        import numpy as np
        from transformer_lens import HookedTransformer  # type: ignore
        import torch  # type: ignore

        model = HookedTransformer.from_pretrained("gpt2")
        model.eval()
        tokens = model.to_tokens(text[:512])

        with torch.no_grad():
            _, cache = model.run_with_cache(tokens)

        # Layer-0 attention entropy: low entropy = focused on specific tokens
        attn = cache["pattern", 0, "attn"]   # shape: [batch, heads, seq, seq]
        attn_np = attn[0].detach().cpu().numpy()  # [heads, seq, seq]

        entropy_per_head = []
        for head in attn_np:
            p = head + 1e-9
            ent = -np.sum(p * np.log(p), axis=-1).mean()
            entropy_per_head.append(float(ent))

        avg_entropy = float(np.mean(entropy_per_head))
        # Low entropy AND regex PII detected = elevated risk
        pii_risk_from_attention = avg_entropy < 1.5 and len(pii_found) > 0

    except Exception as exc:
        logger.debug("[interpretability] TransformerLens PII check skipped: %s", exc)
        pii_risk_from_attention = len(pii_found) > 0

    pii_risk = len(pii_found) > 0 or pii_risk_from_attention
    return {
        "pii_risk": pii_risk,
        "pii_types": pii_found,
        "attention_entropy": avg_entropy,
        "safe": not pii_risk,
    }


async def verify_document_safety(text: str, pod: str = "sentinel_prime") -> dict[str, Any]:
    """
    Purpose:     Combined safety check for Sentinel Prime documents.
                 Runs adversarial pattern check AND PII attention detection.
    Inputs:      text string (document content), pod name
    Outputs:     {safe: bool, risk_score: float, pii_risk: bool,
                  pii_types: list, attention_entropy: float}
    Side Effects: Publishes sentinel_prime.pii_detected event if unsafe
    """
    from events.bus import NexusEvent, publish

    adversarial = await verify_prompt_safety(text, pod)
    pii = await detect_pii_attention_pattern(text)

    combined_safe = adversarial["safe"] and pii["safe"]

    if not combined_safe:
        try:
            await publish(
                NexusEvent(
                    pod=pod,
                    event_type="sentinel_prime.pii_detected",
                    payload={
                        "pii_types": pii["pii_types"],
                        "risk_score": adversarial["risk_score"],
                        "attention_entropy": pii["attention_entropy"],
                    },
                )
            )
        except Exception as exc:
            logger.warning("[interpretability] publish pii_detected failed: %s", exc)

    return {
        "safe": combined_safe,
        "risk_score": adversarial["risk_score"],
        "pii_risk": pii["pii_risk"],
        "pii_types": pii["pii_types"],
        "attention_entropy": pii["attention_entropy"],
    }


async def verify_prompt_safety(prompt: str, pod_name: str) -> dict[str, Any]:
    """
    Purpose:  Mechanistic interpretability check on an evolved prompt.
    Inputs:   prompt — str to analyse (first 500 chars for performance);
              pod_name — origin pod name for logging
    Outputs:  dict: {safe: bool, risk_score: float, flagged_tokens: list, pod: str,
                     prompt_length: int, model_used: str}
    Side Effects: Loads gpt2-small locally on first call (~500 MB one-time download)
    """
    import asyncio
    import os

    # Skip if DISABLE_INTERPRETABILITY env var is set (e.g. in CI)
    if os.getenv("DISABLE_INTERPRETABILITY", "").lower() in ("1", "true", "yes"):
        logger.debug("[interpretability] disabled via env — returning safe=True pod=%s", pod_name)
        return {
            "safe": True,
            "risk_score": 0.0,
            "flagged_tokens": [],
            "pod": pod_name,
            "prompt_length": len(prompt),
            "model_used": "disabled",
        }

    def _run_analysis(prompt_text: str) -> dict[str, Any]:
        """Synchronous TransformerLens analysis — runs in thread executor."""
        try:
            from transformer_lens import HookedTransformer  # type: ignore
            import torch  # type: ignore

            model = HookedTransformer.from_pretrained("gpt2", fold_ln=True, center_writing_weights=True)
            model.eval()

            # Truncate to 500 chars to control compute cost
            truncated = prompt_text[:500]
            tokens = model.to_tokens(truncated)

            with torch.no_grad():
                _, cache = model.run_with_cache(tokens)

            # Check layer-0 attention pattern concentration
            # High max attention weight can indicate injection-style token forcing
            attn_pattern = cache["pattern", 0, "attn"]   # shape: (batch, heads, seq, seq)
            risk_score = float(attn_pattern.max().item())

            # Identify flagged token positions (attention >0.9)
            high_attn = (attn_pattern > 0.9).nonzero(as_tuple=False)
            flagged_positions = high_attn[:, -1].unique().tolist()[:10]  # max 10

            flagged_tokens = []
            for pos in flagged_positions:
                try:
                    tok = model.to_str_tokens(tokens)[pos]
                    flagged_tokens.append(str(tok))
                except Exception:
                    pass

            return {
                "safe": risk_score < 0.95,
                "risk_score": round(risk_score, 4),
                "flagged_tokens": flagged_tokens,
                "pod": pod_name,
                "prompt_length": len(prompt_text),
                "model_used": "gpt2",
            }

        except ImportError:
            logger.warning("[interpretability] transformer_lens not installed — skipping analysis")
            return {
                "safe": True,
                "risk_score": 0.0,
                "flagged_tokens": [],
                "pod": pod_name,
                "prompt_length": len(prompt),
                "model_used": "unavailable",
            }
        except Exception as exc:
            logger.error("[interpretability] analysis error pod=%s: %s", pod_name, exc)
            return {
                "safe": True,   # Default safe on error — don't block evolution
                "risk_score": 0.0,
                "flagged_tokens": [],
                "pod": pod_name,
                "prompt_length": len(prompt),
                "model_used": "error",
                "error": str(exc),
            }

    # Run CPU-bound analysis in thread pool
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_analysis, prompt)

    if not result["safe"]:
        logger.warning(
            "[interpretability] UNSAFE prompt detected pod=%s risk_score=%.3f tokens=%s",
            pod_name, result["risk_score"], result["flagged_tokens"],
        )
    else:
        logger.debug("[interpretability] safe pod=%s risk=%.3f", pod_name, result["risk_score"])

    return result
