"""
nexus/api/payments.py
Stripe + Razorpay subscription management (Sprint 2: INR checkout added).
Aurora Pro $99/mo | DAN $49/mo | Syntropy $29/pack | Sentinel Prime $199/mo | Automation $19/mo
"""

from __future__ import annotations
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from dependencies import get_current_user_id, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(tags=["payments"])

PRODUCTS = {
    "aurora_pro":       {"price_usd": 99,  "name": "Aurora Pro",       "billing": "monthly"},
    "dan_pro":          {"price_usd": 49,  "name": "DAN Pro",           "billing": "monthly"},
    "sentinel_prime":   {"price_usd": 199, "name": "Sentinel Prime",   "billing": "monthly"},
    "shango_automation":{"price_usd": 19,  "name": "Automation Pro",   "billing": "monthly"},
    "syntropy_pack":    {"price_usd": 29,  "name": "Syntropy Pack",    "billing": "one_time"},
    "nexus_pro":        {"price_usd": 299, "name": "Nexus Pro Bundle", "billing": "monthly"},
}


class CheckoutRequest(BaseModel):
    product_id: str
    success_url: str = "https://shango.in/success"
    cancel_url: str = "https://shango.in/pricing"


@router.get("/products")
async def list_products():
    return {"products": PRODUCTS}


@router.post("/stripe/checkout")
async def stripe_checkout(
    body: CheckoutRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    import stripe  # type: ignore
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(400, "Stripe not configured")

    product = PRODUCTS.get(body.product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    mode = "subscription" if product["billing"] == "monthly" else "payment"
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": product["price_usd"] * 100,
                "product_data": {"name": product["name"]},
                **({"recurring": {"interval": "month"}} if mode == "subscription" else {}),
            },
            "quantity": 1,
        }],
        mode=mode,
        success_url=body.success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=body.cancel_url,
        metadata={"user_id": user_id, "product_id": body.product_id},
    )
    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    import stripe  # type: ignore
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as exc:
        raise HTTPException(400, str(exc))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        product_id = session.get("metadata", {}).get("product_id")
        supabase = get_supabase(request)
        try:
            import asyncio
            await asyncio.to_thread(
                lambda: supabase.table("nexus_subscriptions").upsert({
                    "user_id": user_id,
                    "product_id": product_id,
                    "status": "active",
                    "stripe_session_id": session["id"],
                }).execute()
            )
        except Exception as exc:
            logger.warning("[payments] supabase upsert fail: %s", exc)
    return {"received": True}


# ── Razorpay INR Checkout (Sprint 2) ──────────────────────────────────────────

INR_PRICES: dict[str, int] = {
    "aurora_pro":        8500,    # ₹8,500/mo  (~$99)
    "dan_pro":           4200,    # ₹4,200/mo  (~$49)
    "sentinel_prime":   17000,    # ₹17,000/mo (~$199)
    "shango_automation": 1600,    # ₹1,600/mo  (~$19)
    "syntropy_pack":     2500,    # ₹2,500/pack (~$29)
    "nexus_pro":        25000,    # ₹25,000/mo (~$299)
}


class RazorpayOrderRequest(BaseModel):
    product_id: str
    user_email: str


@router.post("/razorpay/create-order")
async def create_razorpay_order(body: RazorpayOrderRequest):
    """
    Purpose:  Create a Razorpay order for INR checkout.
    Inputs:   product_id str, user_email str
    Outputs:  {order_id, amount, currency, key_id}
    Side Effects: Creates order in Razorpay dashboard
    """
    try:
        import razorpay  # type: ignore
    except ImportError:
        raise HTTPException(500, "razorpay package not installed")

    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise HTTPException(400, "Razorpay not configured — set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")

    amount = INR_PRICES.get(body.product_id)
    if not amount:
        raise HTTPException(404, f"Product '{body.product_id}' not found")

    try:
        rz_client = razorpay.Client(auth=(key_id, key_secret))
        order = rz_client.order.create({
            "amount": amount * 100,   # paise
            "currency": "INR",
            "receipt": f"nexus_{body.product_id}",
            "notes": {
                "product": body.product_id,
                "email": body.user_email,
            },
        })
        logger.info("[payments] razorpay order created product=%s amount=₹%d", body.product_id, amount)
        return {
            "order_id": order["id"],
            "amount": amount,
            "currency": "INR",
            "key_id": key_id,   # Frontend needs this for Razorpay.js
        }
    except Exception as exc:
        logger.error("[payments] razorpay order fail: %s", exc)
        raise HTTPException(500, f"Razorpay error: {exc}")


@router.post("/razorpay/verify")
async def verify_razorpay_payment(request: Request):
    """
    Purpose:  Verify Razorpay payment signature and activate subscription.
    Inputs:   {razorpay_order_id, razorpay_payment_id, razorpay_signature, product_id, user_id}
    Outputs:  {verified: bool}
    Side Effects: Upserts nexus_subscriptions row on success
    """
    import hmac
    import hashlib

    body = await request.json()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")

    order_id = body.get("razorpay_order_id", "")
    payment_id = body.get("razorpay_payment_id", "")
    signature = body.get("razorpay_signature", "")

    expected = hmac.new(
        key_secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "Invalid Razorpay signature")

    supabase = get_supabase(request)
    try:
        import asyncio
        await asyncio.to_thread(
            lambda: supabase.table("nexus_subscriptions").upsert({
                "user_id": body.get("user_id", ""),
                "product_id": body.get("product_id", ""),
                "status": "active",
                "razorpay_payment_id": payment_id,
                "currency": "INR",
            }).execute()
        )
    except Exception as exc:
        logger.warning("[payments] razorpay supabase upsert fail: %s", exc)

    return {"verified": True, "payment_id": payment_id}
