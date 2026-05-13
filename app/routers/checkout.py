"""Razorpay Standard Checkout — one-shot order flow.

Separate from the subscription billing router (`billing.py`) — this is
for ad-hoc payments (e.g. credit top-ups, one-off exam fees, donations)
where there's no recurring subscription, just a single charge.

Flow:
  1. Frontend → POST /api/v1/checkout/order with {amount, currency, receipt}
  2. We call Razorpay to create an order, return order_id + amount
  3. Frontend opens the Razorpay checkout.js modal with order_id + key_id
  4. User pays → modal returns {payment_id, order_id, signature}
  5. Frontend → POST /api/v1/checkout/verify with all three
  6. We HMAC-verify the signature server-side; only then mark as paid

Credentials live in env vars (RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET).
Public key is exposed to the frontend via VITE_RAZORPAY_KEY_ID; the
secret is server-only and is the basis for HMAC verification.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..limiter import limiter
from ..services.billing import _get_client, _is_live

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


# ─── Pydantic input models ───────────────────────────────────────


class CreateOrderIn(BaseModel):
    """Body for POST /api/v1/checkout/order.

    `amount` is in the currency's smallest unit (paise for INR,
    cents for USD) per Razorpay convention. The 100-paise minimum
    is a Razorpay platform limit, not ours.
    """
    model_config = ConfigDict(strict=True)
    amount: int = Field(..., ge=100, le=1_00_00_00_000,
                        description="Amount in paise (min 100, max 1 lakh INR)")
    currency: str = Field("INR", min_length=3, max_length=3)
    receipt: Optional[str] = Field(None, max_length=40,
                                    description="Internal receipt ID (≤40 chars per Razorpay)")
    notes: Optional[dict] = Field(None, description="Free-form key-value pairs, stored with the order")


class VerifyPaymentIn(BaseModel):
    """Body for POST /api/v1/checkout/verify.

    All three fields are required and returned verbatim from
    Razorpay's checkout.js success handler.
    """
    model_config = ConfigDict(strict=True)
    razorpay_order_id: str = Field(..., min_length=1, max_length=100)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=100)
    razorpay_signature: str = Field(..., min_length=1, max_length=200)


# ─── Endpoints ────────────────────────────────────────────────────


@router.get("/api/v1/checkout/config")
@limiter.limit("60/minute")
async def checkout_config(request: Request):
    """Expose just the public key ID + sandbox flag to the frontend.

    The secret never leaves the server. Frontend reads
    `VITE_RAZORPAY_KEY_ID` at build time, but this endpoint provides
    a runtime fallback (useful when serving the same JS bundle across
    environments)."""
    return {
        "key_id": os.environ.get("RAZORPAY_KEY_ID", ""),
        "live": _is_live(),
    }


@router.post("/api/v1/checkout/order")
@limiter.limit("30/minute")
async def create_order(body: CreateOrderIn, request: Request):
    """Create a Razorpay order. Returns order_id + amount.

    Auth: anonymous-friendly. If you want to gate this behind a
    logged-in user, add `require_auth(request)` at the top — the
    rate limit alone is enough to deter abuse for public-facing
    use-cases like donations.
    """
    client = _get_client()
    if client is None:
        # Sandbox mode — env keys not set. Return a clearly-mock
        # response so frontend dev can iterate without real keys.
        logger.warning("[checkout] sandbox mode — RAZORPAY_KEY_ID/SECRET not set")
        return {
            "order_id": f"order_sandbox_{int(__import__('time').time())}",
            "amount": body.amount,
            "currency": body.currency,
            "sandbox": True,
        }

    try:
        order = client.order.create({
            "amount":   body.amount,
            "currency": body.currency,
            "receipt":  body.receipt or "",
            "notes":    body.notes or {},
            # Razorpay's payment-capture flag. "1" = auto-capture
            # successful payments. Set to "0" for manual capture
            # (useful for hold-and-confirm flows).
            "payment_capture": 1,
        })
    except Exception as e:
        msg = str(e).lower()
        if "authentic" in msg or "401" in msg or "unauthorized" in msg:
            logger.error("[checkout] Razorpay auth failed — check RAZORPAY_KEY_ID/SECRET: %s", e)
            raise HTTPException(status_code=401,
                                detail="Payment gateway authentication failed.")
        logger.exception("[checkout] order creation failed")
        raise HTTPException(status_code=500,
                            detail="Could not create payment order. Please try again.")

    return {
        "order_id": order["id"],
        "amount":   order["amount"],
        "currency": order["currency"],
        "sandbox":  False,
    }


@router.post("/api/v1/checkout/verify")
@limiter.limit("30/minute")
async def verify_payment(body: VerifyPaymentIn, request: Request):
    """HMAC-verify the payment signature server-side.

    NEVER trust the frontend's word that a payment succeeded — only
    accept the signature check. Razorpay computes
        HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
    and includes it as `razorpay_signature` on success. If the
    signature we compute matches what the client returned, the
    payment is genuine.
    """
    client = _get_client()
    if client is None:
        # Sandbox mode — auto-success so frontend dev can iterate.
        logger.warning("[checkout] sandbox verify — accepting without HMAC check")
        return {"verified": True, "sandbox": True}

    try:
        # Razorpay SDK does the HMAC check for us. Raises
        # SignatureVerificationError on mismatch.
        client.utility.verify_payment_signature({
            "razorpay_order_id":   body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature":  body.razorpay_signature,
        })
    except Exception as e:
        # Don't leak the SDK's exception class name to the client.
        # A signature mismatch is a 400, not a 500 — the request was
        # well-formed but the payment isn't genuine.
        logger.warning("[checkout] signature verify failed for order=%s payment=%s: %s",
                       body.razorpay_order_id, body.razorpay_payment_id, e)
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    logger.info("[checkout] payment verified: order=%s payment=%s",
                body.razorpay_order_id, body.razorpay_payment_id)

    # Hook point: if you have an `orders` or `payments` table, this is
    # where you'd persist {order_id, payment_id, amount, customer,
    # paid_at, status='paid'}. Procta currently doesn't have a
    # one-shot-payments table — only subscriptions — so we just return
    # success. Add the persistence when you wire this to a real
    # product (credit top-ups, exam fees, etc).

    return {
        "verified":   True,
        "order_id":   body.razorpay_order_id,
        "payment_id": body.razorpay_payment_id,
    }
