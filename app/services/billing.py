"""Razorpay billing integration.

Supports two modes:
  - sandbox (default): works without Razorpay keys for local dev/testing
  - live: uses Razorpay API when RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set

Environment variables:
  RAZORPAY_KEY_ID         (live mode)
  RAZORPAY_KEY_SECRET     (live mode)
  RAZORPAY_WEBHOOK_SECRET (live mode — validates webhook signatures)
  RAZORPAY_PLAN_STARTER   plan ID in Razorpay dashboard
  RAZORPAY_PLAN_GROWTH    plan ID in Razorpay dashboard
  RAZORPAY_PLAN_PRO       plan ID in Razorpay dashboard
"""

import hashlib
import hmac
import json
import logging
import os

from ..constants import PLANS

logger = logging.getLogger(__name__)


def _is_live() -> bool:
    return bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"))


def _get_client():
    if not _is_live():
        return None
    import razorpay
    return razorpay.Client(
        auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    )


def create_subscription(org_id: str, plan_id: str) -> dict:
    """Create a Razorpay subscription and return checkout details.

    In sandbox mode, returns a mock response.
    """
    if plan_id not in PLANS:
        raise ValueError(f"Unknown plan: {plan_id}")

    plan = PLANS[plan_id]
    plan_key = os.environ.get(f"RAZORPAY_PLAN_{plan_id.upper()}")

    if _is_live() and plan_key:
        client = _get_client()
        sub = client.subscription.create({
            "plan_id": plan_key,
            "total_count": 12,
            "customer_notify": 1,
            "notes": {"org_id": org_id},
        })
        return {
            "subscription_id": sub["id"],
            "short_url": sub.get("short_url", ""),
            "status": sub.get("status", "created"),
        }

    return {
        "subscription_id": f"mock_sub_{org_id[:8]}",
        "short_url": f"https://razorpay.com/payments/mock_{org_id[:8]}",
        "status": "created",
        "_sandbox": True,
        "_note": f"Sandbox: would charge ₹{plan['price_inr']}/mo for {plan['students']} students ({plan['name']})",
    }


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Verify Razorpay webhook signature.

    In sandbox mode, always returns True.
    Sandbox webhooks can be tested via Razorpay dashboard → Settings → Webhooks
    or by setting RAZORPAY_WEBHOOK_SECRET and calling this endpoint directly.
    """
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret or not _is_live():
        return True
    expected = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_plan_details(plan_id: str) -> dict | None:
    """Return plan details or None for unknown plan."""
    return PLANS.get(plan_id)
