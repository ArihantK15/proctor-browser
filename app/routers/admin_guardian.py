"""Guardian consent endpoints for student privacy (GDPR Art 8 / COPPA).

Teachers:
  - POST /api/v1/admin/guardian/send-request   — send/resend consent email
  - GET  /api/v1/admin/guardian/pending        — list students pending consent

Guardians:
  - GET  /guardian-consent/<token>             — landing page (Grant / Deny)
  - POST /api/v1/guardian/consent              — record grant or deny

The token is a UUID4; only its SHA-256 hash is stored. The GET landing
page shows the student name and two buttons. The POST records the decision
and writes a proof row to consent_records.
"""

import hashlib
import logging
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ..auth import require_admin
from ..auth.scope import resolve_scope, scope_to_teacher_ids, apply_teacher_scope
from ..database import async_table as _atable
from ..limiter import limiter
from ..jobs import enqueue_job, send_guardian_consent_request_job
from ..invites import _get_invite_base_url

_log = logging.getLogger("admin.guardian")

router = APIRouter()


class SendGuardianConsentIn(BaseModel):
    roll_number: str


class GuardianConsentOut(BaseModel):
    ok: bool
    guardian_email: str
    student_name: str
    requested_at: str


class PendingGuardianOut(BaseModel):
    roll_number: str
    full_name: str
    email: str
    guardian_email: str | None
    guardian_consent_requested_at: str | None
    guardian_consent_granted_at: str | None


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _consent_landing_page(student_name: str, raw_token: str) -> HTMLResponse:
    """Landing page with Grant and Deny buttons."""
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Consent request — Procta</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{background:#fff;border-radius:12px;padding:40px;max-width:480px;width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.2)}}
  .card h1{{font-size:20px;margin-bottom:8px;color:#0f172a}}
  .card p{{font-size:14px;line-height:1.5;color:#475569;margin-bottom:24px}}
  .btn-group{{display:flex;gap:12px;justify-content:center}}
  .btn{{padding:12px 28px;border-radius:8px;font-size:15px;font-weight:600;border:none;cursor:pointer;text-decoration:none;display:inline-block}}
  .btn-grant{{background:#059669;color:#fff}}
  .btn-deny{{background:#dc2626;color:#fff}}
  .btn-grant:hover{{background:#047857}}
  .btn-deny:hover{{background:#b91c1c}}
  .muted{{font-size:12px;color:#94a3b8;margin-top:16px}}
</style>
</head><body>
<div class="card" id="consent-card">
  <h1>Consent request</h1>
  <p>A consent request has been sent for <strong>{_esc(student_name)}</strong> to participate in proctored exams through Procta.</p>
  <p>Do you give your consent for this student to use Procta's proctoring services?</p>
  <div class="btn-group">
    <button class="btn btn-grant" data-action="_guardianGrant" data-token="{_esc(raw_token)}">Grant consent</button>
    <button class="btn btn-deny" data-action="_guardianDeny" data-token="{_esc(raw_token)}">Deny consent</button>
  </div>
  <div class="muted" id="consent-status"></div>
</div>
<script src="/static/guardian-consent.js" defer></script>
</body></html>"""
    return HTMLResponse(content=html, status_code=200)


def _escape_html(s: str) -> str:
    """Minimal HTML escape for inline use."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

_esc = _escape_html


# -- Teacher endpoints ---------------------------------------------------------


@router.post("/api/v1/admin/guardian/send-request")
@limiter.limit("10/minute")
async def send_guardian_consent_request(request: Request, body: SendGuardianConsentIn = Body(...)):
    """Send or resend a guardian consent request email.

    Generates a fresh UUID token, stores its SHA-256 hash on the
    student row, and enqueues the email job.
    """
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    roll = body.roll_number.strip().upper()

    # Org-rollup: a plain teacher resolves to their own id; an org admin to
    # every teacher in their org (so they can manage org-wide), bounded to
    # the org — never the whole table.
    q = (
        _atable("students")
        .select("id,roll_number,full_name,guardian_email,guardian_consent_granted_at")
        .eq("roll_number", roll)
    )
    q = apply_teacher_scope(q, tids)
    students = await q.limit(1).execute()
    if not students.data:
        raise HTTPException(status_code=404, detail="Student not found")

    student = students.data[0]
    guardian_email = (student.get("guardian_email") or "").strip()
    if not guardian_email:
        raise HTTPException(
            status_code=422,
            detail="Student has no guardian_email set.",
        )
    if student.get("guardian_consent_granted_at"):
        raise HTTPException(
            status_code=409,
            detail="Guardian consent has already been granted for this student.",
        )

    raw_token = str(_uuid.uuid4())
    token_hash = _token_hash(raw_token)
    now = datetime.now(timezone.utc).isoformat()

    await (
        _atable("students")
        .update({
            "guardian_consent_token_hash": token_hash,
            "guardian_consent_requested_at": now,
        })
        .eq("id", student["id"])
        .execute()
    )

    base_url = _get_invite_base_url()
    consent_url = f"{base_url}/guardian-consent/{raw_token}"

    enqueue_job(
        send_guardian_consent_request_job,
        to_email=guardian_email,
        to_name=guardian_email.split("@")[0],
        student_name=student.get("full_name", roll),
        consent_url=consent_url,
    )

    return GuardianConsentOut(
        ok=True,
        guardian_email=guardian_email,
        student_name=student.get("full_name", roll),
        requested_at=now,
    )


@router.get("/api/v1/admin/guardian/pending")
@limiter.limit("30/minute")
async def list_pending_guardian_consent(request: Request):
    """List students with guardian_email set but consent not yet granted.

    Teacher-scoped: only returns students belonging to the calling teacher
    (or all teachers for admin/superadmin scope).
    """
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    query = (
        _atable("students")
        .select("roll_number,full_name,email,guardian_email,guardian_consent_requested_at,guardian_consent_granted_at")
        .not_.is_("guardian_email", None)
        .is_("guardian_consent_granted_at", None)
        .order("full_name")
    )
    # Org-rollup, bounded to the caller's org — admins must NOT see other
    # tenants' students (the prior "drop the filter for admins" leaked).
    query = apply_teacher_scope(query, tids)

    rows = (await query.execute()).data or []
    return {
        "pending": [
            PendingGuardianOut(
                roll_number=r["roll_number"],
                full_name=r.get("full_name", ""),
                email=r.get("email", ""),
                guardian_email=r.get("guardian_email"),
                guardian_consent_requested_at=r.get("guardian_consent_requested_at"),
                guardian_consent_granted_at=r.get("guardian_consent_granted_at"),
            )
            for r in rows
        ]
    }


# -- Guardian-facing endpoints -------------------------------------------------


class GuardianConsentActionIn(BaseModel):
    token: str
    action: str  # "grant" or "deny"


class GuardianConsentActionResult(BaseModel):
    ok: bool
    action: str
    student_name: str


@router.get("/guardian-consent/{token}")
@limiter.limit("10/minute")
async def guardian_consent_landing(token: str, request: Request):
    """Show the Grant / Deny landing page for the given token."""
    token = token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing consent token")

    token_hash = _token_hash(token)
    students = await (
        _atable("students")
        .select("id,roll_number,full_name,guardian_consent_granted_at")
        .eq("guardian_consent_token_hash", token_hash)
        .limit(1)
        .execute()
    )
    if not students.data:
        raise HTTPException(status_code=404, detail="Invalid or expired consent link")

    student = students.data[0]
    if student.get("guardian_consent_granted_at"):
        html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Consent already recorded</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
  .card{{background:#fff;border-radius:12px;padding:40px;max-width:480px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.2)}}
  .card h1{{font-size:20px;margin-bottom:8px;color:#0f172a}}
  .card p{{font-size:14px;line-height:1.5;color:#475569}}
</style>
</head><body>
<div class="card">
  <h1>Consent already recorded</h1>
  <p>Consent for <strong>{_esc(student.get('full_name', ''))}</strong> has already been recorded.</p>
</div>
</body></html>"""
        return HTMLResponse(content=html, status_code=200)

    return _consent_landing_page(student.get("full_name", "Student"), token)


@router.post("/api/v1/guardian/consent")
@limiter.limit("10/minute")
async def guardian_consent_action(request: Request, body: GuardianConsentActionIn = Body(...)):
    """Record a guardian consent grant or deny.

    Token-only lookup (no student ID in the POST body — no IDOR).
    Writes a consent_records proof row on grant.
    """
    raw_token = body.token.strip()
    action = body.action.strip().lower()
    if not raw_token:
        raise HTTPException(status_code=400, detail="Missing consent token")
    if action not in ("grant", "deny"):
        raise HTTPException(status_code=422, detail="action must be 'grant' or 'deny'")

    token_hash = _token_hash(raw_token)
    students = await (
        _atable("students")
        .select("id,roll_number,full_name,guardian_consent_granted_at,guardian_consent_denied_at")
        .eq("guardian_consent_token_hash", token_hash)
        .limit(1)
        .execute()
    )
    if not students.data:
        raise HTTPException(status_code=404, detail="Invalid or expired consent token")

    student = students.data[0]
    if student.get("guardian_consent_granted_at"):
        return GuardianConsentActionResult(
            ok=True, action=action,
            student_name=student.get("full_name", "Student"),
        )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    client_ip = request.client.host if request.client else "unknown"

    if action == "grant":
        await (
            _atable("students")
            .update({
                "guardian_consent_granted_at": now_iso,
                "guardian_consent_token_hash": None,
            })
            .eq("id", student["id"])
            .execute()
        )
        await (
            _atable("consent_records")
            .insert({
                "user_id": str(student["id"]),
                "user_type": "student",
                "consent_type": "guardian_proctoring",
                "ip_address": client_ip,
            })
            .execute()
        )
    else:
        await (
            _atable("students")
            .update({
                "guardian_consent_denied_at": now_iso,
                "guardian_consent_token_hash": None,
            })
            .eq("id", student["id"])
            .execute()
        )

    return GuardianConsentActionResult(
        ok=True, action=action,
        student_name=student.get("full_name", "Student"),
    )
