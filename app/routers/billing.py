"""Billing router — Razorpay subscription management."""

import json
import logging
from fastapi import APIRouter, Request, HTTPException
from ..dependencies import require_admin, _atable, limiter, PLAN_LIMITS
from ..services.billing import (
    create_subscription as billing_create_subscription,
    verify_webhook, PLANS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


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
        logger.info("Subscription activated for org=%s", org_id)

    elif event_type == "subscription.completed":
        await _atable("subscriptions").update({"status": "expired"}).eq("id", db_sub.data[0]["id"]).execute()
        await _atable("organizations").update({"max_students": 30}).eq("id", str(org_id)).execute()
        logger.info("Subscription completed for org=%s", org_id)

    elif event_type == "subscription.paused":
        await _atable("subscriptions").update({"status": "paused"}).eq("id", db_sub.data[0]["id"]).execute()
        await _atable("organizations").update({"max_students": 30}).eq("id", str(org_id)).execute()
        logger.info("Subscription paused for org=%s", org_id)

    elif event_type == "payment.failed":
        logger.warning("Payment failed for org=%s sub=%s", org_id, sub_id)

    return {"status": "ok"}
