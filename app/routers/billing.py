"""Billing router — Razorpay subscription management."""

from ..log_safe import safe
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, HTTPException
from ..auth import require_admin
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.sessions import PLAN_LIMITS
from ..constants import PLANS
from ..services.billing import (
    create_subscription as billing_create_subscription,
    verify_webhook,
    _get_client,
    _is_live,
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


async def _activate_org_plan(org_id: str, plan_id: str, razorpay_order_id: str):
    period_start = datetime.now(timezone.utc)
    period_end = period_start + timedelta(days=30)
    sub_data = {
        "plan": plan_id,
        "status": "active",
        "razorpay_subscription_id": None,
        "razorpay_order_id": razorpay_order_id,
        "current_period_start": period_start.isoformat(),
        "current_period_end": period_end.isoformat(),
    }
    existing = await _atable("subscriptions").select("id").eq("org_id", org_id).limit(1).execute()
    sub = (existing.data or [None])[0]
    if sub:
        await _atable("subscriptions").update(sub_data).eq("org_id", org_id).execute()
    else:
        await _atable("subscriptions").insert({"org_id": org_id, **sub_data}).execute()
    await _atable("organizations").update({
        "max_students": PLAN_LIMITS.get(plan_id, 30)
    }).eq("id", org_id).execute()
    _invalidate_billing_cache(org_id)


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
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {plan_id}")

    try:
        result = billing_create_subscription(str(org_id), plan_id)
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

    # Update subscription record in DB
    try:
        existing = await _atable("subscriptions").select("id").eq("org_id", str(org_id)).limit(1).execute()
        sub_data = {
            "plan": plan_id,
            "status": "active",
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
        # Update org max_students
        await _atable("organizations").update({
            "max_students": PLAN_LIMITS.get(plan_id, 30)
        }).eq("id", str(org_id)).execute()
        _invalidate_billing_cache(str(org_id))
    except Exception as e:
        logger.error("Failed to update subscription in DB after provider subscription creation: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Subscription was created by the payment provider, but could not be recorded. Please contact support.",
        )

    return result


@router.post("/api/v1/billing/checkout/order")
@limiter.limit("10/minute")
async def create_checkout_order(body: dict, request: Request):
    """Create a Razorpay Standard Checkout order for a paid plan.

    The browser must pass the returned `order_id` to checkout.js. We only
    activate the subscription after `/checkout/verify` validates Razorpay's
    HMAC signature server-side.
    """
    teacher = await require_admin(request)
    org_id = _require_billing_admin(teacher)
    plan_id = (body.get("plan_id") or "").strip().lower()
    plan = _validate_paid_plan(plan_id)

    client = _get_client()
    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    if client is None or not key_id:
        logger.error("Razorpay Standard Checkout requested without RAZORPAY_KEY_ID/SECRET")
        raise HTTPException(status_code=503, detail="Billing unavailable: Razorpay credentials not configured.")

    amount = int(plan["price_inr"]) * 100
    receipt = f"procta_{str(org_id).replace('-', '')[:12]}_{int(datetime.now(timezone.utc).timestamp())}"[:40]
    try:
        order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "org_id": org_id,
                "plan_id": plan_id,
                "teacher_id": str(teacher.get("id") or ""),
            },
            "payment_capture": 1,
        })
    except Exception as e:
        logger.exception("Failed to create Razorpay order for org=%s plan=%s", safe(org_id), safe(plan_id))
        raise HTTPException(status_code=502, detail="Could not create Razorpay order. Please try again.") from e

    return {
        "key_id": key_id,
        "order_id": order["id"],
        "amount": order.get("amount", amount),
        "currency": order.get("currency", "INR"),
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "description": f"{plan['name']} plan",
    }


@router.post("/api/v1/billing/checkout/verify")
@limiter.limit("20/minute")
async def verify_checkout_payment(body: dict, request: Request):
    """Verify Razorpay Checkout success and activate the org plan.

    SECURITY: the plan_id and org_id used for activation come from the
    Razorpay Order's `notes` (pinned server-side at create time), NOT
    from the request body. Without this, a user who pays ₹2,400 for
    Starter could re-submit /verify with plan_id=pro and get the ₹30k
    plan activated — the signature only proves the payment happened
    for that order, it doesn't bind to a plan tier.
    """
    teacher = await require_admin(request)
    caller_org_id = _require_billing_admin(teacher)

    order_id = (body.get("razorpay_order_id") or "").strip()
    payment_id = (body.get("razorpay_payment_id") or "").strip()
    signature = (body.get("razorpay_signature") or "").strip()
    if not order_id or not payment_id or not signature:
        raise HTTPException(status_code=400, detail="Missing Razorpay payment verification fields.")

    secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not secret:
        logger.error("Razorpay verification requested without RAZORPAY_KEY_SECRET")
        raise HTTPException(status_code=503, detail="Payment verification unavailable.")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("Invalid Razorpay signature for org=%s order=%s payment=%s",
                       safe(caller_org_id), safe(order_id), safe(payment_id))
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    # Re-fetch the order from Razorpay so we trust SERVER-PINNED notes
    # (plan_id, org_id) instead of whatever the caller chose to post.
    client = _get_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Razorpay client not configured.")
    try:
        order = client.order.fetch(order_id)
    except Exception as e:
        logger.exception("Razorpay order fetch failed during verify org=%s order=%s",
                         safe(caller_org_id), safe(order_id))
        raise HTTPException(status_code=502, detail="Could not confirm order with Razorpay.") from e

    notes = order.get("notes") or {}
    notes_org_id = str(notes.get("org_id") or "")
    notes_plan_id = (notes.get("plan_id") or "").strip().lower()

    if notes_org_id != caller_org_id:
        logger.warning("Cross-org checkout verify: caller org=%s order notes org=%s order=%s",
                       safe(caller_org_id), safe(notes_org_id), safe(order_id))
        raise HTTPException(status_code=403, detail="This order does not belong to your organization.")

    if notes_plan_id not in PLANS:
        logger.error("Razorpay order missing/invalid plan_id in notes: order=%s notes=%s",
                     safe(order_id), safe(notes))
        raise HTTPException(status_code=500, detail="Order plan binding missing — contact support.")
    plan = _validate_paid_plan(notes_plan_id)

    # Belt-and-suspenders: amount must match what we'd charge for that plan.
    expected_amount = int(plan["price_inr"]) * 100
    if int(order.get("amount") or 0) != expected_amount:
        logger.error("Razorpay order amount mismatch: order=%s amount=%s expected=%s plan=%s",
                     safe(order_id), order.get("amount"), expected_amount, safe(notes_plan_id))
        raise HTTPException(status_code=400, detail="Order amount does not match plan price.")

    # Status check is informational — auto-capture means the order should
    # be "paid" by the time the handler fires. If somehow it isn't yet,
    # the webhook (or the user's next refresh) will eventually reconcile.
    if (order.get("status") or "").lower() not in ("paid", "attempted"):
        logger.warning("Razorpay order verify with unexpected status: order=%s status=%s",
                       safe(order_id), order.get("status"))

    try:
        await _activate_org_plan(caller_org_id, notes_plan_id, order_id)
    except Exception as e:
        logger.error("Payment verified but DB activation failed for org=%s order=%s: %s",
                     safe(caller_org_id), safe(order_id), safe(e))
        raise HTTPException(
            status_code=500,
            detail="Payment verified, but plan activation failed. Please contact support.",
        ) from e

    logger.info("Razorpay payment verified and plan activated org=%s plan=%s order=%s payment=%s",
                safe(caller_org_id), safe(notes_plan_id), safe(order_id), safe(payment_id))
    return {"ok": True, "plan_id": notes_plan_id, "order_id": order_id, "payment_id": payment_id}


@router.post("/api/v1/webhooks/razorpay")
@limiter.limit("60/minute")
async def razorpay_webhook(request: Request):
    """Handle Razorpay webhook events.

    Expected events: subscription.activated, subscription.completed,
    subscription.paused, payment.failed

    Idempotency: Razorpay retries failed deliveries with exponential
    backoff for up to 24 hours, and the same `event.id` is reused. Without
    dedup we'd activate the same plan twice or fire duplicate
    side effects. We cache event_id -> processed for 24 h after a
    successful run and short-circuit any later retry to a 200 OK so
    Razorpay stops retrying. Dedup runs AFTER signature verification —
    otherwise an unauthenticated caller could enumerate which event-ids
    we've ever seen.
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

    # Idempotency dedup — keyed on event.id from Razorpay. Missing id
    # is treated as "process once" with a warning; that's defensive
    # against malformed deliveries in tests/sandbox.
    event_id = str(event.get("id") or "").strip()
    _idem_key = f"webhook:razorpay:{event_id}" if event_id else None
    if _idem_key:
        try:
            prior = _cache.get(_idem_key) if _cache else None
            if prior:
                logger.info(
                    "Razorpay webhook %s already processed for event=%s — short-circuiting to 200",
                    safe(event_type), safe(event_id),
                )
                return {"status": "duplicate", "event_id": event_id}
        except Exception:
            # Cache miss/error is safe — we'll process and re-cache.
            logger.debug("Webhook idempotency cache read failed", exc_info=True)
    else:
        logger.warning(
            "Razorpay webhook missing event.id — dedup skipped (event_type=%s)",
            safe(event_type),
        )

    def _mark_done(status: str) -> None:
        """Persist processed-state for the current event so any Razorpay
        retry in the next 24 h returns the cached duplicate-200 instead
        of re-running this handler. No-op when event_id was missing.
        """
        if not _idem_key:
            return
        try:
            if _cache:
                _cache.set(
                    _idem_key,
                    {"processed": True, "status": status, "type": event_type},
                    ttl=86400,  # match Razorpay's max retry horizon
                )
        except Exception:
            logger.debug("Webhook idempotency cache write failed", exc_info=True)

    # ── Standard Checkout (one-off Order + Payment) safety-net path ──
    # /checkout/verify already activates the plan synchronously when the
    # browser returns from Razorpay. These webhook events are a
    # belt-and-suspenders reconciliation in case the user closes the tab
    # before verify lands. We trust the `notes.plan_id` we set at order
    # creation since the webhook is authenticated by signature.
    if event_type in ("payment.captured", "order.paid"):
        entity = (payload.get("payment", {}).get("entity")
                  or payload.get("order", {}).get("entity")
                  or {})
        notes = entity.get("notes") or {}
        # `order.paid` carries `notes` directly; `payment.captured` only
        # carries the order_id, so we look up the order to read notes.
        notes_org_id = str(notes.get("org_id") or "")
        notes_plan_id = (notes.get("plan_id") or "").strip().lower()
        order_id_for_log = entity.get("order_id") or entity.get("id") or ""

        if not notes_org_id and entity.get("order_id"):
            try:
                client = _get_client()
                if client is not None:
                    o = client.order.fetch(entity["order_id"])
                    o_notes = o.get("notes") or {}
                    notes_org_id = str(o_notes.get("org_id") or "")
                    notes_plan_id = (o_notes.get("plan_id") or "").strip().lower()
                    order_id_for_log = entity["order_id"]
            except Exception as e:
                logger.warning("Webhook order-notes lookup failed for %s: %s",
                               safe(entity.get("order_id")), safe(e))

        if not notes_org_id or notes_plan_id not in PLANS:
            logger.info("Webhook %s ignored — missing org_id/plan_id in notes (event_id=%s)",
                        safe(event_type), safe(event.get("id", "")))
            _mark_done("ignored_missing_notes")
            return {"status": "ignored"}
        try:
            await _activate_org_plan(notes_org_id, notes_plan_id, order_id_for_log)
            logger.info("Webhook %s reconciled org=%s plan=%s order=%s",
                        safe(event_type), safe(notes_org_id), safe(notes_plan_id), safe(order_id_for_log))
        except Exception as e:
            logger.error("Webhook %s activation failed for org=%s order=%s: %s",
                         safe(event_type), safe(notes_org_id), safe(order_id_for_log), safe(e))
            # NB: NOT mark_done — return 500 so Razorpay retries.
            raise HTTPException(status_code=500, detail="Webhook reconciliation failed — will retry")
        _mark_done("ok")
        return {"status": "ok"}

    # ── Legacy subscription-based path (kept for back-compat with any
    #    existing subscription IDs in the DB) ──
    sub_data = payload.get("subscription", {}).get("entity", {})

    sub_id = sub_data.get("id")
    if not sub_id:
        _mark_done("ignored_no_sub_id")
        return {"status": "ignored"}

    # Find the subscription in our DB
    db_sub = await _atable("subscriptions").select("id,org_id").eq("razorpay_subscription_id", sub_id).limit(1).execute()
    if not db_sub.data:
        logger.warning("Unknown Razorpay subscription: %s", safe(sub_id))
        _mark_done("ignored_unknown_sub")
        return {"status": "ignored"}
    org_id = db_sub.data[0]["org_id"]

    try:
        if event_type == "subscription.activated":
            result = await _atable("subscriptions").update({
                "status": "active",
                "current_period_start": sub_data.get("current_start"),
                "current_period_end": sub_data.get("current_end"),
            }).eq("id", db_sub.data[0]["id"]).execute()
            if not result.data:
                raise RuntimeError("subscription.activated DB write returned no data")
            _invalidate_billing_cache(str(org_id))
            logger.info("Subscription activated for org=%s", org_id)

        elif event_type == "subscription.completed":
            result = await _atable("subscriptions").update({"status": "expired"}).eq("id", db_sub.data[0]["id"]).execute()
            if not result.data:
                raise RuntimeError("subscription.completed DB write returned no data")
            await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
            _invalidate_billing_cache(str(org_id))
            logger.info("Subscription completed for org=%s", org_id)

        elif event_type == "subscription.paused":
            result = await _atable("subscriptions").update({"status": "paused"}).eq("id", db_sub.data[0]["id"]).execute()
            if not result.data:
                raise RuntimeError("subscription.paused DB write returned no data")
            await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
            _invalidate_billing_cache(str(org_id))
            logger.info("Subscription paused for org=%s", org_id)

        elif event_type == "subscription.cancelled":
            result = await _atable("subscriptions").update({"status": "cancelled"}).eq("id", db_sub.data[0]["id"]).execute()
            if not result.data:
                raise RuntimeError("subscription.cancelled DB write returned no data")
            await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
            _invalidate_billing_cache(str(org_id))
            logger.info("Subscription cancelled for org=%s", org_id)

        elif event_type == "payment.failed":
            logger.warning("Payment failed for org=%s sub=%s", safe(org_id), safe(sub_id))
            result = await _atable("subscriptions").update({"status": "expired"}).eq("id", db_sub.data[0]["id"]).execute()
            if not result.data:
                raise RuntimeError("payment.failed DB write returned no data")
            _invalidate_billing_cache(str(org_id))
            # Notify the org admin by email so they can update their payment method
            try:
                admin_rows = await _atable("teachers").select("email,full_name")\
                    .eq("org_id", str(org_id)).eq("org_role", "admin").limit(1).execute()
                if admin_rows.data:
                    admin = admin_rows.data[0]
                    from ..emailer import send_payment_failed_notification
                    send_payment_failed_notification(
                        to_email=admin["email"],
                        to_name=admin.get("full_name", ""),
                    )
            except Exception as notify_err:
                logger.warning("Payment-failed email notification failed for org=%s: %s", org_id, notify_err)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Webhook DB write failed for event=%s org=%s: %s", safe(event_type), safe(org_id), safe(e))
        # NB: NOT mark_done — return 500 so Razorpay retries this delivery.
        raise HTTPException(status_code=500, detail="Webhook processing failed — will retry")

    _mark_done("ok")
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
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    sub = await _atable("subscriptions").select("id,razorpay_subscription_id,status,current_period_end")\
        .eq("org_id", str(org_id)).limit(1).execute()
    if not sub.data:
        raise HTTPException(status_code=404, detail="No active subscription found")

    sub_row = sub.data[0]
    if sub_row.get("status") in ("cancelled", "expired"):
        raise HTTPException(status_code=409, detail="Subscription is already cancelled or expired")

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
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

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
        import razorpay
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
            for inv in (raw.get("items", []) if isinstance(raw, dict) else raw.get("items", []))
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
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    # Get current plan limits
    sub = await _atable("subscriptions").select("plan,status").eq("org_id", str(org_id)).limit(1).execute()
    plan_id = (sub.data or [{}])[0].get("plan", "starter") if sub.data else "starter"
    sub_status = (sub.data or [{}])[0].get("status", "unknown") if sub.data else "unknown"
    plan_limit = PLAN_LIMITS.get(plan_id, 30)
    plan_def = PLANS.get(plan_id, {})
    base_price = plan_def.get("price_inr", 0)
    # `overage_price_inr` is the per-EXTRA-STUDENT charge above the
    # plan limit. Was previously using base price_inr (₹12,000 for
    # Growth, etc.) which produced absurd overage_amount values.
    # P1.4 fix — read the explicit per-student rate added to the
    # PLANS dict, fall back to 0 (= no overage charging) when missing.
    price_per_student = plan_def.get("overage_price_inr", 0)

    # Count current period usage
    now_utc = datetime.now(timezone.utc)
    from ..utils import now_ist as _now_ist
    period_start = _now_ist().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Count distinct students who submitted this month
    student_count_q = await _atable("exam_sessions")\
        .select("student_id", count="exact", distinct="student_id")\
        .eq("teacher_id", str(teacher["id"]))\
        .gte("submitted_at", period_start.isoformat())\
        .execute()
    students_used = student_count_q.count or 0

    # Count total exam attempts this month
    attempts_q = await _atable("exam_sessions")\
        .select("session_key", count="exact")\
        .eq("teacher_id", str(teacher["id"]))\
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
