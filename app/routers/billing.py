"""Billing router — Razorpay subscription management."""

import json
import logging
from datetime import datetime, timezone
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


def _invalidate_billing_cache(org_id: str):
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
        logger.warning("Failed to update subscription in DB: %s", e)

    return result


@router.post("/api/v1/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Handle Razorpay webhook events.

    Expected events: subscription.activated, subscription.completed,
    subscription.paused, payment.failed
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
    sub_data = payload.get("subscription", {}).get("entity", {})

    sub_id = sub_data.get("id")
    if not sub_id:
        return {"status": "ignored"}

    # Find the subscription in our DB
    db_sub = await _atable("subscriptions").select("id,org_id").eq("razorpay_subscription_id", sub_id).limit(1).execute()
    if not db_sub.data:
        logger.warning("Unknown Razorpay subscription: %s", sub_id)
        return {"status": "ignored"}
    org_id = db_sub.data[0]["org_id"]

    if event_type == "subscription.activated":
        await _atable("subscriptions").update({
            "status": "active",
            "current_period_start": sub_data.get("current_start"),
            "current_period_end": sub_data.get("current_end"),
        }).eq("id", db_sub.data[0]["id"]).execute()
        _invalidate_billing_cache(str(org_id))
        logger.info("Subscription activated for org=%s", org_id)

    elif event_type == "subscription.completed":
        await _atable("subscriptions").update({"status": "expired"}).eq("id", db_sub.data[0]["id"]).execute()
        await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
        _invalidate_billing_cache(str(org_id))
        logger.info("Subscription completed for org=%s", org_id)

    elif event_type == "subscription.paused":
        await _atable("subscriptions").update({"status": "paused"}).eq("id", db_sub.data[0]["id"]).execute()
        await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
        _invalidate_billing_cache(str(org_id))
        logger.info("Subscription paused for org=%s", org_id)

    elif event_type == "subscription.cancelled":
        await _atable("subscriptions").update({"status": "cancelled"}).eq("id", db_sub.data[0]["id"]).execute()
        await _atable("organizations").update({"max_students": PLAN_LIMITS.get("starter", 30)}).eq("id", str(org_id)).execute()
        _invalidate_billing_cache(str(org_id))
        logger.info("Subscription cancelled for org=%s", org_id)

    elif event_type == "payment.failed":
        logger.warning("Payment failed for org=%s sub=%s", org_id, sub_id)
        await _atable("subscriptions").update({"status": "expired"}).eq("id", db_sub.data[0]["id"]).execute()
        _invalidate_billing_cache(str(org_id))

    return {"status": "ok"}


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

    if not sub_id or not _is_live():
        return {"invoices": [
            {"id": "mock_inv_01", "amount": 999, "currency": "INR",
             "status": "paid", "created_at": "2026-04-01T00:00:00Z",
             "pdf_url": None, "description": "Growth plan — mocked (sandbox mode)"},
        ]}

    try:
        import razorpay
        client = _get_client()
        raw = client.invoice.all({"subscription_id": sub_id})
        invoices = [
            {"id": inv["id"], "amount": inv["amount"], "currency": inv["currency"],
             "status": inv["status"], "created_at": inv.get("created_at"),
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
    price_per_student = PLANS.get(plan_id, {}).get("price_inr", 0)
    base_price = PLANS.get(plan_id, {}).get("price_inr", 0)

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
        .in_("status", ("completed", "submitted"))\
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
