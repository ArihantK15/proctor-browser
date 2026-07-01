"""Razorpay billing integration.

Supports two modes:
  - sandbox (explicit): works without Razorpay keys for local dev/testing
  - live: uses Razorpay API when RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set

Environment variables:
  RAZORPAY_KEY_ID         (live mode)
  RAZORPAY_KEY_SECRET     (live mode)
  RAZORPAY_WEBHOOK_SECRET (live mode — validates webhook signatures)
    RAZORPAY_PLAN_STARTER   plan ID in Razorpay dashboard (monthly)
    RAZORPAY_PLAN_STARTER_ANNUAL  plan ID for annual/yearly billing
    RAZORPAY_PLAN_GROWTH    plan ID in Razorpay dashboard (monthly)
    RAZORPAY_PLAN_GROWTH_ANNUAL   plan ID for annual/yearly billing
    RAZORPAY_PLAN_PRO       plan ID in Razorpay dashboard (monthly)
    RAZORPAY_PLAN_PRO_ANNUAL      plan ID for annual/yearly billing
"""

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime
from typing import Any

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


async def validate_coupon(code: str) -> dict[str, Any] | None:
    """Case-insensitive coupon lookup.

    Returns the row dict only if the coupon is active, not past expires_at,
    and (max_redemptions IS NULL OR times_redeemed < max_redemptions).
    Returns None for unknown / invalid / exhausted / expired coupons.
    """
    from datetime import datetime, timezone

    code = (code or "").strip().lower()
    if not code:
        return None
    rows = (await _atable("coupons").select("*")
            .eq("code", code).limit(1).execute()).data or []
    if not rows:
        return None
    row = rows[0]
    if not row.get("active"):
        return None
    expires = row.get("expires_at")
    if expires:
        try:
            expires_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expires_dt = None
        if expires_dt and expires_dt < datetime.now(timezone.utc):
            return None
    max_r = row.get("max_redemptions")
    if max_r is not None and int(row.get("times_redeemed", 0)) >= int(max_r):
        return None
    return row


def create_subscription(org_id: str, plan_id: str, gstin: str | None = None,
                        billing_cycle: str = "monthly",
                        coupon_code: str | None = None,
                        coupon_offer_id: str | None = None,
                        trial_days: int = 0) -> dict[str, Any]:
    """Create a Razorpay subscription and return checkout details.

    ``billing_cycle`` may be ``"monthly"`` (default) or ``"annual"``.
    Annual pricing is ``monthly_price × 10`` (2 months free) via a separate
    Razorpay yearly plan. The price the customer sees at checkout is the
    annual price; Razorpay's hosted page renders the plan's configured
    amount and period.

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
    is_annual = billing_cycle == "annual"
    suffix = "_ANNUAL" if is_annual else ""
    plan_key = os.environ.get(f"RAZORPAY_PLAN_{plan_id.upper()}{suffix}")
    notes = {"org_id": org_id, "billing_cycle": billing_cycle}
    if gstin:
        notes["gstin"] = gstin
    if coupon_code:
        notes["coupon_code"] = coupon_code

    if _is_live() and plan_key:
        client = _get_client()
        payload: dict[str, object] = {
            "plan_id": plan_key,
            "total_count": 5 if is_annual else 12,
            "customer_notify": 1,
            "notes": notes,
        }
        if trial_days and trial_days > 0:
            # Card-on-signup with a free trial: defer the first charge to
            # now + trial_days. Razorpay only does a token authorization at
            # checkout (immediately refunded) for a future start_at — the
            # mandate/card is captured now, first real charge lands at start_at.
            payload["start_at"] = int(time.time()) + int(trial_days) * 86400
        if coupon_offer_id:
            payload["offer_id"] = coupon_offer_id
        sub = client.subscription.create(payload)
        return {
            "subscription_id": sub["id"],
            "short_url": sub.get("short_url", ""),
            "status": sub.get("status", "created"),
            "billing_cycle": billing_cycle,
        }

    if _is_live() and not plan_key:
        raise RuntimeError(
            f"RAZORPAY_PLAN_{plan_id.upper()}{suffix} not configured — cannot create subscription"
        )

    if not _sandbox_enabled():
        raise RuntimeError(
            "RAZORPAY_KEY_ID/SECRET not configured — cannot create live subscription"
        )

    price = plan.get("annual_price_inr" if is_annual else "price_inr", plan["price_inr"])
    period_label = "/ yr" if is_annual else "/ mo"
    note = f"Sandbox: would charge ₹{price}{period_label} for {plan['students']} students ({plan['name']})"
    if coupon_code:
        note += f"  [coupon: {coupon_code}]"
    return {
        "subscription_id": f"mock_sub_{org_id[:8]}",
        "short_url": "",
        "status": "created",
        "_is_sandbox": True,
        "billing_cycle": billing_cycle,
        "_note": note,
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


def get_plan_details(plan_id: str) -> dict[str, Any] | None:
    """Return plan details or None for unknown plan."""
    return PLANS.get(plan_id)


# ─── Overage computation ──────────────────────────────────────────

async def compute_overage(org_id: str, period_start: datetime, period_end: datetime) -> dict[str, Any]:
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
    # period_start/end may arrive as datetime (unit callers) OR as ISO strings
    # (the postgres_table layer returns TIMESTAMPTZ columns as strings, which is
    # how they reach us from the subscription.charged webhook snapshot).
    ps = period_start.isoformat() if hasattr(period_start, "isoformat") else str(period_start)
    pe = period_end.isoformat() if hasattr(period_end, "isoformat") else str(period_end)
    q = (await _atable("exam_sessions")
         .select("student_id", count="exact", distinct_on="student_id")
         .in_("teacher_id", org_teacher_ids)
         .gte("submitted_at", ps)
         .lt("submitted_at", pe)
         .execute())
    students_used = int(q.count or 0)
    overage = max(0, students_used - int(str(plan_limit)))
    amount = overage * price_per_student
    return {"students_used": students_used, "plan_limit": plan_limit,
            "overage_count": overage, "amount_inr": amount}


def razorpay_plan_key(plan_id: str, billing_cycle: str = "monthly") -> str | None:
    """Map an internal tier (e.g. 'growth') to the Razorpay plan id configured
    in the dashboard via RAZORPAY_PLAN_GROWTH etc. Razorpay's subscription APIs
    want THAT id, not our internal name.

    For annual billing, appends ``_ANNUAL`` to the env var name so e.g.
    ``RAZORPAY_PLAN_GROWTH_ANNUAL`` is read for the yearly plan."""
    suffix = "_ANNUAL" if billing_cycle == "annual" else ""
    return os.environ.get(f"RAZORPAY_PLAN_{(plan_id or '').upper()}{suffix}")


def compute_proration(old_plan: str, new_plan: str, period_start, period_end, now=None) -> int:
    """Prorated upgrade catch-up in INR for the rest of the current cycle:
    (new_price - old_price) * remaining/cycle.

    Returns 0 when it isn't an upgrade, the cycle window is unknown, or the
    cycle is already over. period_start/end may be datetime OR ISO string (the
    postgres_table layer returns TIMESTAMPTZ as a string), so coerce both.
    """
    from datetime import datetime as _dtmod, timezone as _tz

    def _aware(v):
        if v is None:
            return None
        if not isinstance(v, str) and hasattr(v, "isoformat"):
            dt = v
        else:
            try:
                dt = _dtmod.fromisoformat(str(v))
            except ValueError:
                return None
        return dt.replace(tzinfo=_tz.utc) if dt.tzinfo is None else dt

    ps, pe = _aware(period_start), _aware(period_end)
    if ps is None or pe is None:
        return 0
    now = _aware(now) or _dtmod.now(_tz.utc)
    cycle = (pe - ps).total_seconds()
    remaining = (pe - now).total_seconds()
    if cycle <= 0 or remaining <= 0:
        return 0
    new_price = int(PLANS.get(new_plan, {}).get("price_inr", 0))
    old_price = int(PLANS.get(old_plan, {}).get("price_inr", 0))
    diff = new_price - old_price
    if diff <= 0:
        return 0
    return round(diff * remaining / cycle)


def _create_overage_addon(client, sub_id: str, overage_count: int, net_amount_inr: int) -> str | None:
    """Create the Razorpay add-on for one overage charge. Raises on API failure —
    callers decide how to record/retry a failure. Shared by bill_cycle_overage
    (first attempt, inline on the subscription.charged webhook) and the
    overage retry sweeper (follow-up attempts on a previously-failed row), so
    a retry creates the exact same add-on shape as the original attempt."""
    addon = client.subscription.createAddon(str(sub_id), {
        "item": {
            "name": f"Overage: {overage_count} student{'s' if overage_count != 1 else ''}",
            "amount": net_amount_inr * 100,  # paise
            "currency": "INR",
        },
        "quantity": 1,
    })
    return str(addon.get("id", "")) or None


async def bill_cycle_overage(org_id: str, sub_row_before: dict[str, Any]) -> dict[str, Any]:
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

    # Gap #13: apply billing credit before creating the add-on.
    # ATOMIC debit: lock the org row (SELECT … FOR UPDATE) and read+write the
    # credit inside one transaction, so two concurrent overage charges for the
    # same org can't both read the same balance and double-consume it (the
    # read-then-write TOCTOU race). On ANY failure we charge the full amount —
    # never grant credit we didn't actually debit. Runs as system: this is a
    # background/webhook path with no request context, and organizations is
    # RLS-protected.
    amount = res["amount_inr"]
    org_credit = 0
    credit_consumed = 0
    net = amount
    try:
        from ..postgres_table import get_pool
        from .. import db_context as _dbctx
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _dbctx.apply_request_context(conn, force_system=True)
                # nosemgrep: asyncpg-sqli
                org_credit = int(await conn.fetchval(
                    "SELECT billing_credit_inr FROM organizations WHERE id = $1 FOR UPDATE",
                    oid) or 0)
                if org_credit > 0:
                    credit_consumed = min(org_credit, amount)
                    net = amount - credit_consumed
                    # nosemgrep: asyncpg-sqli
                    await conn.execute(
                        "UPDATE organizations SET billing_credit_inr = $1 WHERE id = $2",
                        org_credit - credit_consumed, oid)
    except Exception as exc:
        logger.warning("bill_cycle_overage: credit debit failed for org=%s: %s — charging full amount",
                       oid, exc)
        credit_consumed = 0
        net = amount

    # We hold the claim — now create the add-on. A failure here is recorded
    # ('failed', visible in the usage endpoint) but NEVER raised: the base
    # subscription.charged webhook must still return 200. The claim row blocks
    # an automatic retry from double-charging; a stuck 'failed' row is settled
    # manually (follow-up #4c: safe retry of failed overage add-ons).
    sub_id = sub_row_before.get("razorpay_subscription_id") or ""
    addon_id = None
    if net <= 0:
        # Fully comped by credit — no add-on to create.
        status = "comped"
    else:
        status = "charged"
        try:
            if sub_id:
                addon_id = _create_overage_addon(client, sub_id, res["overage_count"], net)
        except Exception:
            logger.exception("Razorpay add-on creation failed for org=%s", oid)
            status = "failed"

    # Settle the claim with the outcome.
    try:
        await _atable("overage_charges").update(
            {"status": status, "razorpay_addon_id": addon_id,
             "credit_applied_inr": credit_consumed}
        ).eq("org_id", oid).eq("period_start", ps_iso).execute()
    except Exception as exc:
        logger.warning("bill_cycle_overage: status update failed for org=%s: %s", oid, exc)

    if status in ("charged", "comped"):
        try:
            await record_billing_event(
                event_id="",
                org_id=oid,
                event_type="overage.addon",
                amount=credit_consumed if status == "comped" else net,
                status=status,
                razorpay_subscription_id=sub_id,
                payload={"overage_charges": {**claim, "razorpay_addon_id": addon_id, "status": status,
                         "credit_applied_inr": credit_consumed},
                         "subscription_id": sub_id},
            )
        except Exception as exc:
            logger.warning("bill_cycle_overage: billing_event record failed for org=%s: %s", oid, exc)

    return {"status": status, "overage_count": res["overage_count"], "amount_inr": net}


# ─── Entitlement: single source of truth ──────────────────────────────
# The enforced cap (organizations.max_students) is a PROJECTION of the
# subscription's plan+status, written ONLY through reconcile_org_entitlement.
# No other code path may touch max_students — that's what caused the drift.

FREE_CAP: int = int(PLANS.get("starter", {}).get("students", 30))  # un-entitled floor
_PLAN_CAPS: dict[str, int] = {p: int(v.get("students", FREE_CAP)) for p, v in PLANS.items()}

# Statuses that grant access. `past_due` = in Razorpay's retry/dunning window
# (we keep service during grace). `cancelling` = cancelled but still inside the
# paid period. `trialing` = active trial. `created` is NOT here on purpose:
# a subscription the customer never authorised must not grant entitlement.
ENTITLING_STATUSES = frozenset({
    "trialing", "authenticated", "active", "past_due", "cancelling",
})
# Every status the app + Razorpay can legitimately write (keep in sync with the
# subscriptions_status_check constraint, phase144). A status OUTSIDE this set
# reaching reconcile means Razorpay introduced a lifecycle state we don't map —
# reconcile would silently drop the org to FREE_CAP (students locked out). We
# warn loudly instead of failing silently (same enum-drift class as the outage).
_KNOWN_STATUSES = ENTITLING_STATUSES | frozenset({
    "created", "pending", "grace", "halted", "paused",
    "cancelled", "completed", "expired", "none",
})


async def reconcile_org_entitlement(org_id: str) -> int:
    """Recompute and persist organizations.max_students from the org's
    subscription state. The ONLY writer of max_students. Returns the cap.

    If ``max_students_override`` is set on the org, it takes precedence
    over the plan-derived cap — this is how a support override survives
    the next reconcile cycle. Setting the override back to NULL reverts
    to the plan-derived behaviour."""
    from ..database import async_table as _atable
    oid = str(org_id)
    rows = (await _atable("subscriptions").select("plan,status")
            .eq("org_id", oid).limit(1).execute()).data or []
    if rows:
        plan = (rows[0].get("plan") or "starter").strip().lower()
        status = (rows[0].get("status") or "").strip().lower()
    else:
        plan, status = "starter", "none"
    if status not in _KNOWN_STATUSES:
        logger.warning(
            "reconcile_org_entitlement: UNKNOWN subscription status %r for org=%s — "
            "treating as un-entitled (FREE_CAP). If Razorpay added a status, map it in "
            "ENTITLING_STATUSES and the subscriptions_status_check constraint.",
            status, oid)
    entitled = status in ENTITLING_STATUSES
    plan_cap = _PLAN_CAPS.get(plan, FREE_CAP) if entitled else FREE_CAP
    # Gap #13: max_students_override takes precedence over the
    # plan-derived cap.  NULL = use plan, non-NULL = use override.
    org_rows = (await _atable("organizations").select("max_students_override")
                .eq("id", oid).limit(1).execute()).data or []
    override = org_rows[0].get("max_students_override") if org_rows else None
    cap = int(override) if override is not None else plan_cap
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
                               payload: dict[str, Any] | None = None) -> bool:
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
