"""Operator-run Subject Access Request endpoints.

Companion to user-facing `/api/v1/privacy/*`. When a user cannot
authenticate themselves to call `/privacy/delete` (lost account
access, deceased data subject, court order, etc.), a superadmin
operator can run the erasure here instead.

Same retention semantics as the user-facing flow (see
docs/PRIVACY.md). The difference is the **caller** — a superadmin
acting on behalf of the data subject — not the **target** of erasure.

Every action here writes two audit records:
  - `admin_audit_log`: who-ran-it + ticket ref + reason text
  - `auth_events`: account_deleted_by_admin for the target user

Authorization: the env-pinned platform owner only. Org-level admins
can't trigger this because that would let an org admin erase users in
other orgs. The gate compares the caller's email to SUPER_ADMIN_EMAIL
(env pin) — NOT a DB-stored org_role string — so it can never be
satisfied by an org-side role write. See _require_superadmin.

Endpoints:
  POST /api/v1/admin/sar/delete — erase target user
  POST /api/v1/admin/sar/export — staff-side dump for a target user
                                  (returns the same JSON shape as
                                  /privacy/export, useful when the
                                  user emails support without being
                                  able to log in)
"""
# NOTE: deliberately NO `from __future__ import annotations`. slowapi's
# @limiter.limit wraps each handler with functools.wraps, and FastAPI
# resolves string annotations against the WRAPPER's __globals__ (slowapi's
# module), where SARDeleteIn/SARExportIn don't exist. Stringized
# annotations would therefore fail to resolve to the body model and
# FastAPI would treat `body` as a query param (every call → 422). Keeping
# annotations as real objects (Python 3.10+ native `str | None`) avoids
# this. See the audit note in the commit that added this comment.
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_admin
from ..constants import SUPER_ADMIN_EMAIL
from ..database import async_table as _atable
from ..limiter import limiter

_log = logging.getLogger("admin.sar")


def _mask_email(email: str | None) -> str | None:
    """Reduce an email to a non-identifying audit token (a***@domain).

    Used when writing the post-erasure auth_events row: persisting the
    deleted subject's full address into a retained audit table would
    re-introduce the PII we just erased. The masked form preserves
    enough to correlate without storing the identifier.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{(local[:1] or '')}***@{domain}"

router = APIRouter(prefix="/api/v1/admin/sar", tags=["admin", "sar"])


class SARDeleteIn(BaseModel):
    model_config = ConfigDict(strict=True)
    target_user_type: str = Field(..., pattern="^(teacher|student)$")
    target_user_id: str | None = None  # uuid; preferred
    target_email: str | None = None    # fallback identifier
    reason: str = Field(..., min_length=20)
    ticket_id: str | None = None


class SARExportIn(BaseModel):
    model_config = ConfigDict(strict=True)
    target_user_type: str = Field(..., pattern="^(teacher|student)$")
    target_user_id: str | None = None
    target_email: str | None = None
    ticket_id: str | None = None


class SARRevokeSessionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    target_user_type: str = Field(..., pattern="^(teacher|student)$")
    target_user_id: str | None = None
    target_email: str | None = None


async def _require_superadmin(request: Request) -> dict:
    """Reject anyone who isn't the platform owner.

    require_admin enforces an authenticated teacher session; we then
    verify the caller is the env-pinned SUPER_ADMIN_EMAIL directly,
    rather than trusting the in-memory `org_role` string. SAR resolves
    targets across ALL orgs with no tenant scoping, so the gate must
    not be satisfiable by a DB-stored role — otherwise a future code
    path that persisted org_role='superadmin' for an org admin would
    silently grant them cross-tenant erase/export. The env pin is the
    only authority that can never be set by an org-side write.
    """
    teacher = await require_admin(request)
    caller_email = str(teacher.get("email", "")).strip().lower()
    if not (SUPER_ADMIN_EMAIL and caller_email == SUPER_ADMIN_EMAIL):
        raise HTTPException(
            status_code=403,
            detail="Superadmin role required for SAR operations",
        )
    return teacher


async def _resolve_target(
    user_type: str,
    user_id: str | None,
    email: str | None,
) -> dict:
    """Look up the SAR target by id or email. Raises 404 if missing."""
    if not (user_id or email):
        raise HTTPException(
            status_code=400,
            detail="Must provide target_user_id or target_email",
        )
    table = "teachers" if user_type == "teacher" else "student_accounts"
    if user_id:
        q = _atable(table).select("*").eq("id", user_id)
    else:
        q = _atable(table).select("*").eq("email", (email or "").strip().lower())
    rows = (await q.execute()).data or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"No {user_type} found")
    return rows[0]


async def _revoke_target_sessions(user_type: str, user_id: str) -> list[str]:
    """Best-effort revoke of every active session + refresh token."""
    errs: list[str] = []
    user_kind = "teacher" if user_type == "teacher" else "student_account"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await _atable("auth_sessions").update({"revoked_at": now_iso})\
            .eq("user_kind", user_kind).eq("user_id", user_id).execute()
    except Exception as e:
        errs.append(f"auth_sessions: {type(e).__name__}")
    try:
        await _atable("refresh_tokens").update({"revoked_at": now_iso})\
            .is_("revoked_at", "null")\
            .eq("kind", "teacher" if user_type == "teacher" else "student")\
            .eq("user_id", user_id).execute()
    except Exception as e:
        errs.append(f"refresh_tokens: {type(e).__name__}")
    return errs


@router.post("/delete")
@limiter.limit("10/hour")
async def sar_delete(body: SARDeleteIn, request: Request):
    """Erase a target user's account on their behalf.

    Authorization: the env-pinned platform owner (SUPER_ADMIN_EMAIL)
    only — see _require_superadmin. Required body fields include
    a free-text `reason` (≥20 chars) so the audit row carries
    forensic context — *why* this erasure happened outside the
    self-service flow. Optional `ticket_id` links to an external
    helpdesk reference.
    """
    operator = await _require_superadmin(request)
    target = await _resolve_target(body.target_user_type, body.target_user_id, body.target_email)
    target_id = str(target["id"])
    operator_id = str(operator["id"])

    errors: list[str] = []
    anon_local = f"deleted_user_{target_id[:8]}"
    anon_email = f"{anon_local}@deleted.procta.net"

    # 0. Record intent BEFORE touching anything. If the process dies
    # mid-erasure we still have a forensic record that the operation
    # was attempted (and by whom). The completion row below updates the
    # final status; this one proves the attempt regardless of outcome.
    try:
        from ..services.admin_audit import log_admin_action
        await log_admin_action(
            teacher_id=operator_id,
            action="sar_delete_started",
            target_type=body.target_user_type,
            target_id=target_id,
            details={
                "reason": body.reason,
                "ticket_id": body.ticket_id,
                "target_email": target.get("email"),
            },
            request=request,
        )
    except Exception:
        _log.exception("[sar.delete] pre-op admin_audit_log write failed")

    # 1. Kill auth first.
    errors.extend(await _revoke_target_sessions(body.target_user_type, target_id))

    # 2. Per-role erasure.
    if body.target_user_type == "teacher":
        # Same shape as privacy.py teacher path — anonymise profile,
        # students, exam_config; hard-delete oauth + email_otps;
        # deactivate api_keys.
        try:
            await _atable("teachers").update({
                "full_name": "Deleted User",
                "email": anon_email,
                "status": "deleted",
            }).eq("id", target_id).execute()
            from ..auth.admin_auth import clear_teacher_cache
            clear_teacher_cache(target_id)
        except Exception as e:
            errors.append(f"teachers: {type(e).__name__}")
        try:
            await _atable("students").update({
                "full_name": "Deleted User",
                "email": anon_email,
            }).eq("teacher_id", target_id).execute()
        except Exception as e:
            errors.append(f"students: {type(e).__name__}")
        try:
            await _atable("exam_config").update({
                "exam_title": "Deleted Exam",
                "access_code": "",
            }).eq("teacher_id", target_id).execute()
        except Exception as e:
            errors.append(f"exam_config: {type(e).__name__}")
        try:
            await _atable("api_keys").update({"is_active": False}).eq("teacher_id", target_id).execute()
        except Exception as e:
            errors.append(f"api_keys: {type(e).__name__}")
        try:
            await _atable("google_auth_tokens").delete().eq("teacher_id", target_id).execute()
        except Exception as e:
            errors.append(f"google_auth_tokens: {type(e).__name__}")
        try:
            await _atable("email_otps").delete().eq("user_kind", "teacher").eq("user_id", target_id).execute()
        except Exception:
            pass

    else:  # student
        # Delegate to the deeper auth.py flow which notifies the
        # issuing teacher + deletes student_invites + student row
        # cleanup. Pass the target account directly so the helper
        # doesn't try to resolve from the request's auth context.
        try:
            from .auth import _track_a_hybrid_delete_student_account
            result = await _track_a_hybrid_delete_student_account(target, request)
            errors.extend(result.get("errors") or [])
        except Exception as e:
            _log.exception("[sar.delete] student delete failed for %s", target_id)
            errors.append(f"student_delete: {type(e).__name__}")

    # 3. Audit both ways — admin_audit_log captures the operator's
    # action; auth_events captures the impact on the target user.
    try:
        from ..services.admin_audit import log_admin_action
        await log_admin_action(
            teacher_id=operator_id,
            action="sar_delete",
            target_type=body.target_user_type,
            target_id=target_id,
            details={
                "reason": body.reason,
                "ticket_id": body.ticket_id,
                "target_email": target.get("email"),
                "errors": errors,
                "partial": bool(errors),
            },
            request=request,
        )
    except Exception:
        _log.exception("[sar.delete] admin_audit_log write failed")

    try:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        await _atable("auth_events").insert({
            "user_kind": "teacher" if body.target_user_type == "teacher" else "student_account",
            "user_id": target_id,
            # Masked — this row is retained; storing the full address
            # would re-persist the PII the erasure just removed.
            "email": _mask_email(target.get("email")),
            "event_type": "account_deleted_by_admin",
            "ip": ip,
            "user_agent": ua,
            "meta": json.dumps({
                "operator_id": operator_id,
                "reason": body.reason,
                "ticket_id": body.ticket_id,
                "errors": errors,
            }),
        }).execute()
    except Exception:
        _log.exception("[sar.delete] auth_events write failed")

    status = "deleted" if not errors else "partial"
    _log.warning(
        "[sar.delete] operator=%s target=%s/%s status=%s ticket=%s",
        operator_id, body.target_user_type, target_id, status, body.ticket_id,
    )
    return {"status": status, "target_id": target_id, "errors": errors or None}


@router.post("/export")
@limiter.limit("20/hour")
async def sar_export(body: SARExportIn, request: Request):
    """Generate a data export on a target user's behalf.

    Returns the same JSON shape as /api/v1/privacy/export. Used when
    a user emails support requesting their data but cannot or will
    not log in themselves (e.g. lost MFA device).

    Logs to admin_audit_log so we can show a regulator we honoured
    the request.
    """
    operator = await _require_superadmin(request)
    target = await _resolve_target(body.target_user_type, body.target_user_id, body.target_email)
    target_id = str(target["id"])

    # Reuse the user-facing exporter by reconstructing the auth path
    # — the export router resolves "who am I" via require_admin /
    # require_student_account, which we can't satisfy without a real
    # session. Easier: reimplement the export shape inline. Same
    # tables, but parameterised on target_id.
    from .privacy import _safe_fetch, _redact_profile

    data: dict[str, Any] = {
        "user_type": body.target_user_type,
        "user_id": target_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 2,
        "exported_by_operator": str(operator["id"]),
        "ticket_id": body.ticket_id,
    }

    if body.target_user_type == "teacher":
        data["profile"] = _redact_profile(target)
        oid = target.get("org_id")
        if oid:
            org_rows = await _safe_fetch(
                "organizations", eq={"id": oid},
                columns="id,name,slug,max_students,created_at",
            )
            data["organization"] = org_rows[0] if org_rows else None
            data["subscriptions"] = await _safe_fetch("subscriptions", eq={"org_id": oid})
        data["exams"] = await _safe_fetch("exam_config", eq={"teacher_id": target_id})
        data["questions"] = await _safe_fetch("questions", eq={"teacher_id": target_id})
        data["students"] = await _safe_fetch("students", eq={"teacher_id": target_id})
        data["student_groups"] = await _safe_fetch("student_groups", eq={"teacher_id": target_id})
        data["exam_sessions"] = await _safe_fetch("exam_sessions", eq={"teacher_id": target_id})
        data["violations"] = await _safe_fetch("violations", eq={"teacher_id": target_id})
        data["answers"] = await _safe_fetch("answers", eq={"teacher_id": target_id})
        data["student_invites"] = await _safe_fetch("student_invites", eq={"teacher_id": target_id})
        data["api_keys"] = await _safe_fetch(
            "api_keys", eq={"teacher_id": target_id},
            columns="id,name,key_prefix,is_active,created_at,last_used_at",
        )
        data["grading_audit"] = await _safe_fetch("grading_audit", eq={"teacher_id": target_id})
        data["admin_audit_log"] = await _safe_fetch("admin_audit_log", eq={"teacher_id": target_id})
    else:  # student
        data["profile"] = _redact_profile(target)
        data["enrollments"] = await _safe_fetch("students", eq={"account_id": target_id})
        data["exam_sessions"] = await _safe_fetch("exam_sessions", eq={"student_id": target_id})
        data["appeals"] = await _safe_fetch("appeals", eq={"student_id": target_id})
        # Walk each session for its answers + violations so the operator
        # dump is at least as complete as the self-service /privacy/export
        # (which a SAR exists precisely to substitute for). Capped to bound
        # a synchronous export.
        session_keys = [
            s["session_key"] for s in (data["exam_sessions"] or [])
            if s.get("session_key")
        ]
        SESSION_CAP = 500
        if len(session_keys) > SESSION_CAP:
            raise HTTPException(
                status_code=413,
                detail=f"Export exceeds {SESSION_CAP} sessions. Use a smaller time range or contact support.",
            )
        answers: list[dict] = []
        violations: list[dict] = []
        for sk in session_keys:
            answers.extend(await _safe_fetch("answers", eq={"session_key": sk}))
            violations.extend(await _safe_fetch("violations", eq={"session_key": sk}))
        data["answers"] = answers
        data["violations"] = violations

    data["consent_records"] = await _safe_fetch("consent_records", eq={"user_id": target_id})
    data["auth_events"] = await _safe_fetch(
        "auth_events", eq={"user_id": target_id},
        columns="event_type,ip,user_agent,meta,created_at",
    )

    try:
        from ..services.admin_audit import log_admin_action
        await log_admin_action(
            teacher_id=str(operator["id"]),
            action="sar_export",
            target_type=body.target_user_type,
            target_id=target_id,
            details={
                "ticket_id": body.ticket_id,
                "target_email": target.get("email"),
                "tables": sorted(k for k, v in data.items() if isinstance(v, list)),
            },
            request=request,
        )
    except Exception:
        _log.exception("[sar.export] audit write failed")

    return data


@router.post("/revoke-sessions")
@limiter.limit("20/hour")
async def sar_revoke_sessions(body: SARRevokeSessionsIn, request: Request):
    """Superadmin: revoke all auth sessions + refresh tokens for a user.

    The user can be identified by either their UUID (preferred) or email
    (fallback). This does NOT delete the account — it only kills every
    active access token and refresh token so the user is signed out of
    all devices immediately. No reauth required for the operator (the
    superadmin gate is already the strongest auth we have).
    """
    operator = await _require_superadmin(request)
    target = await _resolve_target(body.target_user_type, body.target_user_id, body.target_email)
    target_id = str(target["id"])
    errs = await _revoke_target_sessions(body.target_user_type, target_id)

    from ..services.admin_audit import log_admin_action
    try:
        await log_admin_action(
            teacher_id=str(operator["id"]),
            action="revoke_sessions",
            target_type=body.target_user_type,
            target_id=target_id,
            details={"target_email": target.get("email")},
            request=request,
        )
    except Exception:
        _log.exception("[sar.revoke] audit write failed")

    return {"ok": True, "revoked": True, "errors": errs or None}
