"""Billing router — Razorpay subscription management."""

from ..log_safe import safe
import json
import logging
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from ..auth import require_admin
from ..database import async_table as _atable
from ..models import SessionStatus
from ..limiter import limiter
from ..services.sessions import PLAN_LIMITS
from ..constants import PLANS
from ..services.billing import (
    create_subscription as billing_create_subscription,
    verify_webhook,
    _get_client,
    _is_live,
    reconcile_org_entitlement,
    record_billing_event,
    billing_event_seen,
    ENTITLING_STATUSES,
)
from .. import cache as _cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _require_billing_admin(teacher: dict) -> str:
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    return str(org_id)


def _validate_paid_plan(plan_id: str) -> dict:
    plan_id = (plan_id or "").strip().lower()
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {plan_id}")
    plan = PLANS[plan_id]
    if int(plan.get("price_inr") or 0) <= 0:
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


@router.get("/api/v1/billing/plans")
@limiter.limit("30/minute")
async def list_plans(request: Request):
    """Return available plans — public, no auth needed."""
    return {
        "plans": [
            {
                "id": pid,
                "name": p["name"],
                "price_inr": p["price_inr"],
                "students": p["students"],
                "description": p["desc"],
            }
            for pid, p in PLANS.items()
        ]
    }


@router.post("/api/v1/billing/create-subscription")
@limiter.limit("5/minute")
async def create_subscription(body: dict, request: Request):
    """Create a Razorpay subscription for the org.

    Body: { "plan_id": "growth" }
    Returns Razorpay checkout URL.
    """
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    plan_id = (body.get("plan_id") or "").strip().lower()
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
    existing_sub = (await _atable("subscriptions").select("status")
                    .eq("org_id", str(org_id)).limit(1).execute()).data or []
    if existing_sub and (existing_sub[0].get("status") or "").strip().lower() in ENTITLING_STATUSES:
        raise HTTPException(status_code=409,
            detail="You already have an active plan. Cancel it first — you keep "
                   "access until the end of your billing period — or contact "
                   "sales to change plans.")

    # Optional GSTIN for GST-compliant Razorpay invoices (Indian B2B).
    gstin = (body.get("gstin") or "").strip().upper()
    if gstin and not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin):
        raise HTTPException(status_code=400, detail="Invalid GSTIN format (expected 15 characters).")

    try:
        result = billing_create_subscription(str(org_id), plan_id, gstin=gstin or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Billing misconfigured: %s", e)
        raise HTTPException(status_code=503, detail="Billing unavailable: payment credentials not configured.")
    except Exception as e:
        logger.exception("Failed to create subscription")
        raise HTTPException(status_code=500, detail="Failed to create subscription")

    if result.get("_sandbox"):
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
    except Exception as e:
        logger.error("Failed to update subscription in DB after provider subscription creation: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Subscription was created by the payment provider, but could not be recorded. Please contact support.",
        )

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


async def _notify_payment_issue(org_id: str) -> None:
    """Email the org admin that a renewal payment needs attention (dunning)."""
    try:
        admin_rows = (await _atable("teachers").select("email,full_name")
                      .eq("org_id", str(org_id)).eq("org_role", "admin")
                      .limit(1).execute()).data or []
        if admin_rows:
            from ..emailer import send_payment_failed_notification
            send_payment_failed_notification(
                to_email=admin_rows[0]["email"],
                to_name=admin_rows[0].get("full_name", ""),
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

    # DB-durable idempotency: a retry of an already-recorded event is a no-op.
    if event_id and await billing_event_seen(event_id):
        logger.info("Razorpay webhook %s already processed (event=%s) — 200",
                    safe(event_type), safe(event_id))
        return {"status": "duplicate", "event_id": event_id}
    if not event_id:
        logger.warning("Razorpay webhook missing event.id — idempotency weakened (type=%s)",
                       safe(event_type))

    sub_data = payload.get("subscription", {}).get("entity", {}) or {}
    sub_id = sub_data.get("id")
    if not sub_id:
        # Non-subscription event (e.g. a stray payment.captured) — log to the
        # ledger and ignore. We are subscriptions-only; Orders are deprecated.
        await record_billing_event(event_id=event_id, org_id=None, event_type=event_type,
                                   status="ignored_no_sub", payload=event)
        return {"status": "ignored"}

    db_sub = (await _atable("subscriptions").select("id,org_id,plan,status,past_due_since")
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

    try:
        updates: dict = {}
        if event_type in _SUB_GRANT:
            updates["status"] = _SUB_GRANT[event_type]
            updates["past_due_since"] = None
            if sub_data.get("current_start"):
                updates["current_period_start"] = _epoch_to_iso(sub_data.get("current_start"))
            if sub_data.get("current_end"):
                updates["current_period_end"] = _epoch_to_iso(sub_data.get("current_end"))
            outcome = "grant"
        elif event_type == "subscription.pending":
            # Renewal charge failed; Razorpay keeps retrying. KEEP access (grace)
            # and flag past_due so the dashboard/admin can act.
            updates["status"] = "past_due"
            if not row.get("past_due_since"):
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
        if outcome == "grace":
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

    try:
        client = _get_client()
        raw = client.invoice.all({"subscription_id": sub_id})
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
        invoices = [
            {"id": inv["id"], "amount": inv["amount"], "currency": inv["currency"],
             "status": inv["status"], "created_at": _to_iso(inv.get("created_at")),
             "pdf_url": inv.get("invoice_url") or None,
             "description": inv.get("description", "")}
            for inv in raw.get("items", [])
        ]
        return {"invoices": invoices}
    except Exception as e:
        logger.warning("Failed to fetch Razorpay invoices: %s", e)
        return {"invoices": [], "error": "Failed to fetch invoices. Try again later."}


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
    students_used = student_count_q.count or 0

    # Count total exam attempts this month
    attempts_q = await _atable("exam_sessions")\
        .select("session_key", count="exact")\
        .in_("teacher_id", org_teacher_ids)\
        .in_("status", (SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED))\
        .gte("submitted_at", period_start.isoformat())\
        .execute()
    exam_attempts = attempts_q.count or 0

    overage = max(0, students_used - plan_limit)
    overage_amount = overage * price_per_student

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
    }
