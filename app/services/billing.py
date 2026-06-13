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
from datetime import datetime

from ..constants import PLANS, OVERAGE_BILLING_ENABLED, OVERAGE_GRACE
from ..database import async_table as _atable
from ..services.sessions import PLAN_LIMITS as _PLAN_LIMITS

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


# ─── Overage computation ──────────────────────────────────────────

async def compute_overage(org_id: str, period_start: datetime, period_end: datetime) -> dict:
    """Count distinct students across all org teachers within [period_start,
    period_end) and compute overage (used − cap) × overage_price_inr.

    Returns {students_used, plan_limit, overage_count, amount_inr}.
    """
    oid = str(org_id)

    # Resolve plan + effective cap from the subscription row at call time.
    sub_rows = (await _atable("subscriptions").select("plan")
                .eq("org_id", oid).limit(1).execute()).data or []
    plan_id = (sub_rows[0].get("plan") or "starter").strip().lower() if sub_rows else "starter"
    org_rows = (await _atable("organizations").select("max_students")
                .eq("id", oid).limit(1).execute()).data or []
    plan_limit = int(org_rows[0]["max_students"]) if org_rows else _PLAN_LIMITS.get(plan_id, 30)
    price_per_student = PLANS.get(plan_id, {}).get("overage_price_inr", 0)

    # All teacher ids for this org.
    tid_rows = (await _atable("teachers").select("id")
                .eq("org_id", oid).execute()).data or []
    org_teacher_ids = [str(r["id"]) for r in tid_rows]
    if not org_teacher_ids:
        return {"students_used": 0, "plan_limit": plan_limit,
                "overage_count": 0, "amount_inr": 0}

    # Count distinct students who submitted in [period_start, period_end).
    q = (await _atable("exam_sessions")
         .select("student_id", count="exact", distinct_on="student_id")
         .in_("teacher_id", org_teacher_ids)
         .gte("submitted_at", period_start.isoformat())
         .lt("submitted_at", period_end.isoformat())
         .execute())
    students_used = q.count or 0
    overage = max(0, students_used - plan_limit)
    amount = overage * price_per_student
    return {"students_used": students_used, "plan_limit": plan_limit,
            "overage_count": overage, "amount_inr": amount}


async def bill_cycle_overage(org_id: str, sub_row_before: dict) -> dict:
    """Create a Razorpay add-on for the overage in the cycle that just ended.

    Called from the ``subscription.charged`` webhook branch **after** the
    subscription DB row has been updated but **before** the billing_event is
    recorded, so that if this function raises the webhook properly 500s and
    Razorpay retries.  Internal failures (add-on API error) are swallowed
    and logged — the base webhook must still succeed.

    *sub_row_before* is the subscription-row snapshot taken **before** the
    webhook applied its status/period update — its ``current_period_start`` /
    ``current_period_end`` represent the just-ended cycle.

    Idempotency: the ``overage_charges_period_uniq`` UNIQUE constraint on
    ``(org_id, period_start)`` prevents double-billing on webhook retry.
    """
    oid = str(org_id)

    period_start = sub_row_before.get("current_period_start")
    period_end = sub_row_before.get("current_period_end")
    if not period_start or not period_end:
        logger.warning("bill_cycle_overage: missing period for org=%s — skipping", oid)
        return {"status": "skipped", "reason": "no_period"}

    res = await compute_overage(oid, period_start, period_end)
    if not res["overage_count"]:
        return {"status": "skipped", "reason": "no_overage"}

    ps_iso = period_start.isoformat() if hasattr(period_start, "isoformat") else period_start
    pe_iso = period_end.isoformat() if hasattr(period_end, "isoformat") else period_end

    # Will this overage actually be charged, or only recorded? (grace band,
    # feature flag, and live-Razorpay all have to hold.)
    client = _get_client() if (res["overage_count"] > OVERAGE_GRACE
                               and OVERAGE_BILLING_ENABLED and _is_live()) else None
    will_charge = client is not None

    # CLAIM-THEN-CHARGE: insert the ledger row BEFORE any money moves. The
    # (org_id, period_start) UNIQUE constraint is the idempotency guard — a
    # webhook redelivery for the same cycle trips it here and we return
    # WITHOUT creating a second Razorpay add-on. Charging first (the previous
    # ordering) would double-bill on a redelivery that lacks an event id,
    # which the webhook handler explicitly tolerates.
    claim = {
        "org_id": oid,
        "period_start": ps_iso,
        "period_end": pe_iso,
        "students_used": res["students_used"],
        "plan_limit": res["plan_limit"],
        "overage_count": res["overage_count"],
        "amount_inr": res["amount_inr"],
        "razorpay_addon_id": None,
        "status": "pending" if will_charge else "skipped",
    }
    try:
        await _atable("overage_charges").insert(claim).execute()
    except Exception as exc:
        err = str(exc).lower()
        if "unique" in err or "duplicate" in err or "23505" in err:
            logger.info("bill_cycle_overage: duplicate period for org=%s — idempotent skip", oid)
            return {"status": "duplicate"}
        logger.exception("bill_cycle_overage: claim insert failed for org=%s", oid)
        return {"status": "error"}

    if not will_charge:
        return {"status": "skipped", "overage_count": res["overage_count"],
                "amount_inr": res["amount_inr"]}

    # We hold the claim — now create the add-on. A failure here is recorded
    # ('failed', visible in the usage endpoint) but NEVER raised: the base
    # subscription.charged webhook must still return 200. The claim row blocks
    # an automatic retry from double-charging; a stuck 'failed' row is settled
    # manually (follow-up #4c: safe retry of failed overage add-ons).
    sub_id = sub_row_before.get("razorpay_subscription_id")
    addon_id = None
    status = "charged"
    try:
        addon = client.subscription.createAddon(sub_id, {
            "item": {
                "name": f"Overage: {res['overage_count']} student{'s' if res['overage_count'] != 1 else ''}",
                "amount": res["amount_inr"] * 100,  # paise
                "currency": "INR",
            },
            "quantity": 1,
        })
        addon_id = str(addon.get("id", "")) or None
    except Exception:
        logger.exception("Razorpay add-on creation failed for org=%s", oid)
        status = "failed"

    # Settle the claim with the outcome.
    try:
        await _atable("overage_charges").update(
            {"status": status, "razorpay_addon_id": addon_id}
        ).eq("org_id", oid).eq("period_start", ps_iso).execute()
    except Exception as exc:
        logger.warning("bill_cycle_overage: status update failed for org=%s: %s", oid, exc)

    if status == "charged":
        try:
            await record_billing_event(
                event_id=None,
                org_id=oid,
                event_type="overage.addon",
                amount=res["amount_inr"],
                status="charged",
                razorpay_subscription_id=sub_id,
                payload={"overage_charges": {**claim, "razorpay_addon_id": addon_id, "status": status},
                         "subscription_id": sub_id},
            )
        except Exception as exc:
            logger.warning("bill_cycle_overage: billing_event record failed for org=%s: %s", oid, exc)

    return {"status": status, "overage_count": res["overage_count"], "amount_inr": res["amount_inr"]}


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
