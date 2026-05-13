"""Stripe billing integration.

Sandbox mode (default): works without Stripe keys for local dev/testing.
Live mode: uses Stripe API when STRIPE_SECRET_KEY is set.

Environment variables:
  STRIPE_SECRET_KEY           (live mode — sk_live_...)
  STRIPE_WEBHOOK_SECRET       (live mode — whsec_..., validates webhooks)
  STRIPE_PRICE_STARTER        Price ID in Stripe dashboard
  STRIPE_PRICE_GROWTH         Price ID in Stripe dashboard
  STRIPE_PRICE_PRO            Price ID in Stripe dashboard
"""

import hashlib
import hmac
import json
import logging
import os

from ..constants import PLANS
from ..invites import _get_invite_base_url

logger = logging.getLogger(__name__)


def _is_live() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _get_client():
    if not _is_live():
        return None
    import stripe as _s
    _s.api_key = os.environ["STRIPE_SECRET_KEY"]
    return _s


def create_checkout_session(org_id: str, plan_id: str, success_url: str = None, cancel_url: str = None) -> dict:
    """Create a Stripe Checkout Session for the given plan.

    In sandbox mode, returns a mock response.
    """
    if plan_id not in PLANS:
        raise ValueError(f"Unknown plan: {plan_id}")

    price_key = os.environ.get(f"STRIPE_PRICE_{plan_id.upper()}")

    if _is_live() and price_key:
        stripe = _get_client()
        base = _get_invite_base_url()
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_key, "quantity": 1}],
            metadata={"org_id": org_id, "plan": plan_id},
            success_url=success_url or f"{base}/dashboard?tab=billing&checkout=success",
            cancel_url=cancel_url or f"{base}/dashboard?tab=billing&checkout=cancelled",
            subscription_data={"metadata": {"org_id": org_id, "plan": plan_id}},
        )
        return {
            "session_id": session.id,
            "url": session.url,
            "status": "created",
        }

    plan = PLANS[plan_id]
    return {
        "session_id": f"mock_ses_{org_id[:8]}",
        "url": f"https://checkout.stripe.com/mock/{org_id[:8]}",
        "status": "created",
        "_sandbox": True,
        "_note": f"Sandbox: would create subscription for {plan['name']} (₹{plan['price_inr']}/mo, {plan['students']} students)",
    }


def create_portal_session(customer_id: str, return_url: str = None) -> dict:
    """Create a Stripe Billing Portal session for the customer.

    In sandbox mode, returns a mock response.
    """
    if _is_live():
        stripe = _get_client()
        base = _get_invite_base_url()
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url or f"{base}/dashboard?tab=billing",
        )
        return {"url": session.url}
    return {
        "url": f"https://billing.stripe.com/mock/{customer_id[:8]}",
        "_sandbox": True,
    }


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Verify Stripe webhook signature.

    In sandbox mode, always returns True.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret or not _is_live():
        return True
    # Stripe's signature scheme: HMAC-SHA256 with "v1" prefix
    expected = "v1=" + hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def get_plan_details(plan_id: str) -> dict | None:
    """Return plan details or None for unknown plan."""
    return PLANS.get(plan_id)
