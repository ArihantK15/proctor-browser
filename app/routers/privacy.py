"""Privacy center — consent recording, data subject access requests (DSAR),
account deletion.

Covers the data-subject rights in India's DPDP Act §11–13 and the
parallel GDPR Articles 15–17:

  POST /api/v1/privacy/consent  — record explicit consent (§7, Art 7)
  GET  /api/v1/privacy/export   — right of access / portability (§11, Art 15+20)
  POST /api/v1/privacy/delete   — right of erasure (§13, Art 17)

Both export and delete are audit-logged to auth_events so we have a
verifiable trail of how we honoured each request — required for
regulator audits and customer support.

Retention exceptions (data NOT deleted on erasure request):
  - consent_records: kept as proof we obtained consent before processing.
  - auth_events: kept for security forensics + compliance audit trail.
  - admin_audit_log: kept for security forensics (teacher actions).
  - Payment-derived records: kept for tax compliance (7 years in India).
  - Exam outputs (scores, violations, answers) on completed sessions:
    anonymised but retained — the issuing teacher still needs analytics
    on past exams, and that data is no longer linkable to the user
    after anonymisation.

See docs/PRIVACY.md for the full retention matrix and SAR procedure.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

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


async def _resolve_caller(request: Request) -> tuple[str, str, dict]:
    """Return (user_type, user_id, profile_row) for the authenticated caller.

    Tries teacher auth first, then student-account auth. Raises 401 if
    neither matches.
    """
    try:
        teacher = await require_admin(request)
        return "teacher", str(teacher["id"]), teacher
    except HTTPException:
        pass
    try:
        student = await require_student_account(request)
        return "student", str(student["id"]), student
    except HTTPException:
        raise HTTPException(status_code=401, detail="Authentication required")


async def _record_privacy_event(
    request: Request,
    user_type: str,
    user_id: str,
    event: str,
    meta: dict | None = None,
) -> None:
    """Append to auth_events for regulator audit trail. Best-effort."""
    try:
        ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        await _atable("auth_events").insert({
            "user_kind": "teacher" if user_type == "teacher" else "student_account",
            "user_id": user_id,
            "email": None,
            "event_type": event,
            "ip": ip,
            "user_agent": user_agent,
            "meta": json.dumps(meta or {}),
        }).execute()
    except Exception:
        _log.exception("[privacy] failed to record %s event for %s/%s",
                       event, user_type, user_id)


# ── Record consent ────────────────────────────────────────────────


@router.post("/consent")
@limiter.limit("30/minute")
async def record_consent(body: ConsentIn, request: Request):
    """Record a consent action for the authenticated user (DPDP §7 / GDPR Art 7).

    Stores the consent_type + IP + timestamp. Retained even after
    account deletion as proof of obtained consent.
    """
    user_type, user_id, _ = await _resolve_caller(request)
    ip = request.client.host if request.client else ""

    await _atable("consent_records").insert({
        "user_id": user_id,
        "user_type": user_type,
        "consent_type": body.consent_type,
        "ip_address": ip,
    }).execute()

    return {"status": "recorded"}


# ── Data export ───────────────────────────────────────────────────


async def _safe_fetch(table: str, *, eq: dict | None = None, columns: str = "*") -> list[dict]:
    """Best-effort SELECT — returns [] on error rather than failing the
    whole export. A missing column on one table shouldn't deny the
    user their other data.
    """
    try:
        q = _atable(table).select(columns)
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        result = await q.execute()
        return result.data or []
    except Exception as e:
        _log.warning("[privacy.export] %s lookup failed: %s", table, e)
        return []


@router.get("/export")
@limiter.limit("5/hour")
async def export_data(request: Request):
    """Return a JSON dump of all data Procta holds about the caller.

    DPDP §11 right of access + GDPR Art 15 + Art 20 (portability —
    structured, commonly used, machine-readable format).

    Audit-logged to auth_events so we can prove we honoured the
    request if a regulator asks.
    """
    user_type, user_id, profile = await _resolve_caller(request)

    data: dict[str, Any] = {
        "user_type": user_type,
        "user_id": user_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 2,
    }

    if user_type == "teacher":
        data["profile"] = profile  # already loaded by _resolve_caller

        # Organization + org members visible to this teacher
        oid = profile.get("org_id")
        if oid:
            org_rows = await _safe_fetch(
                "organizations",
                eq={"id": oid},
                columns="id,name,slug,max_students,created_at",
            )
            data["organization"] = org_rows[0] if org_rows else None
            data["subscriptions"] = await _safe_fetch("subscriptions", eq={"org_id": oid})

        # Tenant data this teacher owns
        data["exams"] = await _safe_fetch(
            "exam_config",
            eq={"teacher_id": user_id},
            columns="exam_id,exam_title,exam_status,starts_at,ends_at,duration_minutes,access_code,created_at",
        )
        data["questions"] = await _safe_fetch("questions", eq={"teacher_id": user_id})
        data["students"] = await _safe_fetch("students", eq={"teacher_id": user_id})
        data["student_groups"] = await _safe_fetch("student_groups", eq={"teacher_id": user_id})
        data["exam_templates"] = await _safe_fetch(
            "exam_templates",
            eq={"teacher_id": user_id},
            columns="id,template_name,exam_title,duration_minutes,created_at",
        )
        data["exam_sessions"] = await _safe_fetch("exam_sessions", eq={"teacher_id": user_id})
        data["violations"] = await _safe_fetch("violations", eq={"teacher_id": user_id})
        data["answers"] = await _safe_fetch("answers", eq={"teacher_id": user_id})
        data["student_invites"] = await _safe_fetch(
            "student_invites",
            eq={"teacher_id": user_id},
            columns="id,email,full_name,roll_number,exam_id,status,sent_at,clicked_at,accepted_at,created_at",
        )
        # API keys — hashes redacted; user gets metadata only
        data["api_keys"] = await _safe_fetch(
            "api_keys",
            eq={"teacher_id": user_id},
            columns="id,name,key_prefix,is_active,created_at,last_used_at",
        )
        data["grading_audit"] = await _safe_fetch("grading_audit", eq={"teacher_id": user_id})
        data["admin_audit_log"] = await _safe_fetch("admin_audit_log", eq={"teacher_id": user_id})

    elif user_type == "student":
        data["profile"] = profile
        data["enrollments"] = await _safe_fetch("students", eq={"account_id": user_id})
        # Sessions linked by student_id (current scheme) — older rows
        # may be linked by roll_number only, picked up via enrollments.
        data["exam_sessions"] = await _safe_fetch("exam_sessions", eq={"student_id": user_id})
        # Their appeals (filed by this student)
        data["appeals"] = await _safe_fetch("appeals", eq={"student_id": user_id})
        # Sessions discovered, plus their answers + violations
        session_keys: list[str] = [
            s["session_key"] for s in data["exam_sessions"]
            if s.get("session_key")
        ]
        if session_keys:
            # answer + violation pulls are session-scoped, so we run one
            # query per session_key. Not amazing for huge histories but
            # an export request is rare + asynchronous from a user POV.
            answers: list[dict] = []
            violations: list[dict] = []
            for sk in session_keys[:200]:  # cap to avoid pathological cases
                answers.extend(await _safe_fetch("answers", eq={"session_key": sk}))
                violations.extend(await _safe_fetch("violations", eq={"session_key": sk}))
            data["answers"] = answers
            data["violations"] = violations

    # Both roles: consent + auth-event trail
    data["consent_records"] = await _safe_fetch(
        "consent_records", eq={"user_id": user_id}
    )
    data["auth_events"] = await _safe_fetch(
        "auth_events",
        eq={"user_id": user_id},
        columns="event_type,ip,user_agent,meta,created_at",
    )

    await _record_privacy_event(
        request, user_type, user_id, "data_exported",
        {"tables": sorted(k for k, v in data.items() if isinstance(v, list))},
    )

    return data


# ── Account deletion ──────────────────────────────────────────────


async def _revoke_sessions(user_type: str, user_id: str) -> list[str]:
    """Best-effort revoke of every active session + refresh token.

    Called from delete_account so that even if the per-table erasure
    has a hiccup, the user's auth surface is dead before we return.
    """
    errs: list[str] = []
    user_kind = "teacher" if user_type == "teacher" else "student_account"
    try:
        await _atable("auth_sessions").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_kind", user_kind).eq("user_id", user_id).execute()
    except Exception as e:
        _log.warning("[privacy.delete] auth_sessions revoke failed: %s", e)
        errs.append(f"auth_sessions: {type(e).__name__}")
    try:
        await _atable("refresh_tokens").update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }).is_("revoked_at", "null").eq("kind", "teacher" if user_type == "teacher" else "student").eq("user_id", user_id).execute()
    except Exception as e:
        _log.warning("[privacy.delete] refresh_tokens revoke failed: %s", e)
        errs.append(f"refresh_tokens: {type(e).__name__}")
    return errs


@router.post("/delete")
@limiter.limit("2/hour")
async def delete_account(request: Request, body: dict = Body(default_factory=dict)):
    """Erase the caller's account and personal data (DPDP §13 / GDPR Art 17).

    Anonymisation strategy:
      - Identifiers (name, email) on retained rows are replaced with
        'Deleted User' / 'deleted_user_<8hex>@deleted.procta.net'.
      - Active sessions + refresh tokens are revoked immediately so
        the now-deleted user can't log in even if their browser still
        has a JWT.
      - Records that must be retained for legal/forensic reasons
        (consent_records, auth_events, admin_audit_log, payment data,
        anonymised exam outputs) are preserved per the policy
        documented in docs/PRIVACY.md.

    The student-account flow has a more thorough path through
    auth.py:_perform_student_delete (notifies the issuing teacher,
    cleans student_invites, handles Supabase user delete). For
    students with a reauth token we delegate there. For teachers we
    do the cleanup inline since no parallel deeper-flow exists.
    """
    user_type, user_id, profile = await _resolve_caller(request)

    # Reauth gate — same protection the admin auth deep endpoints use.
    from ..auth.admin_auth import require_reauth_or_403
    require_reauth_or_403(body, user_id, request=request)

    errors: list[str] = []
    anon_local = f"deleted_user_{user_id[:8]}"
    anon_email = f"{anon_local}@deleted.procta.net"

    # Revoke sessions FIRST so even if subsequent steps fail, auth is dead.
    errors.extend(await _revoke_sessions(user_type, user_id))

    if user_type == "teacher":
        # Anonymise teacher profile (retained as the "actor" link on
        # auth_events / admin_audit_log / exams / students).
        try:
            await _atable("teachers").update({
                "full_name": "Deleted User",
                "email": anon_email,
                "status": "deleted",
            }).eq("id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] teacher anonymise failed for %s", user_id)
            errors.append(f"teachers: {type(e).__name__}")

        # Students owned by this teacher: anonymise (keep enrollment
        # records for the org's other admins and historical exam data).
        try:
            await _atable("students").update({
                "full_name": "Deleted User",
                "email": anon_email,
            }).eq("teacher_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] students anonymise failed")
            errors.append(f"students: {type(e).__name__}")

        # Exam titles + access codes carry no PII but blank to be safe.
        try:
            await _atable("exam_config").update({
                "exam_title": "Deleted Exam",
                "access_code": "",
            }).eq("teacher_id", user_id).execute()
        except Exception as e:
            _log.exception("[privacy] exam_config anonymise failed")
            errors.append(f"exam_config: {type(e).__name__}")

        # Revoke API keys + delete Google OAuth tokens (sensitive,
        # never retained for an erased user).
        try:
            await _atable("api_keys").update({"is_active": False}).eq("teacher_id", user_id).execute()
        except Exception as e:
            errors.append(f"api_keys: {type(e).__name__}")
        try:
            await _atable("google_auth_tokens").delete().eq("teacher_id", user_id).execute()
        except Exception as e:
            errors.append(f"google_auth_tokens: {type(e).__name__}")
        # Email OTPs are ephemeral; safe to drop entirely.
        try:
            await _atable("email_otps").delete().eq("user_kind", "teacher").eq("user_id", user_id).execute()
        except Exception:
            pass  # nothing useful to retain on failure

    elif user_type == "student":
        # Delegate to the deeper student delete flow when available —
        # it notifies the teacher + handles supabase + does the same
        # anonymise/delete split as below.
        try:
            from .auth import _perform_student_delete
            result = await _perform_student_delete(request, profile)
            errors.extend(result.get("errors") or [])
        except ImportError:
            # Fallback: inline anonymise + delete if the deeper helper
            # isn't reachable. Keeps the endpoint self-contained.
            try:
                await _atable("student_accounts").update({
                    "full_name": "Deleted User",
                    "email": anon_email,
                }).eq("id", user_id).execute()
            except Exception as e:
                errors.append(f"student_accounts: {type(e).__name__}")
            try:
                await _atable("students").update({
                    "full_name": "Deleted User",
                    "email": anon_email,
                }).eq("account_id", user_id).execute()
            except Exception as e:
                errors.append(f"students: {type(e).__name__}")
            try:
                await _atable("exam_sessions").update({
                    "full_name": "Deleted User",
                    "email": anon_email,
                }).eq("student_id", user_id).execute()
            except Exception as e:
                errors.append(f"exam_sessions: {type(e).__name__}")

    # Audit trail — required regardless of which path ran.
    await _record_privacy_event(
        request, user_type, user_id, "account_deleted",
        {"errors": errors, "partial": bool(errors)},
    )

    status = "deleted" if not errors else "partial"
    _log.warning("[privacy] account deleted: %s %s (errors=%s)",
                 user_type, user_id, errors)
    return {"status": status, "errors": errors or None}
