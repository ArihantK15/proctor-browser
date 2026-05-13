"""Billing router — Stripe subscription management."""

import json
import logging
from fastapi import APIRouter, Request, HTTPException
from ..auth import require_admin
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.sessions import PLAN_LIMITS
from ..constants import PLANS
from ..services.billing import (
    create_checkout_session as billing_create_checkout_session,
    create_portal_session,
    verify_webhook,
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


@router.post("/api/v1/billing/create-checkout")
@limiter.limit("5/minute")
async def create_checkout(body: dict, request: Request):
    """Create a Stripe Checkout Session for the org.
    
    Body: { "plan_id": "growth" }
    Returns Stripe Checkout URL for redirect.
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
        result = billing_create_checkout_session(str(org_id), plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to create checkout session")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")

    if result.get("_sandbox"):
        logger.info("Sandbox checkout: %s", result["_note"])

    return result


@router.post("/api/v1/billing/portal")
@limiter.limit("5/minute")
async def billing_portal(body: dict, request: Request):
    """Create a Stripe Billing Portal session for the org's customer."""
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can manage billing")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    # Look up Stripe customer ID from subscription record
    sub = await _atable("subscriptions").select("stripe_customer_id").eq("org_id", str(org_id)).limit(1).execute()
    customer_id = (sub.data or [{}])[0].get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No customer record found. Create a subscription first.")

    try:
        result = create_portal_session(customer_id)
    except Exception as e:
        logger.exception("Failed to create portal session")
        raise HTTPException(status_code=500, detail="Failed to create portal session")

    return result


@router.post("/api/v1/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events.
    
    Expected events: checkout.session.completed, customer.subscription.updated,
    customer.subscription.deleted, invoice.payment_failed.
    """
    raw_body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    if not verify_webhook(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    # ── Checkout completed → create/update subscription locally ──
    if event_type == "checkout.session.completed":
        metadata = data.get("metadata", {}) or {}
        org_id = metadata.get("org_id")
        plan_id = metadata.get("plan")
        sub_data = data.get("subscription", "")
        customer_id = data.get("customer", "")
        if not org_id or not plan_id:
            logger.warning("Missing org_id or plan in checkout.session.completed metadata")
            return {"status": "ignored"}

        existing = await _atable("subscriptions").select("id").eq("org_id", str(org_id)).limit(1).execute()
        row = {
            "plan": plan_id,
            "status": "active",
            "stripe_subscription_id": sub_data,
            "stripe_customer_id": customer_id,
        }
        if existing.data:
            await _atable("subscriptions").update(row).eq("org_id", str(org_id)).execute()
        else:
            await _atable("subscriptions").insert({"org_id": str(org_id), **row}).execute()

        await _atable("organizations").update({
            "max_students": PLAN_LIMITS.get(plan_id, 30)
        }).eq("id", str(org_id)).execute()
        logger.info("Checkout completed for org=%s plan=%s", org_id, plan_id)

    # ── Subscription updated → reflect in DB ──
    elif event_type in ("customer.subscription.updated",):
        sub_id = data.get("id", "")
        status = data.get("status", "")
        period_end = data.get("current_period_end")

        db_sub = await _atable("subscriptions").select("id,org_id").eq("stripe_subscription_id", sub_id).limit(1).execute()
        if not db_sub.data:
            logger.warning("Unknown Stripe subscription: %s", sub_id)
            return {"status": "ignored"}

        status_map = {
            "active": "active",
            "past_due": "active",
            "trialing": "trialing",
            "paused": "paused",
            "canceled": "cancelled",
            "incomplete": "active",
            "incomplete_expired": "expired",
        }
        local_status = status_map.get(status, "expired")
        await _atable("subscriptions").update({
            "status": local_status,
            "current_period_end": str(period_end) if period_end else None,
        }).eq("id", db_sub.data[0]["id"]).execute()

        if local_status in ("cancelled", "expired"):
            await _atable("organizations").update({
                "max_students": PLAN_LIMITS.get("starter", 30)
            }).eq("id", str(db_sub.data[0]["org_id"])).execute()
        logger.info("Subscription %s for org=%s → %s", sub_id, db_sub.data[0]["org_id"], local_status)

    # ── Subscription deleted → expiry ──
    elif event_type == "customer.subscription.deleted":
        sub_id = data.get("id", "")
        db_sub = await _atable("subscriptions").select("id,org_id").eq("stripe_subscription_id", sub_id).limit(1).execute()
        if db_sub.data:
            await _atable("subscriptions").update({"status": "cancelled"}).eq("id", db_sub.data[0]["id"]).execute()
            await _atable("organizations").update({
                "max_students": PLAN_LIMITS.get("starter", 30)
            }).eq("id", str(db_sub.data[0]["org_id"])).execute()
            logger.info("Subscription deleted for org=%s", db_sub.data[0]["org_id"])

    # ── Payment failed ──
    elif event_type == "invoice.payment_failed":
        sub_id = data.get("subscription", "")
        db_sub = await _atable("subscriptions").select("id,org_id").eq("stripe_subscription_id", sub_id).limit(1).execute()
        if db_sub.data:
            logger.warning("Payment failed for org=%s sub=%s", db_sub.data[0]["org_id"], sub_id)

    return {"status": "ok"}
