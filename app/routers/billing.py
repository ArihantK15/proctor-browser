"""Billing router — Razorpay subscription management."""

from typing import Any
from ..log_safe import safe
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException
from ..auth import require_admin
from ..database import async_table as _atable
from ..models import SessionStatus
from ..limiter import limiter
from ..services.sessions import PLAN_LIMITS
from ..constants import PLANS
from ..services.billing import (
    create_subscription as billing_create_subscription,
    validate_coupon as billing_validate_coupon,
    verify_webhook,
    _get_client,
    _is_live,
    reconcile_org_entitlement,
    record_billing_event,
    billing_event_seen,
    ENTITLING_STATUSES,
    compute_proration,
    razorpay_plan_key,
)
from .. import cache as _cache
from ..auth.admin_auth import require_reauth_or_403

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _require_billing_admin(teacher: dict) -> str:
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    return str(org_id)


def _validate_paid_plan(plan_id: str) -> dict[str, Any]:
    plan_id = (plan_id or "").strip().lower()
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {plan_id}")
    plan = PLANS[plan_id]
    price = plan.get("price_inr")
    if int(price or 0) <= 0:
        raise HTTPException(status_code=400, detail="This plan requires a sales conversation.")
    return plan


def _invalidate_billing_cache(org_id: str):
    """Synchronously delete billing-related cache keys.

    Redis deletes are sub-millisecond on localhost and bounded by
    socket_timeout=2s on failure, so calling the sync client from an
    async handler is acceptable here. Use asyncio.to_thread if this
    ever becomes a bottleneck.
    """
    if not _cache or not org_id:
        return
    _cache.delete(f"org_subscription:{org_id}")
    _cache.delete(f"org_limits:{org_id}")


async def _bump_coupon_redemption(coupon_code: str) -> None:
    """Atomically increment a coupon's ``times_redeemed``, capped at
    ``max_redemptions``. Best-effort — never raises into the caller.

    Uses a single conditional UPDATE rather than read-modify-write: two
    concurrent redemptions of the same code would otherwise both read
    ``times_redeemed=N`` and both write ``N+1``, losing an increment and
    letting a capped promo coupon be redeemed past its cap (validate_coupon
    gates on ``times_redeemed < max_redemptions``). The predicate under the
    row write-lock is the atomic primitive — same shape as
    ``invites._claim_and_bump_cap_postgres``. The PostgresTable adapter can't
    express ``col = col + 1``, so this uses a raw asyncpg connection.
    """
    try:
        from ..postgres_table import get_pool
        from .. import db_context as _dbctx
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # RLS: scope this raw transaction like PostgresTable does
                # (no-op while RLS_SESSION_CONTEXT is off). coupons is RLS-gated
                # under procta_app; create_subscription runs under an
                # authenticated billing-admin request, so the request context
                # (or the system fallback) applies.
                await _dbctx.apply_request_context(conn)
                # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
                new_count = await conn.fetchval(
                    "UPDATE coupons SET times_redeemed = times_redeemed + 1 "
                    "WHERE code = $1 "
                    "AND (max_redemptions IS NULL OR times_redeemed < max_redemptions) "
                    "RETURNING times_redeemed",
                    coupon_code.lower(),
                )
        if new_count is None:
            # Raced past the cap (or the code was deleted between validate and
            # here): the Razorpay subscription already exists so the discount
            # stands, but the counter stays accurate and the next
            # validate_coupon correctly rejects further redemptions.
            logger.warning(
                "Coupon %s not incremented — already at its redemption cap "
                "(concurrent race) or no longer exists.", coupon_code)
    except Exception as e:
        logger.warning("Failed to increment coupon redemption for %s: %s", coupon_code, e)


@router.get("/api/v1/billing/plans")
@limiter.limit("30/minute")
async def list_plans(request: Request):
    """Return available plans — public, no auth needed.
    Each plan includes ``annual_price_inr`` (monthly × 10, 2 months free)
    and ``annual_savings_inr`` (savings vs 12 months of monthly pricing)."""
    return {
        "plans": [
            {
                "id": pid,
                "name": p["name"],
                "price_inr": p["price_inr"],
                "annual_price_inr": p.get("annual_price_inr", 0),
                "annual_savings_inr": int(p["price_inr"]) * 12 - int(p.get("annual_price_inr", 0) or int(p["price_inr"]) * 12),
                "students": p["students"],
                "description": p["desc"],
            }
            for pid, p in PLANS.items()
        ]
    }


def _best_effort_cancel_razorpay(sub_id: str, *, reason: str) -> None:
    """Cancel a Razorpay subscription IMMEDIATELY, best-effort. Used on error/
    cleanup paths to avoid an orphaned or duplicate provider subscription that
    would keep charging the customer — a DB write that failed after the sub was
    created (#6), or a stale 'created' sub left by an abandoned checkout that a
    retry has now superseded (#17). Never raises."""
    sub_id = (sub_id or "").strip()
    if not sub_id or sub_id.startswith("mock_"):
        return
    client = _get_client()
    if not client:
        return
    try:
        client.subscription.cancel(sub_id, {"cancel_at_cycle_end": 0})
        logger.info("Best-effort cancelled Razorpay sub %s (%s)", sub_id, reason)
    except Exception as e:
        logger.error("Best-effort Razorpay cancel failed for sub=%s (%s): %s", sub_id, reason, e)


@router.post("/api/v1/billing/create-subscription")
@limiter.limit("5/minute")
async def create_subscription(body: dict, request: Request):
    """Create a Razorpay subscription for the org.

    Body: { "plan_id": "growth", "billing_cycle": "monthly"|"annual" }
    ``billing_cycle`` defaults to ``"monthly"``.
    Returns Razorpay checkout URL.
    """
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    plan_id = (body.get("plan_id") or "").strip().lower()
    billing_cycle = (body.get("billing_cycle") or "monthly").strip().lower()
    if billing_cycle not in ("monthly", "annual"):
        raise HTTPException(status_code=400,
            detail="billing_cycle must be 'monthly' or 'annual'.")
    if billing_cycle == "annual" and plan_id in ("enterprise",):
        raise HTTPException(status_code=400,
            detail="Enterprise plans are custom-priced — annual billing is managed by sales.")

    # Rejects unknown plans AND zero-price tiers (Enterprise): a ₹0 plan must
    # never enter self-serve checkout — in sandbox it would mint a free
    # subscription, and Enterprise is a contact-sales/manual-contract flow.
    _validate_paid_plan(plan_id)

    # Block creating a second subscription while one is already entitling. We
    # keep ONE subscription row per org, so a new Razorpay subscription would
    # overwrite (and orphan) the live one — and an abandoned switch would then
    # strand the original sub's renewal webhooks as "unknown". The customer
    # cancels the current plan first (access persists to period end) or talks
    # to sales to change plans.
    existing_sub = (await _atable("subscriptions").select("status,razorpay_subscription_id")
                    .eq("org_id", str(org_id)).limit(1).execute()).data or []
    if existing_sub and (existing_sub[0].get("status") or "").strip().lower() in ENTITLING_STATUSES:
        raise HTTPException(status_code=409,
            detail="You already have an active plan. Cancel it first — you keep "
                   "access until the end of your billing period — or contact "
                   "sales to change plans.")

    # Card-on-signup: the FIRST subscription a billing owner sets up (no Razorpay
    # mandate yet) gets a 14-day deferred first charge — card captured now, first
    # real charge at trial end. A later plan change/re-subscribe charges
    # immediately. Only in the card-on-signup model; legacy upgrades from the
    # free trial keep charging immediately (trial_days=0).
    from ..constants import CARD_ON_SIGNUP_ENFORCED, TRIAL_DAYS
    _first_setup = not ((existing_sub[0].get("razorpay_subscription_id") or "").strip() if existing_sub else False)
    trial_days = TRIAL_DAYS if (CARD_ON_SIGNUP_ENFORCED and _first_setup) else 0

    # A previous abandoned checkout can leave a non-entitling 'created' Razorpay
    # sub on file (an entitling sub would have 409'd above). Capture its id so we
    # can cancel it after the replacement is created — otherwise that old mandate
    # keeps charging the customer alongside the new one (#17, double-charge).
    _stale_rzp_sub_id = ""
    if existing_sub and (existing_sub[0].get("status") or "").strip().lower() == "created":
        _stale_rzp_sub_id = (existing_sub[0].get("razorpay_subscription_id") or "").strip()

    # Optional GSTIN for GST-compliant Razorpay invoices (Indian B2B).
    gstin = (body.get("gstin") or "").strip().upper()
    if gstin and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin):
        raise HTTPException(status_code=400, detail="Invalid GSTIN format (expected 15 characters).")

    # Optional coupon code.
    coupon_code = (body.get("coupon_code") or "").strip()
    coupon_offer_id: str | None = None
    if coupon_code:
        coupon = await billing_validate_coupon(coupon_code)
        if not coupon:
            raise HTTPException(status_code=400,
                detail="Invalid or expired coupon code.")
        coupon_offer_id = coupon["razorpay_offer_id"]

    try:
        result = billing_create_subscription(str(org_id), plan_id, gstin=gstin or None,
                                              billing_cycle=billing_cycle,
                                              coupon_code=coupon_code or None,
                                              coupon_offer_id=coupon_offer_id,
                                              trial_days=trial_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Billing misconfigured: %s", e)
        raise HTTPException(status_code=503, detail="Billing unavailable: payment credentials not configured.")
    except Exception as e:
        logger.exception("Failed to create subscription")
        raise HTTPException(status_code=500, detail="Failed to create subscription")

    if result.get("_is_sandbox"):
        logger.info("Sandbox subscription: %s", result["_note"])

    # Record the subscription INTENT only — do NOT grant entitlement here.
    # The Razorpay subscription is in "created" state (customer hasn't
    # authorised payment yet). Entitlement (max_students) is granted ONLY when
    # subscription.activated / .charged arrives, via reconcile_org_entitlement.
    # We deliberately do NOT lower an existing active plan's cap on create
    # (upgrade path): reconcile is not called here, so the current cap persists
    # until the new subscription actually activates.
    try:
        existing = await _atable("subscriptions").select("id").eq("org_id", str(org_id)).limit(1).execute()
        sub_data = {
            "plan": plan_id,
            "status": (result.get("status") or "created").strip().lower(),
            "razorpay_subscription_id": result["subscription_id"],
            "billing_cycle": billing_cycle,
        }
        sub = (existing.data or [None])[0]
        if sub:
            await _atable("subscriptions").update(sub_data).eq("org_id", str(org_id)).execute()
        else:
            await _atable("subscriptions").insert({
                "org_id": str(org_id),
                **sub_data,
            }).execute()
        if gstin:
            await _atable("organizations").update({"gstin": gstin}).eq("id", str(org_id)).execute()
        _invalidate_billing_cache(str(org_id))
        # The replacement is now recorded — stop the superseded abandoned-checkout
        # sub from charging the customer (#17).
        if _stale_rzp_sub_id and _stale_rzp_sub_id != result.get("subscription_id"):
            _best_effort_cancel_razorpay(_stale_rzp_sub_id, reason="superseded_by_retry")
    except Exception as e:
        logger.error("Failed to update subscription in DB after provider subscription creation: %s", e)
        # Compensating cancel: the Razorpay sub was created but we couldn't record
        # it. Cancel it so the customer isn't left with an orphan paid subscription
        # that charges them for a plan we never provisioned (#6).
        _best_effort_cancel_razorpay(result.get("subscription_id", ""), reason="db_write_failed")
        raise HTTPException(
            status_code=500,
            detail="Subscription was created by the payment provider, but could not be recorded. Please contact support.",
        )

    # Increment coupon redemption after the subscription is created.
    if coupon_code:
        await _bump_coupon_redemption(coupon_code)

    return result


def _epoch_to_iso(value) -> str | None:
    """Razorpay sends period timestamps as Unix epoch seconds. Convert to an
    ISO-8601 UTC string for our timestamptz columns. Pass-through ISO strings."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _effective_due(value) -> bool:
    """True when a scheduled_plan_effective_at (datetime or ISO string — the
    postgres_table layer returns TIMESTAMPTZ as a string) is at or before now."""
    if not value:
        return False
    eff = value
    if isinstance(eff, str):
        try:
            eff = datetime.fromisoformat(eff)
        except ValueError:
            return False
    if getattr(eff, "tzinfo", None) is None:
        eff = eff.replace(tzinfo=timezone.utc)
    return eff <= datetime.now(timezone.utc)


async def _notify_payment_issue(org_id: str) -> None:
    """Email the org admin that a renewal payment needs attention (dunning).

    Delivery order:
      1. organizations.billing_email (dedicated billing contact, if set)
      2. First teacher with org_role='admin' in the org (fallback)
    Respects the recipient's notification_prefs.billing opt-out.
    """
    try:
        to_email: str | None = None
        to_name: str = ""
        recipient_teacher_id: str | None = None
        org_row = (await _atable("organizations").select("billing_email")
                    .eq("id", str(org_id)).limit(1).execute()).data or []
        if org_row and org_row[0].get("billing_email"):
            to_email = org_row[0]["billing_email"]
            # Look up the teacher who owns this billing_email
            owner = (await _atable("teachers").select("id,full_name")
                     .eq("email", to_email).limit(1).execute()).data or []
            if owner:
                recipient_teacher_id = str(owner[0]["id"])
                to_name = owner[0].get("full_name", "")
        if not to_email:
            admin_rows = (await _atable("teachers").select("id,email,full_name")
                          .eq("org_id", str(org_id)).eq("org_role", "admin")
                          .limit(1).execute()).data or []
            if admin_rows:
                to_email = admin_rows[0]["email"]
                recipient_teacher_id = str(admin_rows[0]["id"])
                to_name = admin_rows[0].get("full_name", "")
        if to_email and recipient_teacher_id:
            from ..services.notification_prefs import teacher_wants
            if not await teacher_wants(recipient_teacher_id, "billing"):
                logger.info("Billing notification suppressed by teacher pref (org=%s)", safe(org_id))
                return
        if to_email:
            from ..emailer import send_payment_failed_notification
            send_payment_failed_notification(
                to_email=to_email,
                to_name=to_name,
            )
    except Exception as e:
        logger.warning("Payment-issue email failed for org=%s: %s", safe(org_id), safe(e))


# Subscription event → (our status). Entitlement is reconciled afterward, never
# set inline. GRANT events entitle; DOWNGRADE events drop to the free cap.
_SUB_GRANT = {
    "subscription.authenticated": "authenticated",
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.resumed": "active",
}
_SUB_DOWNGRADE = {
    "subscription.halted": "halted",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "completed",
    "subscription.paused": "paused",
}


# ── GET /api/v1/billing/validate-coupon ────────────────────────────
@router.get("/api/v1/billing/validate-coupon")
@limiter.limit("30/minute")
async def validate_coupon(request: Request, code: str = ""):
    """Preview coupon validity for the UI — no redemption side-effect.

    Public endpoint (no auth required) so the checkout page can show
    the discount before the user is logged in or has an org.
    Returns {valid: bool, description, ...} for valid; {valid: false}
    for invalid / expired / exhausted.
    """
    coupon = await billing_validate_coupon(code)
    if not coupon:
        return {"valid": False}
    return {
        "valid": True,
        "description": coupon.get("description") or "",
        "razorpay_offer_id": coupon["razorpay_offer_id"],
    }


@router.post("/api/v1/webhooks/razorpay")
@limiter.limit("60/minute")
async def razorpay_webhook(request: Request):
    """Handle Razorpay SUBSCRIPTION webhooks (recurring-subscriptions model).

    Lifecycle → entitlement (organizations.max_students is reconciled from
    subscription state by reconcile_org_entitlement — never written inline):
      authenticated / activated / charged / resumed → grant plan, renew period
      pending  → past_due (Razorpay is retrying the charge): KEEP access during
                 the grace window and email the admin — no instant downgrade
      halted   → retries exhausted → downgrade
      cancelled / completed / paused → downgrade

    Idempotency is DB-durable: each processed event is appended to
    billing_events with the Razorpay event.id UNIQUE. A retry of an already-
    recorded event short-circuits to 200. Signature is verified FIRST; the
    idempotency read runs only after. On any failure we return 500 and do NOT
    record, so Razorpay retries.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("event", "")
    payload = event.get("payload", {})
    event_id = str(event.get("id") or "").strip()

    if not event_id:
        # Razorpay almost always sends event.id; on a rare delivery without one,
        # synthesize a STABLE dedup key from the event's own identifying fields so
        # a redelivery is still caught. This matters because subscription.charged
        # triggers real overage billing — reprocessing a retry would double-charge.
        # A genuinely distinct charge carries a distinct payment id, so distinct
        # events are NOT collapsed together.
        _sub = (payload.get("subscription", {}).get("entity", {}) or {}).get("id")
        _pay = (payload.get("payment", {}).get("entity", {}) or {}).get("id")
        if _sub or _pay:
            event_id = f"synth:{event_type}:{_sub or ''}:{_pay or ''}"
            logger.warning("Razorpay webhook missing event.id — synthesized dedup key (type=%s)",
                           safe(event_type))
        else:
            logger.warning("Razorpay webhook missing event.id and no stable id — idempotency weakened (type=%s)",
                           safe(event_type))

    # DB-durable idempotency: a retry of an already-recorded event is a no-op.
    if event_id and await billing_event_seen(event_id):
        logger.info("Razorpay webhook %s already processed (event=%s) — 200",
                    safe(event_type), safe(event_id))
        return {"status": "duplicate", "event_id": event_id}

    sub_data = payload.get("subscription", {}).get("entity", {}) or {}
    sub_id = sub_data.get("id")

    # Invoice events (invoice.paid, invoice.expired, …) carry the invoice under
    # payload.invoice.entity — NOT payload.subscription.entity — so sub_id above
    # is empty for them. Record each to the ledger keyed by the invoice's
    # subscription_id + org so list_invoices can surface them as a durable
    # fallback when Razorpay's live invoice API is momentarily unreachable.
    # Invoice events NEVER change entitlement — only subscription.* events do.
    if not sub_id and event_type.startswith("invoice."):
        inv_entity = payload.get("invoice", {}).get("entity", {}) or {}
        inv_sub_id = inv_entity.get("subscription_id")
        inv_org_id = None
        if inv_sub_id:
            _inv_rows = (await _atable("subscriptions").select("org_id")
                         .eq("razorpay_subscription_id", inv_sub_id).limit(1).execute()).data or []
            if _inv_rows:
                inv_org_id = str(_inv_rows[0]["org_id"])
        pay_entity = payload.get("payment", {}).get("entity", {}) or {}
        await record_billing_event(
            event_id=event_id, org_id=inv_org_id, event_type=event_type,
            status="invoice", razorpay_subscription_id=inv_sub_id,
            razorpay_payment_id=pay_entity.get("id"),
            amount=inv_entity.get("amount"), currency=inv_entity.get("currency") or "INR",
            payload=event)
        logger.info("Recorded %s for sub=%s org=%s",
                    safe(event_type), safe(inv_sub_id), safe(inv_org_id))
        return {"status": "ok", "kind": "invoice"}

    if not sub_id:
        # Non-subscription event (e.g. a stray payment.captured) — log to the
        # ledger and ignore. We are subscriptions-only; Orders are deprecated.
        await record_billing_event(event_id=event_id, org_id=None, event_type=event_type,
                                   status="ignored_no_sub", payload=event)
        return {"status": "ignored"}

    # current_period_start/end + razorpay_subscription_id are needed for the
    # _sub_before snapshot that bill_cycle_overage reads (the just-ended cycle
    # + the sub to add the overage add-on to). Without them overage billing
    # silently no-ops.
    db_sub = (await _atable("subscriptions")
              .select("id,org_id,plan,status,past_due_since,"
                      "current_period_start,current_period_end,razorpay_subscription_id,"
                      "scheduled_plan,scheduled_plan_effective_at")
              .eq("razorpay_subscription_id", sub_id).limit(1).execute()).data or []
    if not db_sub:
        logger.warning("Unknown Razorpay subscription %s (event=%s)",
                       safe(sub_id), safe(event_type))
        # A GRANT for a sub we don't have yet is almost certainly a webhook
        # outrunning our own create_subscription DB commit (Razorpay can deliver
        # subscription.authenticated/activated within milliseconds). Recording
        # it as processed + 200 would dedup the activation away FOREVER, leaving
        # the org paid-but-unentitled. Return a retryable 500 and DELIBERATELY
        # do NOT record the event_id, so the redelivery reprocesses once the row
        # exists. Non-granting events for a sub we never tracked are safe to drop.
        if event_type in _SUB_GRANT:
            raise HTTPException(status_code=500,
                detail="Subscription not yet on record — will retry")
        await record_billing_event(event_id=event_id, org_id=None, event_type=event_type,
                                   status="ignored_unknown_sub",
                                   razorpay_subscription_id=sub_id, payload=event)
        return {"status": "ignored"}
    row = db_sub[0]
    org_id = str(row["org_id"])
    # Snapshot the pre-update subscription row so bill_cycle_overage can
    # read the just-ended cycle's period_start/period_end.
    _sub_before = dict(row)

    try:
        updates: dict = {}
        newly_past_due = False
        if event_type in _SUB_GRANT:
            updates["status"] = _SUB_GRANT[event_type]
            updates["past_due_since"] = None
            if sub_data.get("current_start"):
                updates["current_period_start"] = _epoch_to_iso(sub_data.get("current_start"))
            if sub_data.get("current_end"):
                updates["current_period_end"] = _epoch_to_iso(sub_data.get("current_end"))
            # Apply a scheduled plan change (downgrade) once its effective time
            # has passed. Done on the renewal charge, in the SAME update so the
            # following reconcile_org_entitlement lowers the cap — and #4's
            # bill_cycle_overage then bills any students over the new cap.
            if event_type == "subscription.charged":
                _sched = (_sub_before.get("scheduled_plan") or "").strip().lower() or None
                if _sched and _effective_due(_sub_before.get("scheduled_plan_effective_at")):
                    updates["plan"] = _sched
                    updates["scheduled_plan"] = None
                    updates["scheduled_plan_effective_at"] = None
                    logger.info("Applied scheduled plan change org=%s -> %s", org_id, _sched)
            outcome = "grant"
        elif event_type == "subscription.pending":
            # Renewal charge failed; Razorpay keeps retrying. KEEP access (grace)
            # and flag past_due so the dashboard/admin can act.
            updates["status"] = "past_due"
            # Notify only on the actual transition INTO past_due. The event_id
            # is recorded last (so an unrecorded event reprocesses on retry —
            # see the docstring); a redelivery re-runs this block, and without
            # this guard it would email the admin a duplicate payment-issue
            # notice. The status/reconcile side effects are already idempotent.
            newly_past_due = not row.get("past_due_since")
            if newly_past_due:
                updates["past_due_since"] = datetime.now(timezone.utc).isoformat()
            outcome = "grace"
        elif event_type in _SUB_DOWNGRADE:
            updates["status"] = _SUB_DOWNGRADE[event_type]
            outcome = "downgrade"
        else:
            await record_billing_event(event_id=event_id, org_id=org_id, event_type=event_type,
                                       status="ignored_unhandled",
                                       razorpay_subscription_id=sub_id, payload=event)
            return {"status": "ignored"}

        upd = await _atable("subscriptions").update(updates).eq("id", row["id"]).execute()
        if not (upd.data or []):
            raise RuntimeError(f"{event_type} DB write returned no data")
        await reconcile_org_entitlement(org_id)
        _invalidate_billing_cache(org_id)
        # Overage billing (Gap #4): for subscription.charged events, compute
        # and charge for the just-ended cycle's overage.  Best-effort: a
        # failure here never fails the webhook (Razordpay must get its 200).
        if event_type == "subscription.charged":
            try:
                from ..services.billing import bill_cycle_overage
                await bill_cycle_overage(org_id, _sub_before)
            except Exception as exc:
                logger.warning("Overage billing failed for org=%s: %s", safe(org_id), safe(exc))
        if outcome == "grace" and newly_past_due:
            await _notify_payment_issue(org_id)
        logger.info("Webhook %s → org=%s status=%s (%s)",
                    safe(event_type), safe(org_id), safe(updates.get("status")), outcome)
    except Exception as e:
        logger.error("Webhook %s failed for org=%s sub=%s: %s",
                     safe(event_type), safe(org_id), safe(sub_id), safe(e))
        # NOT recorded → 500 → Razorpay retries this delivery.
        raise HTTPException(status_code=500, detail="Webhook processing failed — will retry")

    # Capture the actual charge from the payment entity (present on
    # subscription.charged) so the ledger is a real financial record, not just
    # a state log. Falls back to the subscription's nominal amount.
    pay_entity = payload.get("payment", {}).get("entity", {}) or {}
    await record_billing_event(
        event_id=event_id, org_id=org_id, event_type=event_type, status=outcome,
        razorpay_subscription_id=sub_id, razorpay_payment_id=pay_entity.get("id"),
        amount=pay_entity.get("amount") or sub_data.get("amount") or None,
        currency=pay_entity.get("currency") or "INR", payload=event)
    return {"status": "ok"}


@router.post("/api/v1/billing/cancel")
@limiter.limit("5/minute")
async def cancel_subscription(request: Request):
    """Cancel the org's Razorpay subscription at the end of the current period.

    Uses Razorpay's `cancel_at_cycle_end=1` so the subscription stays active
    until the period expires — the teacher doesn't lose access immediately.
    In sandbox mode (no Razorpay client), just marks the DB row as cancelled.
    """
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)  # admin/superadmin only

    sub = await _atable("subscriptions").select("id,razorpay_subscription_id,status,current_period_end")\
        .eq("org_id", str(org_id)).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="No active subscription found")

    sub_row = sub.data[0]
    if sub_row.get("status") in ("cancelling", "cancelled", "expired"):
        raise HTTPException(status_code=409, detail="Subscription is already cancelled or expiring")

    razorpay_sub_id = sub_row.get("razorpay_subscription_id", "")

    client = _get_client()
    if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
        try:
            client.subscription.cancel(razorpay_sub_id, {"cancel_at_cycle_end": 1})
            logger.info("Subscription %s cancelled at cycle end for org=%s", razorpay_sub_id, org_id)
        except Exception as e:
            logger.error("Razorpay cancel failed for sub=%s: %s", razorpay_sub_id, e)
            raise HTTPException(status_code=502, detail="Failed to cancel with payment provider")
    else:
        logger.info("Sandbox: cancelling sub for org=%s without Razorpay API call", org_id)

    await _atable("subscriptions").update({"status": "cancelling"})\
        .eq("id", sub_row["id"]).execute()
    _invalidate_billing_cache(str(org_id))

    return {"ok": True, "message": "Subscription cancelled. You retain access until the end of your billing period."}


@router.post("/api/v1/billing/reactivate")
@limiter.limit("5/minute")
async def reactivate_subscription(request: Request):
    """Reactivate a subscription that was cancelled at cycle end.

    Reverses a prior cancel-at-cycle-end by telling Razorpay to reset the
    flag, then restores the subscription status to 'active' and reconciles
    entitlement. Only works while the subscription is still in its current
    billing period (status == 'cancelling'); once the period expires and
    Razorpay delivers subscription.cancelled, the org must create a fresh
    subscription.
    """
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)

    sub = await _atable("subscriptions").select("id,razorpay_subscription_id,status,plan")\
        .eq("org_id", str(org_id)).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="No subscription found")

    sub_row = sub.data[0]
    if sub_row.get("status") != "cancelling":
        raise HTTPException(status_code=409,
            detail="Only a subscription cancelled at cycle end (status 'cancelling') can be reactivated. "
                   "Expired subscriptions should create a new subscription.")

    razorpay_sub_id = sub_row.get("razorpay_subscription_id", "")

    client = _get_client()
    if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
        try:
            client.subscription.update(razorpay_sub_id, {"cancel_at_cycle_end": 0})
            logger.info("Subscription %s reactivated for org=%s", razorpay_sub_id, org_id)
        except Exception as e:
            logger.error("Razorpay reactivate failed for sub=%s: %s", razorpay_sub_id, e)
            raise HTTPException(status_code=502, detail="Failed to reactivate with payment provider")
    else:
        logger.info("Sandbox: reactivating sub for org=%s without Razorpay API call", org_id)

    await _atable("subscriptions").update({"status": "active", "past_due_since": None})\
        .eq("id", sub_row["id"]).execute()
    _invalidate_billing_cache(str(org_id))
    await reconcile_org_entitlement(str(org_id))

    return {"ok": True, "message": "Subscription reactivated."}


@router.post("/api/v1/billing/change-plan")
@limiter.limit("5/minute")
async def change_plan(request: Request):
    """Change the org's plan — upgrade now (prorated) or downgrade at cycle end.

    Body: { "plan_id": "growth" }

    Requires X-Reauth-Token header.

    Returns:
      - upgrade:   {"ok":true, "plan_id": "...", "proration_inr": <int>}
      - downgrade: {"ok":true, "plan_id": "...", "scheduled_plan_effective_at": "..."}
      - cancel scheduled change (same plan): {"ok":true, "cleared":true}
    """
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    # Parse body safely per §7.4 guardrail
    try:
        body = await request.json()
    except Exception:
        body = {}
    require_reauth_or_403(body, str(teacher["id"]), request=request)

    plan_id = (body.get("plan_id") or "").strip().lower()
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_id}")

    sub = await _atable("subscriptions").select(
        "id,plan,status,razorpay_subscription_id,scheduled_plan,scheduled_plan_effective_at,"
        "current_period_start,current_period_end"
    ).eq("org_id", str(org_id)).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="No active subscription found")
    sub_row = sub.data[0]
    current_plan = (sub_row.get("plan") or "").strip().lower()
    sub_status = (sub_row.get("status") or "").strip().lower()
    razorpay_sub_id = sub_row.get("razorpay_subscription_id", "")
    scheduled_plan = (sub_row.get("scheduled_plan") or "").strip().lower() or None
    scheduled_effective = sub_row.get("scheduled_plan_effective_at")

    # Enterprise is custom-priced and sales-managed — never self-serve into or
    # out of it (price_inr=0 would otherwise read as a "downgrade" and hand out
    # the 999999-student cap for free).
    if plan_id == "enterprise" or current_plan == "enterprise":
        raise HTTPException(status_code=400, detail="Enterprise plans are managed by sales")

    # Same-plan = cancel scheduled change if one exists, else no-op
    if plan_id == current_plan:
        if scheduled_plan and scheduled_effective:
            # Cancel the pending downgrade
            client = _get_client()
            if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
                try:
                    client.subscription.update(razorpay_sub_id, {
                        "plan_id": razorpay_plan_key(current_plan) or current_plan,
                        "schedule_change_at": "cycle_end",
                    })
                    logger.info("Cancelled schedule change for sub=%s, org=%s", razorpay_sub_id, org_id)
                except Exception as exc:
                    logger.error("Razorpay cancel-schedule failed for sub=%s: %s", razorpay_sub_id, exc)
                    raise HTTPException(status_code=502, detail="Failed to cancel schedule with payment provider")
            else:
                logger.info("Sandbox: clearing schedule for org=%s", org_id)

            await _atable("subscriptions").update({
                "scheduled_plan": None,
                "scheduled_plan_effective_at": None,
            }).eq("id", sub_row["id"]).execute()
            _invalidate_billing_cache(str(org_id))
            return {"ok": True, "cleared": True}
        else:
            raise HTTPException(status_code=409, detail="You are already on this plan.")

    # Must have an entitling status to switch
    if sub_status not in ENTITLING_STATUSES:
        raise HTTPException(status_code=409, detail="Your subscription must be active to change plans. Create a new subscription instead.")

    # Trialing subs have no billing cycle and no payment mandate yet, so a plan
    # change just switches the trial plan IMMEDIATELY — nothing to prorate or
    # charge, and no cycle end to schedule a downgrade against. (Scheduling a
    # cycle-end downgrade here was the "Invalid Date" bug: a trialing sub has no
    # current_period_end, so the effective date was bogus.) The real
    # upgrade/downgrade-with-billing flow below only applies to paying subs.
    if sub_status == "trialing":
        await _atable("subscriptions").update({
            "plan": plan_id,
            "scheduled_plan": None,
            "scheduled_plan_effective_at": None,
        }).eq("id", sub_row["id"]).execute()
        _invalidate_billing_cache(str(org_id))
        await reconcile_org_entitlement(str(org_id))
        return {"ok": True, "plan_id": plan_id, "immediate": True}

    current_price = int(PLANS.get(current_plan, {}).get("price_inr", 0))
    new_price = int(PLANS.get(plan_id, {}).get("price_inr", 0))
    is_upgrade = new_price > current_price

    client = _get_client()
    if is_upgrade:
        # Recurring rate flips to the new plan NEXT cycle; charge the prorated
        # difference for the REMAINDER of this cycle now via an add-on — don't
        # lean on Razorpay's own opaque subscription proration.
        proration_inr = compute_proration(
            current_plan, plan_id,
            sub_row.get("current_period_start"), sub_row.get("current_period_end"),
        )
        proration_charged = False
        if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
            try:
                client.subscription.update(razorpay_sub_id, {
                    "plan_id": razorpay_plan_key(plan_id) or plan_id,
                    "schedule_change_at": "cycle_end",
                })
            except Exception as exc:
                logger.error("Razorpay upgrade (plan switch) failed for sub=%s: %s", razorpay_sub_id, exc)
                raise HTTPException(status_code=502, detail="Failed to upgrade with payment provider")
            if proration_inr > 0:
                try:
                    client.subscription.createAddon(razorpay_sub_id, {
                        "item": {"name": f"Upgrade proration {current_plan}->{plan_id}",
                                 "amount": proration_inr * 100, "currency": "INR"},
                        "quantity": 1,
                    })
                    proration_charged = True
                except Exception as exc:
                    # Non-fatal: the plan still upgrades; the catch-up charge just
                    # didn't land. Surface it so the caller/ops can follow up.
                    logger.error("Razorpay proration add-on failed for sub=%s: %s", razorpay_sub_id, exc)
        else:
            logger.info("Sandbox: upgrading org=%s to %s (proration INR %s, no API call)",
                        org_id, plan_id, proration_inr)

        await _atable("subscriptions").update({
            "plan": plan_id,
            "scheduled_plan": None,
            "scheduled_plan_effective_at": None,
        }).eq("id", sub_row["id"]).execute()
        _invalidate_billing_cache(str(org_id))
        await reconcile_org_entitlement(str(org_id))

        return {"ok": True, "plan_id": plan_id, "proration_inr": proration_inr,
                "proration_charged": proration_charged}

    else:
        # Downgrade — schedule at cycle end
        effective_at = sub_row.get("current_period_end")
        if not effective_at:
            effective_at = datetime.now(timezone.utc) + timedelta(days=30)
        effective_str = effective_at.isoformat() if hasattr(effective_at, "isoformat") else str(effective_at)

        if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
            try:
                client.subscription.update(razorpay_sub_id, {
                    "plan_id": razorpay_plan_key(plan_id) or plan_id,
                    "schedule_change_at": "cycle_end",
                })
                logger.info("Scheduled downgrade sub=%s to %s at cycle end for org=%s",
                            razorpay_sub_id, plan_id, org_id)
            except Exception as exc:
                logger.error("Razorpay downgrade schedule failed for sub=%s: %s", razorpay_sub_id, exc)
                raise HTTPException(status_code=502, detail="Failed to schedule downgrade with payment provider")
        else:
            logger.info("Sandbox: scheduling downgrade for org=%s to %s", org_id, plan_id)

        await _atable("subscriptions").update({
            "scheduled_plan": plan_id,
            # Write the normalized ISO string (was writing the raw datetime/None,
            # which read back as an unparseable "Invalid Date" in the banner).
            "scheduled_plan_effective_at": effective_str,
        }).eq("id", sub_row["id"]).execute()
        _invalidate_billing_cache(str(org_id))

        return {"ok": True, "plan_id": plan_id, "scheduled_plan_effective_at": effective_str}


@router.post("/api/v1/billing/portal-link")
@limiter.limit("10/minute")
async def billing_portal_link(request: Request):
    """Generate a Razorpay customer portal session URL for managing
    payment methods, invoices, and billing details.

    In live mode, fetches the subscription from Razorpay to resolve the
    customer_id, then creates a portal session. In sandbox mode, returns
    a mock URL pointing to the Razorpay test dashboard.
    """
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)

    sub = await _atable("subscriptions").select("id,razorpay_subscription_id,status")\
        .eq("org_id", str(org_id)).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="No subscription found")

    sub_row = sub.data[0]
    razorpay_sub_id = sub_row.get("razorpay_subscription_id", "")

    client = _get_client()
    if client and razorpay_sub_id and not razorpay_sub_id.startswith("mock_"):
        try:
            rp_sub = client.subscription.fetch(razorpay_sub_id)
            customer_id = rp_sub.get("customer_id")
            if not customer_id:
                raise HTTPException(status_code=502,
                    detail="No customer associated with this subscription")
            result = client.post(
                f"/customers/{customer_id}/portal_sessions",
                {"redirect_url": "https://app.procta.net/dashboard#billing"},
            )
            session_url = (result or {}).get("session_url", "")
            if not session_url:
                raise HTTPException(status_code=502,
                    detail="Failed to create portal session")
            logger.info("Portal session created for org=%s customer=%s", org_id, customer_id)
            return {"portal_url": session_url}
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Portal session failed for sub=%s: %s", razorpay_sub_id, e)
            raise HTTPException(status_code=502,
                detail="Failed to create portal session with payment provider")
    else:
        logger.info("Sandbox: returning mock portal URL for org=%s", org_id)
        return {
            "portal_url": f"https://dashboard.razorpay.com/app/portal/mock_{org_id[:8]}",
            "sandbox": True,
            "note": "Sandbox mode — no live portal available",
        }


@router.get("/api/v1/billing/invoices")
@limiter.limit("10/minute")
async def list_invoices(request: Request):
    """Return invoice history for the org's Razorpay subscription.

    In sandbox mode, returns sample invoices.
    """
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)  # admin/superadmin only

    sub = await _atable("subscriptions").select("razorpay_subscription_id").eq("org_id", str(org_id)).limit(1).execute()
    sub_id = (sub.data or [{}])[0].get("razorpay_subscription_id")

    if not sub_id and _is_live():
        return {"invoices": []}

    if not sub_id or not _is_live():
        return {"invoices": [
            {"id": "mock_inv_01", "amount": 240000, "currency": "INR",
             "status": "paid", "created_at": "2026-04-01T00:00:00Z",
             "pdf_url": None, "description": "Growth plan — mocked (sandbox mode)"},
        ]}

    # P2.5: Razorpay returns `created_at` as Unix epoch seconds (int).
    # React clients pipe it into `new Date(value)` which expects ms or
    # ISO — the seconds value rendered as a 1970-era date. Convert
    # here so the contract is human-readable ISO 8601 in UTC.
    def _to_iso(epoch_secs):
        if not epoch_secs: return ""
        try:
            return datetime.fromtimestamp(int(epoch_secs), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return str(epoch_secs)  # last-resort: pass through

    try:
        client = _get_client()
        raw = client.invoice.all({"subscription_id": sub_id})
        invoices = [
            {"id": inv["id"], "amount": inv["amount"], "currency": inv["currency"],
             "status": inv["status"], "created_at": _to_iso(inv.get("created_at")),
             "pdf_url": inv.get("short_url") or inv.get("invoice_url") or None,
             "description": inv.get("description", "")}
            for inv in raw.get("items", [])
        ]
        return {"invoices": invoices}
    except Exception as e:
        logger.warning("Failed to fetch Razorpay invoices: %s", e)
        try:
            ev_rows = (await _atable("billing_events").select("payload,event_type,amount,status,created_at")
                       .eq("org_id", str(org_id)).eq("razorpay_subscription_id", sub_id)
                       .like("event_type", "invoice.%")
                       .order("created_at", desc=True).execute()).data or []
            invoices = []
            for ev in ev_rows:
                payload = ev.get("payload")
                if isinstance(payload, str):
                    import json as _json
                    payload = _json.loads(payload)
                # The real invoice fields live under payload.invoice.entity —
                # the prior code stopped at payload.invoice (the wrapper), so
                # every field below read empty even on a matched row.
                inv = (((payload or {}).get("payload", {}).get("invoice", {}) or {}).get("entity", {})
                       if isinstance(payload, dict) else {})
                if inv.get("id"):
                    invoices.append({
                        "id": inv["id"],
                        "amount": inv.get("amount", ev.get("amount", 0)),
                        "currency": inv.get("currency", "INR"),
                        "status": inv.get("status", ev.get("status", "unknown")),
                        "created_at": _to_iso(inv.get("created_at")),
                        "pdf_url": inv.get("short_url") or inv.get("invoice_url") or None,
                        "description": inv.get("description", f"invoice.{ev.get('event_type', 'unknown')}"),
                    })
            if invoices:
                return {"invoices": invoices, "_cached": True}
        except Exception as e2:
            logger.warning("Failed to reconstruct invoices from billing_events: %s", e2)
        return {"invoices": [], "error": "Failed to fetch invoices. Try again later."}


@router.get("/api/v1/billing/onboarding-status")
@limiter.limit("60/minute")
async def billing_onboarding_status(request: Request):
    """Whether the billing owner must set up a payment method before using the
    product (card-on-signup onboarding gate). True only when CARD_ON_SIGNUP_ENFORCED
    and the org's subscription is 'created' (never authorised — no Razorpay
    mandate). Always False for invited teachers and in the legacy free-trial mode,
    so the frontend gate stays dormant until card-on-signup is switched on."""
    teacher = await require_admin(request)
    from ..constants import CARD_ON_SIGNUP_ENFORCED
    org_id = teacher.get("org_id")
    if (not CARD_ON_SIGNUP_ENFORCED or not org_id
            or teacher.get("org_role") not in ("admin", "superadmin")):
        return {"needs_payment_setup": False}
    sub = (await _atable("subscriptions").select("status,razorpay_subscription_id")
           .eq("org_id", str(org_id)).limit(1).execute()).data or []
    status = (sub[0].get("status") or "").strip().lower() if sub else ""
    has_mandate = bool((sub[0].get("razorpay_subscription_id") or "").strip()) if sub else False
    return {
        "needs_payment_setup": (status == "created") and not has_mandate,
        "status": status,
    }


@router.get("/api/v1/billing/usage")
@limiter.limit("30/minute")
async def get_usage(request: Request):
    """Return current billing period usage for the org."""
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)  # admin/superadmin only

    # Get current plan limits
    sub = await _atable("subscriptions").select("plan,status").eq("org_id", str(org_id)).limit(1).execute()
    plan_id = (sub.data or [{}])[0].get("plan", "starter") if sub.data else "starter"
    sub_status = (sub.data or [{}])[0].get("status", "unknown") if sub.data else "unknown"
    # Enforced cap (organizations.max_students, kept current by
    # reconcile_org_entitlement) — NOT the nominal plan limit. For a cancelled
    # or past-grace org this is the free floor, which is the real cap to show.
    _org_row = (await _atable("organizations").select("max_students")
                .eq("id", str(org_id)).limit(1).execute()).data or []
    plan_limit = (int(_org_row[0]["max_students"])
                  if _org_row and _org_row[0].get("max_students") is not None
                  else PLAN_LIMITS.get(plan_id, 30))
    plan_def = PLANS.get(plan_id, {})
    base_price = plan_def.get("price_inr", 0)
    # `overage_price_inr` is the per-EXTRA-STUDENT charge above the
    # plan limit. Was previously using base price_inr (₹12,000 for
    # Growth, etc.) which produced absurd overage_amount values.
    # P1.4 fix — read the explicit per-student rate added to the
    # PLANS dict, fall back to 0 (= no overage charging) when missing.
    price_per_student = plan_def.get("overage_price_inr", 0)

    # Billing is ORG-scoped, so usage must span EVERY teacher in the org, not
    # just the calling admin — max_students is the org-wide cap, so the usage
    # shown beside it has to be the org-wide figure for the two to be
    # comparable (a per-admin count silently under-reports a multi-teacher org).
    _tid_rows = (await _atable("teachers").select("id")
                 .eq("org_id", str(org_id)).execute()).data or []
    org_teacher_ids = [str(r["id"]) for r in _tid_rows] or [str(teacher["id"])]

    # Count current period usage
    now_utc = datetime.now(timezone.utc)
    period_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Count distinct students who submitted this month
    student_count_q = await _atable("exam_sessions")\
        .select("student_id", count="exact", distinct_on="student_id")\
        .in_("teacher_id", org_teacher_ids)\
        .gte("submitted_at", period_start.isoformat())\
        .execute()
    students_used = int(student_count_q.count or 0)

    # Count total exam attempts this month
    attempts_q = await _atable("exam_sessions")\
        .select("session_key", count="exact")\
        .in_("teacher_id", org_teacher_ids)\
        .in_("status", (SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED))\
        .gte("submitted_at", period_start.isoformat())\
        .execute()
    exam_attempts = int(attempts_q.count or 0)

    overage = max(0, students_used - int(plan_limit))
    overage_amount = overage * price_per_student

    # Surface recent overage charges so the admin sees what's been billed.
    _oc_rows = (await _atable("overage_charges")
                .select("period_start,period_end,overage_count,amount_inr,status,created_at")
                .eq("org_id", str(org_id))
                .order("created_at", desc=True)
                .limit(5).execute()).data or []
    overage_charges = [
        {"period_start": r["period_start"], "period_end": r["period_end"],
         "overage_count": r["overage_count"], "amount_inr": r["amount_inr"],
         "status": r["status"], "created_at": r["created_at"]}
        for r in _oc_rows
    ]

    from ..constants import OVERAGE_BILLING_ENABLED as _OBE
    return {
        "plan_id": plan_id,
        "plan_limit": plan_limit,
        "plan_name": PLANS.get(plan_id, {}).get("name", "Unknown"),
        "base_price": base_price,
        "status": sub_status,
        "period_start": period_start.isoformat(),
        "students_used": students_used,
        "exam_attempts": exam_attempts,
        "overage": overage,
        "overage_amount": overage_amount,
        "overage_billing_enabled": _OBE,
        "overage_charges": overage_charges,
    }
