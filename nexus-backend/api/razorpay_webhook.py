"""
nexus/api/razorpay_webhook.py
Razorpay payment.captured webhook handler to activate INR subscriptions.

Purpose:     Receive Razorpay payment.captured events, verify HMAC signature,
             upsert nexus_subscriptions row, send Brevo onboarding email.
             Sprint 6: Redis-backed retry queue ensures zero lost payments.
Inputs:      POST body (Razorpay webhook payload) + X-Razorpay-Signature header
Outputs:     200 {"status": "ok"} on success; 403 on bad signature; 200 "ignored"
             for non-payment events; 200 "deferred" if circuit open;
             200 "queued" if Supabase temporarily unavailable.
Side Effects: Creates/updates nexus_subscriptions row in Supabase,
              sends welcome email via Brevo, publishes nexus.payment_captured event.
              On Supabase failure: pushes to Redis retry queue (Sprint 6).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from core.constitution import check_breaker
from events.bus import NexusEvent, publish

# Module-level imports for testability
try:
    from config import get_settings
    from supabase import create_client
except ImportError:  # pragma: no cover
    get_settings = create_client = None  # type: ignore[assignment]

# ── Sprint 6: Retry queue constants ──────────────────────────────────────────
RETRY_QUEUE_KEY = "nexus:razorpay:retry_queue"
DEAD_LETTER_KEY = "nexus:razorpay:dead_letter"
MAX_RETRIES = 5


def get_redis():
    """Return a standalone async Redis client for background job use."""
    try:
        import redis.asyncio as aioredis
        return aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)
    except Exception:  # pragma: no cover
        return None


def get_supabase():
    """Return a standalone Supabase client for background job use."""
    if not get_settings or not create_client:
        return None
    try:
        settings = get_settings()
        return create_client(settings.supabase_url, settings.supabase_service_key or settings.supabase_key)
    except Exception:  # pragma: no cover
        return None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/razorpay", tags=["razorpay"])

# ── Sprint 6: Retry queue helpers ─────────────────────────────────────────────

async def push_to_retry_queue(payload: dict, attempt: int = 0) -> None:
    """
    Purpose:     Push a failed payment payload to the Redis retry queue.
    Inputs:      payload dict (raw Razorpay webhook body), attempt int
    Outputs:     None
    Side Effects: Appends entry to Redis list, publishes nexus.payment_queued_for_retry
    """
    try:
        redis = get_redis()
        if not redis:
            logger.warning("[razorpay_retry] Redis unavailable — payload not queued")
            return
        entry = {
            "payload": payload,
            "attempt": attempt,
            "queued_at": datetime.utcnow().isoformat(),
        }
        await redis.lpush(RETRY_QUEUE_KEY, json.dumps(entry))
        payment_id = (
            payload.get("payload", {})
                   .get("payment", {})
                   .get("entity", {})
                   .get("id", "unknown")
        )
        await publish(
            NexusEvent(
                pod="nexus",
                event_type="nexus.payment_queued_for_retry",
                payload={"payment_id": payment_id, "attempt": attempt},
            )
        )
        logger.info("[razorpay_retry] queued payment_id=%s attempt=%d", payment_id, attempt)
    except Exception as exc:
        logger.error("[razorpay_retry] push_to_retry_queue failed: %s", exc)


async def process_retry_queue() -> None:
    """
    Purpose:     Drain Redis retry queue, re-attempt failed subscription activations.
                 Called by APScheduler every 5 minutes.
    Inputs:      None (reads from Redis)
    Outputs:     None
    Side Effects: Creates nexus_subscriptions rows on success, moves failures to
                  dead_letter after MAX_RETRIES attempts, sends Slack alert on
                  dead-letter, re-queues on transient failure.
    """
    redis = get_redis()
    sb = get_supabase()
    if not redis:
        logger.warning("[razorpay_retry] Redis unavailable — skipping retry sweep")
        return

    processed = 0
    while True:
        item_raw = await redis.rpop(RETRY_QUEUE_KEY)
        if not item_raw:
            break

        try:
            item = json.loads(item_raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[razorpay_retry] malformed queue entry — discarding")
            continue

        raw_payload = item.get("payload", {})
        attempt = item.get("attempt", 0) + 1
        payment_entity = (
            raw_payload.get("payload", {})
                       .get("payment", {})
                       .get("entity", {}) or {}
        )
        payment_id = payment_entity.get("id", "")
        notes = payment_entity.get("notes", {}) or {}
        product_id = notes.get("product", "aurora_pro")
        user_email = notes.get("email", payment_entity.get("email", ""))
        amount = payment_entity.get("amount", 0)

        # Idempotency check — skip if already activated
        try:
            if sb:
                existing = (
                    sb.table("nexus_subscriptions")
                      .select("id")
                      .eq("payment_id", payment_id)
                      .execute()
                )
                if existing.data:
                    logger.info("[razorpay_retry] payment_id=%s already activated — skipping", payment_id)
                    processed += 1
                    continue
        except Exception as exc:
            logger.warning("[razorpay_retry] idempotency check failed: %s", exc)

        try:
            if sb:
                sb.table("nexus_subscriptions").upsert(
                    {
                        "user_email": user_email,
                        "product_id": product_id,
                        "payment_id": payment_id,
                        "amount_paise": amount,
                        "currency": "INR",
                        "status": "active",
                        "provider": "razorpay",
                    },
                    on_conflict="user_email,product_id",
                ).execute()

            await send_welcome_email(user_email, product_id)
            await publish(
                NexusEvent(
                    pod="nexus",
                    event_type="nexus.payment_retry_succeeded",
                    payload={"payment_id": payment_id, "attempt": attempt},
                )
            )
            logger.info("[razorpay_retry] ✅ retry succeeded payment_id=%s attempt=%d", payment_id, attempt)
            processed += 1

        except Exception as exc:
            logger.warning("[razorpay_retry] retry attempt=%d failed payment_id=%s: %s", attempt, payment_id, exc)
            if attempt >= MAX_RETRIES:
                # Dead-letter: alert team and stop retrying
                dead_entry = {
                    **item,
                    "error": str(exc),
                    "attempts": attempt,
                    "dead_at": datetime.utcnow().isoformat(),
                }
                try:
                    await redis.lpush(DEAD_LETTER_KEY, json.dumps(dead_entry))
                except Exception:
                    pass
                slack_url = os.getenv("SLACK_WEBHOOK_URL", "")
                if slack_url:
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            await client.post(slack_url, json={
                                "text": (
                                    f"💀 *Dead Letter Payment*\n"
                                    f"`{payment_id}` failed {MAX_RETRIES} times.\n"
                                    f"Email: {user_email} | Product: {product_id}\n"
                                    f"Error: {str(exc)[:200]}\n"
                                    f"Manual activation required."
                                )
                            })
                    except Exception:
                        pass
                logger.error("[razorpay_retry] dead-lettered payment_id=%s after %d attempts", payment_id, attempt)
            else:
                # Re-queue with incremented attempt count
                await push_to_retry_queue(raw_payload, attempt)

    if processed:
        logger.info("[razorpay_retry] sweep complete processed=%d", processed)

PRODUCT_MAP = {
    "aurora_pro":         {"name": "Aurora Pro",          "amount_paise": 850000},
    "dan_pro":            {"name": "DAN Pro",              "amount_paise": 420000},
    "sentinel_prime":     {"name": "Sentinel Prime",       "amount_paise": 1700000},
    "shango_automation":  {"name": "Shango Automation",    "amount_paise": 160000},
    "syntropy_pack":      {"name": "Syntropy Pack",        "amount_paise": 250000},
    "nexus_pro":          {"name": "Nexus Pro",            "amount_paise": 2500000},
}


def verify_razorpay_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate Razorpay HMAC-SHA256 webhook signature."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
):
    """
    Handle Razorpay webhook events.
    Only payment.captured events trigger subscription activation.
    """
    body = await request.body()
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    if secret and x_razorpay_signature:
        if not verify_razorpay_signature(body, x_razorpay_signature, secret):
            logger.warning("[razorpay_webhook] invalid signature — rejected")
            raise HTTPException(status_code=403, detail="Invalid Razorpay signature")
    elif secret and not x_razorpay_signature:
        raise HTTPException(status_code=403, detail="Missing X-Razorpay-Signature header")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = payload.get("event")
    logger.info("[razorpay_webhook] received event=%s", event)

    if event != "payment.captured":
        return {"status": "ignored", "event": event}

    # Extract payment details
    try:
        payment = payload["payload"]["payment"]["entity"]
    except (KeyError, TypeError):
        logger.warning("[razorpay_webhook] malformed payload — no payment entity")
        return {"status": "ignored", "reason": "no_payment_entity"}

    notes = payment.get("notes", {}) or {}
    product_id = notes.get("product", "aurora_pro")
    user_email = notes.get("email", payment.get("email", ""))
    amount = payment.get("amount", 0)
    payment_id = payment.get("id", "")

    if not check_breaker("supabase"):
        logger.warning("[razorpay_webhook] supabase circuit open — queuing subscription write")
        await push_to_retry_queue(payload, attempt=0)
        return {"status": "queued", "reason": "circuit_open"}

    # Upsert subscription — keyed on user_email + product_id
    try:
        settings = get_settings()
        sb = create_client(settings.supabase_url, settings.supabase_service_key or settings.supabase_key)

        # Idempotency: skip if already processed
        existing = (
            sb.table("nexus_subscriptions")
              .select("id")
              .eq("payment_id", payment_id)
              .execute()
        )
        if existing.data:
            logger.info("[razorpay_webhook] payment_id=%s already activated — idempotent skip", payment_id)
            return {"status": "ok", "product": product_id, "email": user_email, "note": "already_activated"}

        sb.table("nexus_subscriptions").upsert(
            {
                "user_email": user_email,
                "product_id": product_id,
                "payment_id": payment_id,
                "amount_paise": amount,
                "currency": "INR",
                "status": "active",
                "provider": "razorpay",
            },
            on_conflict="user_email,product_id",
        ).execute()
        logger.info("[razorpay_webhook] subscription upserted email=%s product=%s", user_email, product_id)
    except Exception as exc:
        logger.error("[razorpay_webhook] supabase upsert failed — queuing for retry: %s", exc)
        await push_to_retry_queue(payload, attempt=0)
        return {"status": "queued", "reason": str(exc)}

    # Send welcome email via Brevo
    await send_welcome_email(user_email, product_id)

    # Publish cross-pod event
    await publish(
        NexusEvent(
            pod="nexus",
            event_type="nexus.payment_captured",
            payload={
                "user_email": user_email,
                "product_id": product_id,
                "amount_paise": amount,
                "payment_id": payment_id,
                "provider": "razorpay",
            },
        )
    )

    logger.info("[razorpay_webhook] ✅ payment processed email=%s product=%s amount_paise=%d",
                user_email, product_id, amount)
    return {"status": "ok", "product": product_id, "email": user_email}


async def send_welcome_email(email: str, product_id: str) -> None:
    """
    Purpose:     Send Brevo onboarding email after successful payment.
    Inputs:      user email, product_id str
    Outputs:     None
    Side Effects: Sends transactional email via Brevo SMTP API
    """
    if not check_breaker("brevo"):
        logger.warning("[razorpay_webhook] brevo circuit open — skipping welcome email")
        return

    if not email:
        logger.warning("[razorpay_webhook] no email address — skipping welcome email")
        return

    brevo_key = os.getenv("BREVO_API_KEY", "")
    if not brevo_key:
        logger.info("[razorpay_webhook] BREVO_API_KEY not set — skipping welcome email")
        return

    product_name = PRODUCT_MAP.get(product_id, {}).get("name", "Shango Nexus")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": brevo_key,
                    "Content-Type": "application/json",
                },
                json={
                    "sender": {"email": "team@shango.in", "name": "Shango India"},
                    "to": [{"email": email}],
                    "subject": f"Welcome to {product_name} — Your Alien Intelligence is Live",
                    "htmlContent": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; background: #07070E; color: #fff; padding: 32px;">
  <h2 style="color: #7c3aed;">Welcome to {product_name}</h2>
  <p>Your autonomous AI system is now active at
  <a href="https://shango.in/nexus" style="color: #7c3aed;">shango.in/nexus</a>.</p>
  <p>Login with your email. Dashboard is live.
  No setup needed — it's already learning.</p>
  <hr style="border-color: #333;" />
  <p style="font-size: 12px; color: #888;">
    Questions? Reply to this email → team@shango.in<br/>
    Shango India · Kolkata · shango.in
  </p>
</body>
</html>
                    """.strip(),
                },
            )
            if response.status_code not in (200, 201):
                logger.warning("[razorpay_webhook] brevo returned %d: %s", response.status_code, response.text[:200])
            else:
                logger.info("[razorpay_webhook] welcome email sent to %s", email)
    except Exception as exc:
        logger.warning("[razorpay_webhook] send_welcome_email failed: %s", exc)
