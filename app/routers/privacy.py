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

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict, field_validator

from ..auth import require_admin, require_student_account
from pathlib import Path as _Path
from .. import cache as _cache
from ..constants import SCREENSHOTS_DIR as _SCREENSHOTS_DIR
from ..database import async_table as _atable
from ..jobs import enqueue_job
from ..limiter import limiter
from ..services.object_store import delete_prefix as _s3_delete_prefix

_log = logging.getLogger("privacy")

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


class ConsentIn(BaseModel):
    model_config = ConfigDict(strict=True)
    consent_type: str  # 'signup_terms' | 'privacy_policy' | 'phone_camera'


class ObjectionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    grounds: str = ""
    scope: str = "all"

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v):
        if v not in ("proctoring", "all"):
            raise ValueError(f"scope must be 'proctoring' or 'all', got {v!r}")
        return v


async def _resolve_caller(request: Request) -> tuple[str, str, dict[str, Any]]:
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
    meta: dict[str, Any] | None = None,
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


@router.post("/consent/withdraw")
@limiter.limit("10/minute")
async def withdraw_consent(body: ConsentIn, request: Request):
    """Withdraw a previously-given consent (GDPR Art 7(3) / DPDP Act §7(4)).

    Marks the most-recent matching consent record with a `withdrawn_at`
    timestamp (added by phase98 migration). The original record is kept
    as proof that consent was once obtained, satisfying the audit burden;
    the withdrawn_at field shows it is no longer active.
    """
    user_type, user_id, _ = await _resolve_caller(request)
    ip = request.client.host if request.client else ""

    rows = (await _atable("consent_records")
            .select("id")
            .eq("user_id", user_id)
            .eq("user_type", user_type)
            .eq("consent_type", body.consent_type)
            .is_("withdrawn_at", None)
            .order("created_at", desc=True)
            .limit(1)
            .execute()).data or []
    if not rows:
        raise HTTPException(status_code=404,
            detail="No active consent record found to withdraw.")

    from datetime import datetime, timezone
    await _atable("consent_records")\
        .update({"withdrawn_at": datetime.now(timezone.utc).isoformat(),
                 "withdrawn_ip": ip})\
        .eq("id", rows[0]["id"]).execute()
    await _record_privacy_event(request, user_type, user_id, "consent_withdrawn", {"consent_type": body.consent_type})

    return {"status": "withdrawn", "consent_type": body.consent_type}


@router.post("/object")
@limiter.limit("10/minute")
async def object_to_processing(body: ObjectionIn, request: Request):
    """Lodge a right-to-object request (GDPR Art 21 / DPDP Act §9).

    * Students → routed to their controller (teacher) via email.
    * Teachers → routed to privacy@procta.net for DPO handling.
    """
    user_type, user_id, profile = await _resolve_caller(request)

    row = await _atable("objection_records").insert({
        "user_id": user_id,
        "user_type": user_type,
        "grounds": body.grounds or None,
        "scope": body.scope,
        "status": "open",
    }).execute()
    obj_id = row.data[0]["id"] if row.data else None

    from ..jobs.email_jobs import send_objection_to_controller_notice_job

    async def _org_name(org_id: str | None) -> str:
        if not org_id:
            return "Unknown"
        rows = (await _atable("organizations")
                .select("name").eq("id", org_id).limit(1).execute()).data or []
        return (rows[0].get("name") if rows else None) or "Unknown"

    if user_type == "student":
        # Procta is a processor for student exam data — it cannot action the
        # objection itself; it routes to every controller (teacher) the
        # subject is linked to. Resolve via the two proven links: the
        # exam_sessions the student sat, and the roster account mapping.
        # (students.id is a BIGSERIAL roster PK, NOT the caller's account
        # UUID — never join on it.)
        teacher_ids: set[str] = set()
        sessions = (await _atable("exam_sessions")
                    .select("teacher_id").eq("student_id", user_id).execute()).data or []
        roster = (await _atable("students")
                  .select("teacher_id").eq("account_id", user_id).execute()).data or []
        for r in (*sessions, *roster):
            if r.get("teacher_id"):
                teacher_ids.add(str(r["teacher_id"]))

        notified: list[str] = []
        for tid in teacher_ids:
            trows = (await _atable("teachers")
                     .select("email, full_name, org_id").eq("id", tid).limit(1).execute()).data or []
            teacher = trows[0] if trows else None
            if not teacher or not teacher.get("email"):
                continue
            enqueue_job(send_objection_to_controller_notice_job,
                to_email=teacher["email"],
                to_name=teacher.get("full_name") or "",
                org_name=await _org_name(teacher.get("org_id")),
                user_type="student",
                grounds=body.grounds or "",
                scope=body.scope,
            )
            notified.append(teacher["email"])

        if notified:
            await _atable("objection_records")\
                .update({"status": "routed", "routed_to": ",".join(notified)})\
                .eq("id", obj_id).execute()
        await _record_privacy_event(
            request, user_type, user_id, "right_to_object",
            {"scope": body.scope, "grounds": body.grounds or "", "objection_id": str(obj_id)},
        )
        return {"status": "objection_recorded", "objection_id": str(obj_id), "notified": notified}

    # Teacher: Procta is the controller — route to the DPO for manual handling.
    await _atable("objection_records")\
        .update({"status": "routed", "routed_to": "privacy@procta.net"})\
        .eq("id", obj_id).execute()
    enqueue_job(send_objection_to_controller_notice_job,
        to_email="privacy@procta.net",
        to_name="Privacy Team",
        org_name=await _org_name(profile.get("org_id")),
        user_type="teacher",
        grounds=body.grounds or "",
        scope=body.scope,
    )
    await _record_privacy_event(
        request, user_type, user_id, "right_to_object",
        {"scope": body.scope, "grounds": body.grounds or "", "objection_id": str(obj_id)},
    )
    return {"status": "objection_recorded", "objection_id": str(obj_id), "notified": ["privacy@procta.net"]}


# ── Data export ───────────────────────────────────────────────────


# Hard cap on rows pulled per table during export. Without this, an
# org with months of exam history could OOM the api container or hit
# asyncpg's statement timeout. 25k rows × 10 tables comfortably fits
# in memory and JSON-serialises in <2s; covers the largest production
# org. Truncation is surfaced to the user via the _truncated marker
# inserted alongside the table.
_EXPORT_ROW_CAP = 25000


# Credential / secret columns that must NEVER appear in a data export.
# A profile row is loaded with select("*"), so without this filter a
# password hash or TOTP seed would ship to the requester (and, via the
# operator SAR export, to staff for ANY user). Matched case-insensitively
# by exact name or by the *_hash / *_secret / *_token suffix.
_PROFILE_SECRET_KEYS = frozenset({
    "password", "password_hash", "totp_secret", "totp_secret_temp",
    "password_reset_token", "password_reset_expires",
    "email_verify_token", "email_verification_token", "refresh_token",
    "api_key", "secret",
})


def _redact_profile(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip credential/secret columns from a profile row before export.

    Defends both the self-service `/export` and the operator-run SAR
    export — neither should ever return a password hash, TOTP seed, or
    reset token to the requester.
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for k in list(out.keys()):
        kl = k.lower()
        if kl in _PROFILE_SECRET_KEYS or kl.endswith(("_hash", "_secret", "_token")):
            out.pop(k, None)
    return out


async def _safe_fetch(
    table: str,
    *,
    eq: dict[str, Any] | None = None,
    columns: str = "*",
    limit: int = _EXPORT_ROW_CAP,
) -> list[dict[str, Any]]:
    """Best-effort SELECT — returns [] on error rather than failing the
    whole export. A missing column on one table shouldn't deny the
    user their other data.
    """
    try:
        q = _atable(table).select(columns)
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        # Pull `limit + 1` so the caller can detect "more rows existed"
        # without a separate count query.
        result = await q.limit(limit + 1).execute()
        rows = result.data or []
        return rows
    except Exception as e:
        _log.warning("[privacy.export] %s lookup failed: %s", table, e)
        return []


def _maybe_truncate(rows: list[dict[str, Any]], cap: int = _EXPORT_ROW_CAP) -> tuple[list[dict[str, Any]], bool]:
    """If _safe_fetch hit the +1 sentinel, trim back to `cap` and flag.

    Returns (visible_rows, was_truncated). Caller writes both back into
    the export so the user knows the data they're seeing isn't the
    complete set and they should contact support for a fuller dump.
    """
    if len(rows) > cap:
        return rows[:cap], True
    return rows, False


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
        # Lists any table that hit _EXPORT_ROW_CAP; populated below.
        "_truncated_tables": [],
    }

    def _add(key: str, rows: list[dict[str, Any]]) -> None:
        """Insert rows into the export, flagging truncation when hit."""
        visible, truncated = _maybe_truncate(rows)
        data[key] = visible
        if truncated:
            data["_truncated_tables"].append(key)

    if user_type == "teacher":
        data["profile"] = _redact_profile(profile)  # strip credential cols

        # Organization + org members visible to this teacher
        oid = profile.get("org_id")
        if oid:
            org_rows = await _safe_fetch(
                "organizations",
                eq={"id": oid},
                columns="id,name,slug,max_students,created_at",
            )
            data["organization"] = org_rows[0] if org_rows else None
            _add("subscriptions", await _safe_fetch("subscriptions", eq={"org_id": oid}))

        # Tenant data this teacher owns
        _add("exams", await _safe_fetch(
            "exam_config",
            eq={"teacher_id": user_id},
            columns="exam_id,exam_title,exam_status,starts_at,ends_at,duration_minutes,access_code,created_at",
        ))
        _add("questions", await _safe_fetch("questions", eq={"teacher_id": user_id}))
        _add("students", await _safe_fetch("students", eq={"teacher_id": user_id}))
        _add("student_groups", await _safe_fetch("student_groups", eq={"teacher_id": user_id}))
        _add("exam_templates", await _safe_fetch(
            "exam_templates",
            eq={"teacher_id": user_id},
            columns="id,template_name,exam_title,duration_minutes,created_at",
        ))
        _add("exam_sessions", await _safe_fetch("exam_sessions", eq={"teacher_id": user_id}))
        _add("violations", await _safe_fetch("violations", eq={"teacher_id": user_id}))
        _add("answers", await _safe_fetch("answers", eq={"teacher_id": user_id}))
        _add("student_invites", await _safe_fetch(
            "student_invites",
            eq={"teacher_id": user_id},
            columns="id,email,full_name,roll_number,exam_id,status,sent_at,clicked_at,accepted_at,created_at",
        ))
        # API keys — hashes redacted; user gets metadata only
        _add("api_keys", await _safe_fetch(
            "api_keys",
            eq={"teacher_id": user_id},
            columns="id,name,key_prefix,is_active,created_at,last_used_at",
        ))
        _add("grading_audit", await _safe_fetch("grading_audit", eq={"teacher_id": user_id}))
        _add("admin_audit_log", await _safe_fetch("admin_audit_log", eq={"teacher_id": user_id}))

    elif user_type == "student":
        data["profile"] = _redact_profile(profile)  # strip credential cols
        _add("enrollments", await _safe_fetch("students", eq={"account_id": user_id}))
        # Sessions linked by student_id (current scheme) — older rows
        # may be linked by roll_number only, picked up via enrollments.
        _add("exam_sessions", await _safe_fetch("exam_sessions", eq={"student_id": user_id}))
        # Their appeals (filed by this student)
        _add("appeals", await _safe_fetch("appeals", eq={"student_id": user_id}))
        # Sessions discovered, plus their answers + violations.
        session_keys: list[str] = [
            s["session_key"] for s in data["exam_sessions"]
            if s.get("session_key")
        ]
        if session_keys:
            # DPDP §11 / GDPR Art 15 give the student the right to ALL their
            # data — so we never hard-fail a heavy export. We cap the per-
            # session answer/violation fan-out (each session = 2 queries) to
            # bound request time, and flag the cap in `_truncated_tables` the
            # same way every other large table here does, rather than denying
            # the whole export with a 413. A student over the cap still gets
            # their full session list + the detail for the most recent
            # SESSION_CAP sessions, with a machine-readable warning that
            # detail beyond that is available on request.
            SESSION_CAP = 2000
            capped_keys = session_keys[:SESSION_CAP]
            if len(session_keys) > SESSION_CAP:
                data["_truncated_tables"].extend(["answers", "violations"])
            answers: list[dict[str, Any]] = []
            violations: list[dict[str, Any]] = []
            for sk in capped_keys:
                answers.extend(await _safe_fetch("answers", eq={"session_key": sk}))
                violations.extend(await _safe_fetch("violations", eq={"session_key": sk}))
            data["answers"] = answers
            data["violations"] = violations

    # Both roles: consent + auth-event trail
    _add("consent_records", await _safe_fetch(
        "consent_records", eq={"user_id": user_id}
    ))
    _add("auth_events", await _safe_fetch(
        "auth_events",
        eq={"user_id": user_id},
        columns="event_type,ip,user_agent,meta,created_at",
    ))

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


async def _cleanup_redis_frames(user_type: str, user_id: str) -> None:
    """Delete Redis room-frame keys for a user's exam sessions."""
    try:
        if user_type == "teacher":
            rows = await _atable("exam_sessions").select("session_key")\
                .eq("teacher_id", user_id).execute()
        else:
            rows = await _atable("exam_sessions").select("session_key")\
                .eq("student_id", user_id).execute()
        keys = [r["session_key"] for r in (rows.data or []) if r.get("session_key")]
        if not keys:
            return
        for sk in keys:
            _cache.delete(f"{_cache.ROOMFRAME_PREFIX}{sk}")
            _cache.delete(f"{_cache.ROOMCAM_PREFIX}{sk}")
    except Exception as e:
        _log.warning("[privacy.delete] Redis frame cleanup failed: %s", e)


async def _cleanup_screenshots(user_type: str, user_id: str) -> None:
    """Delete screenshot files for a user's exam sessions (local + S3)."""
    try:
        if user_type == "teacher":
            teacher_dir = _Path(_SCREENSHOTS_DIR) / str(user_id)
            if teacher_dir.is_dir():
                import shutil as _shutil
                _shutil.rmtree(teacher_dir, ignore_errors=True)
            # Also delete from S3 — delete_prefix is a synchronous boto3
            # call, offloaded so it doesn't block this worker's event loop.
            await asyncio.to_thread(_s3_delete_prefix, f"{user_id}/")
        else:
            rows = await _atable("exam_sessions").select("teacher_id,roll_number")\
                .eq("student_id", user_id).execute()
            seen: set[tuple[str, str]] = set()
            for r in (rows.data or []):
                tid = r.get("teacher_id")
                roll = r.get("roll_number")
                if not tid or not roll or (tid, roll) in seen:
                    continue
                seen.add((tid, roll))
                student_dir = _Path(_SCREENSHOTS_DIR) / str(tid) / str(roll)
                if student_dir.is_dir():
                    import shutil as _shutil
                    _shutil.rmtree(student_dir, ignore_errors=True)
                # Trailing slash is required: a bare "{tid}/{roll}" prefix
                # also matches sibling rolls (R1 → R10, R12…) and would
                # over-erase other students' screenshots.
                await asyncio.to_thread(_s3_delete_prefix, f"{tid}/{roll}/")
    except Exception as e:
        _log.warning("[privacy.delete] screenshot cleanup failed: %s", e)


@router.post("/delete-request")
@limiter.limit("3/hour")
async def delete_account_request(request: Request):
    """Email a 6-digit OTP to the caller before account deletion.

    Deleting an account is irreversible (anonymisation, not a soft-delete),
    so it's gated by BOTH the existing password reauth AND this OTP —
    proving the caller both knows the password and controls the registered
    email inbox. Mirrors the same account_delete purpose already used by
    the student-side flow (auth.py's student_account_delete_request); this
    is the shared-endpoint equivalent, since /delete itself serves either
    caller type via _resolve_caller.
    """
    from ..services import email_otp
    from ..services.email_otp import OtpRateLimitError
    from ..emailer import send_2fa_otp_email

    user_type, user_id, profile = await _resolve_caller(request)
    email = (profile.get("email") or "").strip().lower()
    try:
        code = await email_otp.issue(user_type, user_id, "account_delete")
    except OtpRateLimitError:
        raise HTTPException(
            status_code=429,
            detail="Too many delete codes requested. Please wait before trying again.",
        )
    await asyncio.to_thread(
        send_2fa_otp_email, email, profile.get("full_name") or "", code, purpose="delete")
    return {"sent": True, "expires_in": 600}


@router.post("/delete")
@limiter.limit("2/hour")
async def delete_account(request: Request,     body: dict[str, Any] = Body(default_factory=dict)):
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
    auth.py:_track_a_hybrid_delete_student_account (notifies the issuing teacher,
    cleans student_invites, handles email_otps + auth_sessions). For
    students with a reauth token we delegate there. For teachers we
    do the cleanup inline since no parallel deeper-flow exists.
    """
    user_type, user_id, profile = await _resolve_caller(request)

    # Reauth gate — same protection the admin auth deep endpoints use.
    from ..auth.admin_auth import clear_teacher_cache, require_reauth_or_403
    require_reauth_or_403(body, user_id, request=request)

    # OTP gate — proves the caller controls the registered email inbox, not
    # just their password (which a session hijack could already reuse via
    # the reauth token above). Get a fresh code via /delete-request first.
    import re as _re
    from ..services import email_otp
    otp_code = _re.sub(r"\D", "", str((body or {}).get("otp_code") or ""))
    if len(otp_code) != 6 or not await email_otp.verify(user_type, user_id, "account_delete", otp_code):
        raise HTTPException(status_code=403, detail="Invalid or expired confirmation code")

    errors: list[str] = []
    anon_local = f"deleted_user_{user_id[:8]}"
    anon_email = f"{anon_local}@deleted.procta.net"

    # Revoke sessions FIRST so even if subsequent steps fail, auth is dead.
    errors.extend(await _revoke_sessions(user_type, user_id))
    await _cleanup_redis_frames(user_type, user_id)

    if user_type == "teacher":
        # Anonymise teacher profile (retained as the "actor" link on
        # auth_events / admin_audit_log / exams / students).
        try:
            await _atable("teachers").update({
                "full_name": "Deleted User",
                "email": anon_email,
                "status": "deleted",
            }).eq("id", user_id).execute()
            clear_teacher_cache(user_id)
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
            from .auth import _track_a_hybrid_delete_student_account
            result = await _track_a_hybrid_delete_student_account(profile, request)
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

    # Delete screenshot files for both teacher and student paths.
    await _cleanup_screenshots(user_type, user_id)

    # Audit trail — required regardless of which path ran.
    await _record_privacy_event(
        request, user_type, user_id, "account_deleted",
        {"errors": errors, "partial": bool(errors)},
    )

    status = "deleted" if not errors else "partial"
    _log.warning("[privacy] account deleted: %s %s (errors=%s)",
                 user_type, user_id, errors)
    return {"status": status, "errors": errors or None}
