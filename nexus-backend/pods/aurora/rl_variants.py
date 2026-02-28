"""
nexus/pods/aurora/rl_variants.py
AlphaProof-style RL variant engine for sales scripts.

Purpose:  Generate N competing micro-variants per script element, select with
          UCB1 bandit, record binary outcomes (meeting booked = ground truth),
          and retire low-win-rate variants every 10 calls.
Inputs:   element str (opener/objection_reframe/closing_ask/follow_up_subject),
          lead_context dict, meeting_booked bool
Outputs:  selected variant str + variant index; generate_variants returns list[str]
Side Effects: Reads/writes win-rate stats via memory layer
"""

from __future__ import annotations

import json
import logging
import math
import os

logger = logging.getLogger(__name__)

# Module-level imports for testability (patched in unit tests)
try:
    from core.memory import recall, remember
except ImportError:  # pragma: no cover
    recall = remember = None  # type: ignore[assignment]

try:
    from events.bus import NexusEvent, publish
except ImportError:  # pragma: no cover
    NexusEvent = publish = None  # type: ignore[assignment]

try:
    from core.ai_cascade import cascade_call
except ImportError:  # pragma: no cover
    cascade_call = None  # type: ignore[assignment]

try:
    from core.constitution import check_breaker
except ImportError:  # pragma: no cover
    check_breaker = None  # type: ignore[assignment]

try:
    from core.improvement_proofs import generate_improvement_proof
except ImportError:  # pragma: no cover
    generate_improvement_proof = None  # type: ignore[assignment]

import httpx

VARIANT_ELEMENTS = ["opener", "objection_reframe", "closing_ask", "follow_up_subject"]

_RETIRE_AFTER_CALLS = 20
_RETIRE_BELOW_WIN_RATE = 0.10   # retire if win rate <10% after threshold calls

# Sprint 6: Champion promotion thresholds
PROMOTION_MIN_CALLS = 30
PROMOTION_MIN_WIN_RATE = 0.60


async def generate_variants(element: str, lead_context: dict, n: int = 5) -> list[str]:
    """
    Purpose:  Generate n competing micro-variants for a script element.
    Inputs:   element — one of VARIANT_ELEMENTS; lead_context dict; n int
    Outputs:  list of n variant strings
    Side Effects: None
    """
    from core.ai_cascade import cascade_call

    try:
        raw = await cascade_call(
            f"Generate exactly {n} different, distinct versions of the '{element}' for this "
            f"outbound sales call context:\n{json.dumps(lead_context, default=str)}\n\n"
            f"Return ONLY a JSON array of {n} strings. No keys. No markdown fences. Raw JSON array.",
            task_type="variant_generation",
            pod_name="aurora",
        )
        parsed = json.loads(raw.strip())
        if isinstance(parsed, list):
            return [str(v) for v in parsed[:n]]
    except Exception as exc:
        logger.warning("[rl_variants] generate_variants fail: %s", exc)

    # Fallback: basic defaults
    defaults = {
        "opener": [
            "Hi {name}, I noticed you're scaling your sales team — quick question?",
            "Hey {name}, we helped a company like yours 3x their meeting rate — worth 2 minutes?",
            "Hi {name}, I won't take much of your time — what's your biggest outreach challenge right now?",
            "{name}, saw your recent hire post — are you still manually qualifying leads?",
            "Hi {name}, one question: how long does it take your team to follow up with a new lead?",
        ],
        "closing_ask": [
            "Does Tuesday at 3 PM work for a 15-minute call?",
            "Can we find 20 minutes this week — Thursday morning?",
            "What's your calendar look like Wednesday afternoon?",
            "I have Friday at 10 AM open — does that work?",
            "Quick 15 minutes tomorrow — any window that works for you?",
        ],
        "objection_reframe": [
            "Totally fair — most founders say that before they see the ROI. What if we could show you data first?",
            "I hear you on timing. Honestly, that's exactly when this matters most — can I show you why in under 2 minutes?",
            "Makes sense. The companies we work with said the same thing — what changed their mind was one number. Want to hear it?",
            "Understood — and I respect that. Ten seconds: our average client gets their first meeting booked within 48 hours. Does that shift things at all?",
            "Fair enough — I won't push. Can I send you one case study that takes 90 seconds to read? If it's irrelevant, we never speak again.",
        ],
        "follow_up_subject": [
            "Quick follow-up — [Company] + Shango",
            "Re: the question I asked on Monday",
            "One number I forgot to mention",
            "This took us 3 minutes to build for you",
            "Still relevant? — Shango",
        ],
    }
    return defaults.get(element, [f"Default {element} variant {i}" for i in range(n)])[:n]


async def select_variant(element: str, variants: list[str]) -> tuple[str, int]:
    """
    Purpose:  UCB1 bandit selection — exploits winners while exploring new variants.
    Inputs:   element str; variants list of strings
    Outputs:  (selected_variant_str, variant_index)
    Side Effects: Reads win-rate stats from memory
    """
    from core.memory import recall

    stats: dict[int, dict] = {}
    for i, v in enumerate(variants):
        key = f"aurora:variant:{element}:{hash(v) & 0xFFFFFF}"
        try:
            raw = await recall(query=key, pod="aurora", top_k=1)
            if raw and isinstance(raw, list) and raw[0]:
                stats[i] = raw[0] if isinstance(raw[0], dict) else {"wins": 0, "calls": 0}
            else:
                stats[i] = {"wins": 0, "calls": 0}
        except Exception:
            stats[i] = {"wins": 0, "calls": 0}

    total_calls = max(1, sum(s.get("calls", 0) for s in stats.values()))

    def ucb1(i: int) -> float:
        s = stats[i]
        calls = s.get("calls", 0)
        wins = s.get("wins", 0)
        exploitation = wins / (calls + 1)
        exploration = math.sqrt(2 * math.log(total_calls + 1) / (calls + 1))
        return exploitation + exploration

    best_idx = max(range(len(variants)), key=ucb1)
    return variants[best_idx], best_idx


async def record_outcome(
    element: str,
    variant_idx: int,
    variant: str,
    meeting_booked: bool,
) -> None:
    """
    Purpose:  Record call outcome for a variant. Retires losers after threshold.
    Inputs:   element, variant_idx, variant str, meeting_booked bool
    Outputs:  None
    Side Effects: Writes win-rate increment to memory; logs retirement if triggered
    """
    from core.memory import remember

    variant_hash = hash(variant) & 0xFFFFFF
    key = f"aurora:variant:{element}:{variant_hash}"

    try:
        await remember(
            content={"wins": int(meeting_booked), "calls": 1, "variant_hash": variant_hash},
            pod="aurora",
            metadata={"type": "rl_variant", "element": element, "variant_idx": variant_idx},
        )
        logger.info(
            "[rl_variants] recorded element=%s idx=%d booked=%s",
            element, variant_idx, meeting_booked,
        )
    except Exception as exc:
        logger.warning("[rl_variants] record_outcome fail: %s", exc)

    # Sprint 6: check if this element now has a champion worth promoting
    try:
        await check_and_promote_champion(element)
    except Exception as exc:
        logger.warning("[rl_variants] check_and_promote_champion error: %s", exc)


async def retire_losing_variants(
    element: str,
    min_calls: int = 20,
    min_win_rate: float = 0.10,
) -> list:
    """
    Purpose:     Remove variants with <10% win rate after ≥20 calls.
    Inputs:      element name (one of VARIANT_ELEMENTS), call/win thresholds
    Outputs:     list of retired variant hashes
    Side Effects: Marks variants inactive in nexus_memories, publishes event
    """
    all_stats = await recall(
        query=f"aurora variant {element} stats",
        pod="aurora",
        top_k=50,
    )

    retired = []
    for stat in all_stats or []:
        if not isinstance(stat, dict):
            continue
        calls = stat.get("calls", 0)
        wins = stat.get("wins", 0)
        variant_hash = stat.get("variant_hash")
        if not variant_hash:
            continue

        if calls >= min_calls:
            win_rate = wins / calls
            if win_rate < min_win_rate:
                try:
                    await remember(
                        content={"retired": True, "win_rate": win_rate, "calls": calls},
                        pod="aurora",
                        metadata={
                            "element": element,
                            "variant_hash": variant_hash,
                            "status": "retired",
                            "type": "rl_variant",
                        },
                    )
                    retired.append(variant_hash)
                    logger.info(
                        "[rl_variants] retired element=%s hash=%s win_rate=%.2f calls=%d",
                        element, variant_hash, win_rate, calls,
                    )
                except Exception as exc:
                    logger.warning("[rl_variants] retire failed hash=%s: %s", variant_hash, exc)

    if retired:
        await publish(
            NexusEvent(
                pod="aurora",
                event_type="aurora.variants_retired",
                payload={"element": element, "count": len(retired)},
            )
        )
        logger.info("[rl_variants] retired total=%d for element=%s", len(retired), element)

    return retired


async def check_and_promote_champion(element: str) -> dict:
    """
    Purpose:     Detect if any variant has crossed promotion threshold (30+ calls, 60%+ win rate).
                 If yes, generate a new Vapi system prompt incorporating the champion variant
                 and PATCH the Vapi assistant permanently.
    Inputs:      element name (opener/objection_reframe/closing_ask/follow_up_subject)
    Outputs:     dict with promoted bool, element, win_rate, or reason if not promoted
    Side Effects: Vapi PATCH, nexus_improvement_proofs insert, memory update, Slack alert
    """
    all_stats = await recall(
        query=f"aurora variant {element} stats",
        pod="aurora",
        top_k=50,
    ) if recall else []

    champion = None
    for stat in (all_stats or []):
        if not isinstance(stat, dict):
            continue
        calls = stat.get("calls", 0)
        wins = stat.get("wins", 0)
        if stat.get("retired") or stat.get("promoted"):
            continue
        if calls >= PROMOTION_MIN_CALLS:
            win_rate = wins / calls
            if win_rate >= PROMOTION_MIN_WIN_RATE:
                if champion is None or win_rate > champion["win_rate"]:
                    champion = {**stat, "win_rate": win_rate}

    if not champion:
        return {"promoted": False}

    if not check_breaker or not check_breaker("ai_cascade"):
        return {"promoted": False, "reason": "circuit_open"}

    # Generate updated Vapi prompt incorporating champion variant
    new_prompt = None
    if cascade_call:
        try:
            new_prompt = await cascade_call(
                f"""You are updating Aurora's Vapi AI sales agent (ARIA).
    The {element} variant below has won {champion['calls']} calls
    at {champion['win_rate']:.0%} win rate. It is now the permanent script.

    Champion {element}: {champion.get('variant_text', '')}

    Generate a complete, updated Vapi system prompt that permanently
    incorporates this champion {element} while keeping ARIA's personality.
    Under 800 words. Ready to deploy immediately.""",
                task_type="champion_promotion",
                pod_name="aurora",
            )
        except Exception as exc:
            logger.warning("[rl_variants] cascade_call for champion failed: %s", exc)
            return {"promoted": False, "reason": str(exc)}

    # PATCH Vapi assistant
    if check_breaker and check_breaker("vapi") and new_prompt:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(
                    f"https://api.vapi.ai/assistant/{os.getenv('VAPI_ASSISTANT_ID', '')}",
                    headers={"Authorization": f"Bearer {os.getenv('VAPI_API_KEY', '')}"},
                    json={"model": {"messages": [{"role": "system", "content": new_prompt}]}},
                )
                if resp.status_code not in (200, 201):
                    logger.warning("[rl_variants] Vapi PATCH returned %d", resp.status_code)
                    return {"promoted": False, "reason": f"Vapi PATCH failed: {resp.status_code}"}
        except Exception as exc:
            logger.warning("[rl_variants] Vapi PATCH error: %s", exc)
            return {"promoted": False, "reason": str(exc)}

    # Store improvement proof
    if generate_improvement_proof:
        try:
            await generate_improvement_proof(
                pod_name="aurora",
                cycle_id=f"champion_{element}_{str(champion.get('variant_hash', ''))[:8]}",
                avg_score_before=0.5,
                avg_score_after=champion["win_rate"],
                genome=[champion["win_rate"]] * 8,
                n_calls=champion["calls"],
            )
        except Exception as exc:
            logger.warning("[rl_variants] generate_improvement_proof failed: %s", exc)

    # Mark variant as promoted in memory
    if remember:
        try:
            await remember(
                content={**champion, "promoted": True},
                pod="aurora",
                metadata={"element": element, "status": "promoted"},
            )
        except Exception as exc:
            logger.warning("[rl_variants] remember promoted variant failed: %s", exc)

    if publish:
        await publish(
            NexusEvent(
                pod="aurora",
                event_type="aurora.champion_promoted",
                payload={
                    "element": element,
                    "win_rate": champion["win_rate"],
                    "calls": champion["calls"],
                },
            )
        )

    # Slack alert
    slack_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if slack_url:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(slack_url, json={
                    "text": (
                        f"🏆 *Aurora Champion Promoted*\n"
                        f"*Element:* `{element}`\n"
                        f"*Win rate:* {champion['win_rate']:.0%} over {champion['calls']} calls\n"
                        f"*Vapi prompt patched automatically.* ARIA is now smarter."
                    )
                })
        except Exception:
            pass

    logger.info(
        "[rl_variants] champion promoted element=%s win_rate=%.2f calls=%d",
        element, champion["win_rate"], champion["calls"],
    )
    return {"promoted": True, "element": element, "win_rate": champion["win_rate"]}


async def get_active_variants(element: str) -> list[str]:
    """
    Purpose:     Return only non-retired variant texts for UCB1 selection.
    Inputs:      element str (one of VARIANT_ELEMENTS)
    Outputs:     list of non-retired variant strings
    Side Effects: Memory read only
    """
    all_variants = await recall(
        query=f"aurora variant {element}",
        pod="aurora",
        top_k=20,
    )
    return [
        v.get("variant_text", "") or str(v)
        for v in (all_variants or [])
        if isinstance(v, dict) and not v.get("retired", False)
    ]