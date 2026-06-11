"""Razorpay billing integration.

Supports two modes:
  - sandbox (explicit): works without Razorpay keys for local dev/testing
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


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "development").lower().strip() == "production"


def _sandbox_enabled() -> bool:
    return os.environ.get("RAZORPAY_SANDBOX_MODE", "").lower().strip() in {"1", "true", "yes", "on"}


def create_subscription(org_id: str, plan_id: str, gstin: str | None = None) -> dict:
    """Create a Razorpay subscription and return checkout details.

    Live mode requires Razorpay credentials and a configured Razorpay plan ID.
    Sandbox mode is explicit via RAZORPAY_SANDBOX_MODE=1 and never returns a
    fake external Razorpay URL; otherwise users land on Razorpay's 404 page.

    `gstin` (optional) is recorded in the subscription notes so it appears on
    Razorpay's GST-compliant invoices (the Razorpay account must have GST
    configured for tax invoices to be generated).
    """
    if plan_id not in PLANS:
        raise ValueError(f"Unknown plan: {plan_id}")

    plan = PLANS[plan_id]
    plan_key = os.environ.get(f"RAZORPAY_PLAN_{plan_id.upper()}")
    notes = {"org_id": org_id}
    if gstin:
        notes["gstin"] = gstin

    if _is_live() and plan_key:
        client = _get_client()
        sub = client.subscription.create({
            "plan_id": plan_key,
            "total_count": 12,
            "customer_notify": 1,
            "notes": notes,
        })
        return {
            "subscription_id": sub["id"],
            "short_url": sub.get("short_url", ""),
            "status": sub.get("status", "created"),
        }

    if _is_live() and not plan_key:
        raise RuntimeError(
            f"RAZORPAY_PLAN_{plan_id.upper()} not configured — cannot create subscription"
        )

    if not _sandbox_enabled():
        raise RuntimeError(
            "RAZORPAY_KEY_ID/SECRET not configured — cannot create live subscription"
        )

    return {
        "subscription_id": f"mock_sub_{org_id[:8]}",
        "short_url": "",
        "status": "created",
        "_sandbox": True,
        "_note": f"Sandbox: would charge ₹{plan['price_inr']}/mo for {plan['students']} students ({plan['name']})",
    }


_SANDBOX_WEBHOOK_SECRET = os.environ.get("SANDBOX_WEBHOOK_SECRET", "")

def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Verify Razorpay webhook signature.

    In production: RAZORPAY_WEBHOOK_SECRET must be set; rejects if absent.
    In non-production: if RAZORPAY_WEBHOOK_SECRET is set, verifies against it.
    If explicitly configured with SANDBOX_WEBHOOK_SECRET, verifies against that.
    If neither is set in non-production, still rejects — set SANDBOX_WEBHOOK_SECRET
    to a known value or RAZORPAY_WEBHOOK_SECRET for verified playback.
    """
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "") or _SANDBOX_WEBHOOK_SECRET
    if not secret:
        logger.error("[billing] No webhook secret configured (set RAZORPAY_WEBHOOK_SECRET or SANDBOX_WEBHOOK_SECRET)")
        return False
    expected = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_plan_details(plan_id: str) -> dict | None:
    """Return plan details or None for unknown plan."""
    return PLANS.get(plan_id)


# ─── Entitlement: single source of truth ──────────────────────────────
# The enforced cap (organizations.max_students) is a PROJECTION of the
# subscription's plan+status, written ONLY through reconcile_org_entitlement.
# No other code path may touch max_students — that's what caused the drift.

FREE_CAP = int(PLANS.get("starter", {}).get("students", 30))  # un-entitled floor
_PLAN_CAPS = {p: int(v.get("students", FREE_CAP)) for p, v in PLANS.items()}

# Statuses that grant access. `past_due` = in Razorpay's retry/dunning window
# (we keep service during grace). `cancelling` = cancelled but still inside the
# paid period. `trialing` = active trial. `created` is NOT here on purpose:
# a subscription the customer never authorised must not grant entitlement.
ENTITLING_STATUSES = frozenset({
    "trialing", "authenticated", "active", "past_due", "cancelling",
})


async def reconcile_org_entitlement(org_id: str) -> int:
    """Recompute and persist organizations.max_students from the org's
    subscription state. The ONLY writer of max_students. Returns the cap."""
    from ..database import async_table as _atable
    oid = str(org_id)
    rows = (await _atable("subscriptions").select("plan,status")
            .eq("org_id", oid).limit(1).execute()).data or []
    if rows:
        plan = (rows[0].get("plan") or "starter").strip().lower()
        status = (rows[0].get("status") or "").strip().lower()
    else:
        plan, status = "starter", "none"
    entitled = status in ENTITLING_STATUSES
    cap = _PLAN_CAPS.get(plan, FREE_CAP) if entitled else FREE_CAP
    await _atable("organizations").update({"max_students": cap}).eq("id", oid).execute()
    # Self-contained: the single entitlement writer also drops the stale
    # billing/limit cache so callers can't forget to (which would serve an
    # out-of-date cap).
    try:
        from .. import cache as _cache
        if _cache:
            _cache.delete(f"org_subscription:{oid}")
            _cache.delete(f"org_limits:{oid}")
    except Exception as e:
        # Best-effort bust: the cap is already persisted, so don't fail the
        # entitlement update — but log it, since a silently-swallowed failure
        # means a stale cap is served until the TTL expires.
        logger.warning("Failed to clear billing cache for org %s: %s", oid, e)
    return cap


# ─── Payment/event ledger (DB-durable idempotency + audit) ────────────

async def billing_event_seen(event_id: str) -> bool:
    """True if this Razorpay event.id was already recorded (processed). Used for
    the early webhook short-circuit before any side effects run."""
    from ..database import async_table as _atable
    eid = (event_id or "").strip()
    if not eid:
        return False
    rows = (await _atable("billing_events").select("id")
            .eq("event_id", eid).limit(1).execute()).data or []
    return bool(rows)


async def record_billing_event(*, event_id: str, org_id: str | None, event_type: str,
                               status: str, razorpay_subscription_id: str | None = None,
                               razorpay_payment_id: str | None = None,
                               amount: int | None = None, currency: str = "INR",
                               payload: dict | None = None) -> bool:
    """Append an immutable row to billing_events. The event_id UNIQUE
    constraint makes this the durable webhook-idempotency guard: returns
    False if this event_id was already recorded (duplicate delivery),
    True on a fresh insert. Rows with no event_id are always inserted."""
    import json as _json
    from ..database import async_table as _atable
    eid = (event_id or "").strip()
    if eid:
        prior = (await _atable("billing_events").select("id")
                 .eq("event_id", eid).limit(1).execute()).data or []
        if prior:
            return False
    try:
        await _atable("billing_events").insert({
            "event_id": eid or None,
            "org_id": str(org_id) if org_id else None,
            "razorpay_subscription_id": razorpay_subscription_id,
            "razorpay_payment_id": razorpay_payment_id,
            "event_type": event_type,
            "status": status,
            "amount": amount,
            "currency": currency,
            "payload": _json.dumps(payload) if payload is not None else None,
        }).execute()
        return True
    except Exception as e:
        # Concurrent duplicate (UNIQUE race) → treat as already-processed.
        if "duplicate" in str(e).lower() or "unique" in str(e).lower() or "23505" in str(e):
            return False
        raise
