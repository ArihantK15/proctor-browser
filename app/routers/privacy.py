"""Privacy center — consent recording, data export, account deletion."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict

from ..auth import require_admin, require_student_account
from ..database import async_table as _atable
from ..limiter import limiter

_log = logging.getLogger("privacy")

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


class ConsentIn(BaseModel):
    model_config = ConfigDict(strict=True)
    consent_type: str  # 'signup_terms' | 'privacy_policy' | 'phone_camera'


# ── Record consent ────────────────────────────────────────────────


@router.post("/consent")
@limiter.limit("30/minute")
async def record_consent(body: ConsentIn, request: Request):
    """Record a consent action for the authenticated user."""
    user_type = None
    user_id = None
    try:
        teacher = await require_admin(request)
        user_type = "teacher"
        user_id = str(teacher["id"])
    except HTTPException:
        pass
    if not user_id:
        try:
            student = await require_student_account(request)
            user_type = "student"
            user_id = str(student["id"])
        except HTTPException:
            raise HTTPException(status_code=401, detail="Authentication required")

    ip = request.client.host if request.client else ""

    await _atable("consent_records").insert({
        "user_id": user_id,
        "user_type": user_type,
        "consent_type": body.consent_type,
        "ip_address": ip,
    }).execute()

    return {"status": "recorded"}


# ── Data export ───────────────────────────────────────────────────


@router.get("/export")
@limiter.limit("5/hour")
async def export_data(request: Request):
    """Export all personal data for the authenticated user as JSON."""
    user_type = None
    user_id = None
    try:
        teacher = await require_admin(request)
        user_type = "teacher"
        user_id = str(teacher["id"])
    except HTTPException:
        pass
    if not user_id:
        try:
            student = await require_student_account(request)
            user_type = "student"
            user_id = str(student["id"])
        except HTTPException:
            raise HTTPException(status_code=401, detail="Authentication required")

    data = {"user_type": user_type, "user_id": user_id, "exported_at": datetime.now(timezone.utc).isoformat()}

    if user_type == "teacher":
        # Profile
        rows = await _atable("teachers").select("id,email,full_name,org_id,org_role,supabase_uid,created_at").eq("id", user_id).execute()
        data["profile"] = rows.data[0] if rows.data else None

        # Org
        if data.get("profile"):
            oid = data["profile"].get("org_id")
            if oid:
                rows = await _atable("organizations").select("id,name,slug,max_students,created_at").eq("id", oid).execute()
                data["organization"] = rows.data[0] if rows.data else None

        # Exams
        rows = await _atable("exam_config").select("exam_id,exam_title,exam_status,starts_at,ends_at,duration_minutes,created_at").eq("teacher_id", user_id).execute()
        data["exams"] = rows.data or []

        # Students
        rows = await _atable("students").select("*").eq("teacher_id", user_id).execute()
        data["students"] = rows.data or []

        # Subscriptions
        if data.get("organization"):
            rows = await _atable("subscriptions").select("*").eq("org_id", data["organization"]["id"]).execute()
            data["subscriptions"] = rows.data or []

    elif user_type == "student":
        # Profile
        rows = await _atable("student_accounts").select("*").eq("id", user_id).execute()
        data["profile"] = rows.data[0] if rows.data else None

        # Sessions
        rows = await _atable("exam_sessions").select("*").eq("student_id", user_id).execute()
        data["sessions"] = rows.data or []

        # Enrolled students (matching student_account_id)
        if data.get("profile"):
            rows = await _atable("students").select("*").eq("account_id", user_id).execute()
            data["enrollments"] = rows.data or []

    # Consent records
    rows = await _atable("consent_records").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    data["consent_records"] = rows.data or []

    return data


# ── Account deletion ──────────────────────────────────────────────


@router.post("/delete")
@limiter.limit("2/hour")
async def delete_account(request: Request, body: dict = Body(default_factory=dict)):
    """Delete or anonymize all personal data for the authenticated user."""
    user_type = None
    user_id = None
    supabase_uid = None
    try:
        teacher = await require_admin(request)
        user_type = "teacher"
        user_id = str(teacher["id"])
        supabase_uid = teacher.get("supabase_uid")
    except HTTPException:
        pass
    if not user_id:
        try:
            student = await require_student_account(request)
            user_type = "student"
            user_id = str(student["id"])
            supabase_uid = student.get("supabase_uid")
        except HTTPException:
            raise HTTPException(status_code=401, detail="Authentication required")

    # GDPR account-delete is the most destructive thing a logged-in
    # user can trigger; demand a fresh reauth token regardless of
    # user_type. Helper accepts body-or-X-Reauth-Token-header so
    # legacy callers passing the field in the body keep working.
    from ..auth.admin_auth import require_reauth_or_403
    require_reauth_or_403(body, user_id, request=request)

    errors = []
    anon = f"deleted_user_{user_id[:8]}"

    if user_type == "teacher":
        # Anonymize teacher profile
        try:
            await _atable("teachers").update({
                "full_name": "Deleted User",
                "email": anon + "@deleted.procta.net",
            }).eq("id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] teacher anonymise failed for %s", user_id)
            errors.append(f"teacher update: {e.__class__.__name__}")

        # Anonymize students
        try:
            await _atable("students").update({
                "full_name": "Deleted User",
                "email": anon + "@deleted.procta.net",
            }).eq("teacher_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] students anonymise failed for %s", user_id)
            errors.append(f"students update: {e.__class__.__name__}")

        # Mark exam configs
        try:
            await _atable("exam_config").update({
                "exam_title": "Deleted Exam",
            }).eq("teacher_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] exam_config anonymise failed for %s", user_id)
            errors.append(f"exam_config update: {e.__class__.__name__}")

    elif user_type == "student":
        # Anonymize student profile
        try:
            await _atable("student_accounts").update({
                "full_name": "Deleted User",
                "email": anon + "@deleted.procta.net",
            }).eq("id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] student_account anonymise failed for %s", user_id)
            errors.append(f"student_account update: {e.__class__.__name__}")

        # Anonymize student enrollments
        try:
            await _atable("students").update({
                "full_name": "Deleted User",
                "email": anon + "@deleted.procta.net",
            }).eq("account_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] students anonymise failed for %s", user_id)
            errors.append(f"students update: {e.__class__.__name__}")

        # Anonymize exam sessions
        try:
            await _atable("exam_sessions").update({
                "full_name": "Deleted User",
                "email": anon + "@deleted.procta.net",
            }).eq("student_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] exam_sessions anonymise failed for %s", user_id)
            errors.append(f"exam_sessions update: {e.__class__.__name__}")

    # Delete Supabase auth user (revokes all tokens, prevents login)
    from ..database import is_postgres_backend
    if supabase_uid and not is_postgres_backend():
        try:
            from ..database import supabase
            supabase.auth.admin.delete_user(supabase_uid)
        except Exception as e:
            _log.exception("[privacy] supabase auth delete failed for %s", user_id)
            errors.append(f"auth delete: {e.__class__.__name__}")

    # Revoke API keys for teachers
    if user_type == "teacher":
        try:
            await _atable("api_keys").update({"is_active": False}).eq("teacher_id", user_id).execute()
        except Exception:
            _log.warning("privacy: api_keys deactivate failed during user delete", exc_info=True)

    status = "deleted" if not errors else "partial"
    _log.warning("[privacy] account deleted: %s %s (errors=%s)", user_type, user_id, errors)

    return {"status": status, "errors": errors if errors else None}
