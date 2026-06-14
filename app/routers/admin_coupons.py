"""Superadmin coupon management endpoints."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_admin
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.admin_audit import log_admin_action

_log = logging.getLogger("admin.coupons")

router = APIRouter()


class CouponCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1, max_length=100)
    razorpay_offer_id: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    max_redemptions: int | None = None
    expires_at: str | None = Field(None, description="ISO-8601 timestamp")


class CouponUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool | None = None
    max_redemptions: int | None = None
    description: str | None = None


@router.post("/api/v1/admin/coupons")
@limiter.limit("10/minute")
async def create_coupon(request: Request, body: CouponCreateIn):
    """Register a new coupon code (superadmin only)."""
    teacher = await require_admin(request)
    if teacher.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    code = body.code.strip().lower()
    row = await _atable("coupons").insert({
        "code": code,
        "razorpay_offer_id": body.razorpay_offer_id.strip(),
        "description": body.description.strip() if body.description else None,
        "max_redemptions": body.max_redemptions,
        "expires_at": body.expires_at,
        "created_by": teacher.get("id"),
    }).execute()

    coupon_id = str(row.data[0]["id"])
    await log_admin_action(
        teacher_id=str(teacher.get("id")),
        action="coupon.create",
        target_type="coupon",
        target_id=coupon_id,
        details={"code": code},
        request=request,
    )

    return {**row.data[0], "id": coupon_id}


@router.get("/api/v1/admin/coupons")
@limiter.limit("30/minute")
async def list_coupons(request: Request, limit: int = 100, offset: int = 0):
    """List all coupon codes (superadmin only)."""
    teacher = await require_admin(request)
    if teacher.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    rows = await (
        _atable("coupons")
        .select("*")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    data = [{**r, "id": str(r["id"])} for r in rows.data]
    return {"data": data, "total": len(data)}


@router.patch("/api/v1/admin/coupons/{coupon_id}")
@limiter.limit("10/minute")
async def update_coupon(request: Request, coupon_id: str, body: CouponUpdateIn):
    """Update a coupon (deactivate, adjust limits) — superadmin only."""
    teacher = await require_admin(request)
    if teacher.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    rows = await _atable("coupons").select("*").eq("id", coupon_id).limit(1).execute()
    if not rows.data:
        raise HTTPException(status_code=404, detail="coupon not found")

    updates: dict[str, Any] = {}
    if body.active is not None:
        updates["active"] = body.active
    if body.max_redemptions is not None:
        updates["max_redemptions"] = body.max_redemptions
    if body.description is not None:
        updates["description"] = body.description.strip()

    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")

    await _atable("coupons").update(updates).eq("id", coupon_id).execute()

    await log_admin_action(
        teacher_id=str(teacher.get("id")),
        action="coupon.update",
        target_type="coupon",
        target_id=coupon_id,
        details={k: str(v) for k, v in updates.items()},
        request=request,
    )

    updated = await _atable("coupons").select("*").eq("id", coupon_id).limit(1).execute()
    if updated.data:
        updated.data[0]["id"] = str(updated.data[0]["id"])
        return updated.data[0]
    return {"id": coupon_id}
