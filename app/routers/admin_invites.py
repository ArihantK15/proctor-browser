"""Invites and templates router. Extracted from admin.py."""

import logging
import uuid as _uuid
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Body
from ..auth import require_admin
from ..auth.scope import resolve_scope, scope_to_teacher_ids, apply_teacher_scope
from ..database import async_table as _atable
from .. import cache as _cache
from ..repositories.questions import load_exam_config as _load_exam_config, load_questions as _load_questions
from ..repositories.sessions import cohort_roll_numbers as _cohort_roll_numbers
from ..invites import _get_invite_base_url, _new_invite_token, _new_access_code, _claim_and_bump_cap
from ..utils import now_ist, fmt_ist
from ..models import InviteStatus
from ..limiter import limiter
from ..jobs import send_invite_email_job
from ..models import InviteRecipient, SendInvitesBody, SaveTemplateIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _exam_registration_url(base_url: str, teacher_id: str, exam_id: str) -> str:
    return f"{base_url}/register?t={teacher_id}&e={exam_id}"


# ──────────────────────────────────────────────────────────────────
# Reusable invite-mint + send helper. Called from BOTH:
#   1. POST /api/v1/admin/invites/send (the explicit "Email Invites"
#      tool on the dashboard) — wraps a batch and applies the cap.
#   2. POST /api/v1/admin/register-students-bulk +
#      POST /api/v1/admin/students/import-csv — the just-registered
#      student gets an invite right away so they aren't left to
#      "wait for someone to send them a link." See
#      _process_student_rows() in admin_students.py.
#
# Behaviour mirrors the inline path in send_invites() (line 23ish):
#   - Upsert the student_invites row scoped to (teacher_id, email,
#     exam_id) so a re-run is idempotent (token rotates; status
#     reset; no duplicate).
#   - Enqueue send_invite_email_job. If the provider returns a real
#     msg_id, flip status → SENT + record sent_at.
#   - Returns dict with {ok, status, msg_id, error}.
#
# Cap accounting is the caller's responsibility — different callers
# want different cap semantics (the bulk-register path counts each
# row against the same daily cap, but doesn't fail-closed on cap
# overflow — it just skips the email and lets the row land).
# ──────────────────────────────────────────────────────────────────


async def _mint_and_send_invite_for_student(
    *,
    teacher: dict,
    email: str,
    full_name: str,
    roll_number: str,
    exam_id: str,
    exam_title: str,
    base_url: str,
    custom_message: Optional[str] = None,
) -> dict:
    """Mint a token + (idempotent) upsert student_invites + enqueue email.

    Returns a dict the caller can aggregate:
      {ok: bool, status: 'sent'|'failed'|'skipped', msg_id: str|None,
       error: str|None, invite_url: str}
    """
    tid = str(teacher["id"])
    email = (email or "").strip().lower()
    roll = (roll_number or "").strip().upper()
    if not email or not roll:
        return {"ok": False, "status": "skipped",
                "error": "missing email or roll", "msg_id": None,
                "invite_url": ""}

    token = _new_invite_token()
    invite_url = f"{base_url}/invite/{token}"
    download_url = f"{base_url}/download"
    registration_url = _exam_registration_url(base_url, tid, exam_id)

    invite_row = {
        "id": _uuid.uuid4(),
        "teacher_id": tid,
        "email": email,
        "full_name": full_name,
        "roll_number": roll,
        "exam_id": exam_id,
        "token": token,
        "status": InviteStatus.QUEUED,
        "sent_at": None,
        "access_code": None,
        "custom_message": custom_message,
    }

    # Idempotency: if a row already exists for (teacher_id, email,
    # exam_id), update it (rotates token + clears bounce state) so
    # re-running an import never double-rows the same student.
    try:
        existing = (await _atable("student_invites")
                    .select("id")
                    .eq("teacher_id", tid)
                    .eq("email", email)
                    .eq("exam_id", exam_id)
                    .limit(1)
                    .execute()).data or []
    except Exception as e:
        logger.warning("[InviteHelper] existing-lookup failed for %s/%s: %s",
                       tid, email, e)
        existing = []

    try:
        if existing:
            update_row = dict(invite_row)
            update_row.pop("id", None)
            await (_atable("student_invites")
                   .update(update_row)
                   .eq("id", existing[0]["id"])
                   .execute())
        else:
            await _atable("student_invites").insert(invite_row).execute()
    except Exception as e:
        logger.warning("[InviteHelper] upsert failed for %s/%s: %s",
                       tid, email, e)
        return {"ok": False, "status": "failed",
                "error": f"row upsert: {type(e).__name__}",
                "msg_id": None, "invite_url": invite_url}

    send_result = send_invite_email_job(
        to_email=email,
        to_name=full_name,
        exam_title=exam_title,
        invite_url=invite_url,
        download_url=download_url,
        roll_number=roll,
        registration_url=registration_url,
        custom_message=custom_message,
        teacher_name=teacher.get("email"),
    )
    provider_msg_id = (send_result or {}).get("provider_msg_id")
    if send_result.get("ok") and provider_msg_id and provider_msg_id != "noop":
        try:
            await (_atable("student_invites")
                   .update({
                       "status": InviteStatus.SENT,
                       "sent_at": now_ist().isoformat(),
                       "provider_msg_id": provider_msg_id,
                   })
                   .eq("teacher_id", tid).eq("email", email)
                   .eq("exam_id", exam_id).execute())
        except Exception as e:
            logger.warning("[InviteHelper] post-send status update failed: %s", e)
        return {"ok": True, "status": "sent",
                "msg_id": provider_msg_id, "error": None,
                "invite_url": invite_url}
    if send_result.get("ok"):
        # noop backend (e.g. dev with RESEND_API_KEY unset) — record
        # the row but don't claim "sent." Status stays QUEUED.
        return {"ok": True, "status": "skipped",
                "msg_id": None, "error": "noop provider",
                "invite_url": invite_url}
    return {"ok": False, "status": "failed",
            "msg_id": None,
            "error": send_result.get("error") or "send failed",
            "invite_url": invite_url}


@router.post("/api/v1/admin/invites/send")
@limiter.limit("5/minute")
async def send_invites(body: SendInvitesBody, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Idempotency check
    if body.idempotency_key:
        from ..services.idempotency import check_idempotency, mark_idempotent, idempotency_key as _idk
        _k = _idk("invite-send", tid, body.idempotency_key)
        cached = await check_idempotency(_k)
        if cached:
            return cached

    base_url = _get_invite_base_url()

    # Expand cohort (group_id / batch) into additional invite recipients.
    # Merged with explicit recipients, deduplicated by email.
    recipients = list(body.recipients)
    if body.group_id or body.batch:
        try:
            tids = [tid]
            rolls = await _cohort_roll_numbers(tids, group_id=body.group_id, batch=body.batch)
            if rolls and "__none__" not in rolls:
                cohort_rows = (await _atable("students")
                               .select("full_name,email,roll_number")
                               .in_("roll_number", list(rolls))
                               .eq("teacher_id", tid)
                               .execute()).data or []
                seen_emails = {r.email.strip().lower() for r in recipients if r.email}
                for s in cohort_rows:
                    email = (s.get("email") or "").strip().lower()
                    if email and email not in seen_emails:
                        recipients.append(InviteRecipient(
                            email=email,
                            full_name=s.get("full_name") or "",
                            roll_number=str(s.get("roll_number") or ""),
                        ))
                        seen_emails.add(email)
        except Exception:
            logger.exception("send_invites: cohort expansion failed")

    ok, remaining = await _claim_and_bump_cap(tid, len(recipients))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"Daily cap exceeded. {remaining} remaining, {len(recipients)} requested."
        )

    exam_cfg = (await _atable("exam_config")
                .select("*")
                .eq("teacher_id", tid).eq("exam_id", body.exam_id).execute()).data
    cfg = exam_cfg[0] if exam_cfg else {}
    exam_title = cfg.get("exam_title", body.exam_id) if cfg else body.exam_id
    registration_url = _exam_registration_url(base_url, tid, body.exam_id)
    starts_at = fmt_ist(cfg.get("starts_at")) if cfg.get("starts_at") else None
    ends_at = fmt_ist(cfg.get("ends_at")) if cfg.get("ends_at") else None

    results = {"sent": 0, "failed": 0, "skipped": 0, "failures": []}
    for rec in recipients:
        email = rec.email.strip().lower()
        token = _new_invite_token()
        invite_url = f"{base_url}/invite/{token}"
        download_url = f"{base_url}/download"
        access_code = _new_access_code() if body.per_invite_code else None

        invite_row = {
            "id": _uuid.uuid4(),
            "teacher_id": tid,
            "email": email,
            "full_name": rec.full_name,
            "roll_number": rec.roll_number.strip().upper(),
            "exam_id": body.exam_id,
            "token": token,
            "status": InviteStatus.QUEUED,
            "sent_at": None,
            "access_code": access_code,
            "custom_message": body.custom_message,
            "expires_at": body.expires_at,
        }

        # Avoid relying on a deployment-specific UNIQUE constraint for
        # ON CONFLICT (teacher_id,email,exam_id). Some plain-Postgres
        # installs predate that constraint, which made invite sends fail
        # with a 500 even though the logical update is simple.
        existing = (await _atable("student_invites")
                    .select("id")
                    .eq("teacher_id", tid)
                    .eq("email", email)
                    .eq("exam_id", body.exam_id)
                    .limit(1)
                    .execute()).data or []
        if existing:
            update_row = dict(invite_row)
            update_row.pop("id", None)
            await (_atable("student_invites")
                   .update(update_row)
                   .eq("id", existing[0]["id"])
                   .execute())
        else:
            await _atable("student_invites").insert(invite_row).execute()

        send_result = send_invite_email_job(
            to_email=rec.email,
            to_name=rec.full_name,
            exam_title=exam_title,
            invite_url=invite_url,
            download_url=download_url,
            roll_number=rec.roll_number,
            registration_url=registration_url,
            access_code=access_code,
            exam_starts_at=starts_at,
            exam_ends_at=ends_at,
            custom_message=body.custom_message,
            teacher_name=teacher.get("email"),
        )
        provider_msg_id = (send_result or {}).get("provider_msg_id")
        if send_result.get("ok") and provider_msg_id and provider_msg_id != "noop":
            await (_atable("student_invites")
                   .update({
                       "status": InviteStatus.SENT,
                       "sent_at": now_ist().isoformat(),
                       "provider_msg_id": provider_msg_id,
                   })
                   .eq("teacher_id", tid).eq("email", email)
                   .eq("exam_id", body.exam_id).execute())
            results["sent"] += 1
        else:
            reason = send_result.get("error") or (
                "Email provider is not configured for real delivery"
                if provider_msg_id == "noop"
                else "Email provider did not accept the invite"
            )
            await (_atable("student_invites")
                   .update({
                       "status": InviteStatus.FAILED,
                       "provider_msg_id": provider_msg_id,
                   })
                   .eq("teacher_id", tid).eq("email", email)
                   .eq("exam_id", body.exam_id).execute())
            results["failed"] += 1
            results["failures"].append({"email": email, "reason": reason})

    if body.idempotency_key and tid:
        try:
            from ..services.idempotency import mark_idempotent
            await mark_idempotent(_k, results)
        except Exception:
            logger.debug("admin_invites: idempotency mark failed", exc_info=True)

    return results


@router.get("/api/v1/admin/invites/cap-status")
@limiter.limit("30/minute")
async def invite_cap_status(request: Request):
    """Return current daily invite usage + cap for the calling teacher.

    Used by the Email Invites UI to surface "X remaining of Y" and a
    reset button when the counter is at the cap. Without this the
    only signal of cap exhaustion was the 429 response after the
    send attempt — frustrating during demo prep where dry-runs had
    silently consumed quota.
    """
    from datetime import datetime, timezone
    from ..constants import INVITE_DAILY_CAP
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    today = datetime.now(timezone.utc).date().isoformat()
    used = 0
    try:
        row = (await _atable("invite_send_counters").select("count")
               .eq("teacher_id", tid).eq("day", today).execute()).data
        if row:
            used = int(row[0].get("count") or 0)
    except Exception:
        logger.debug("admin_invites: cap-status read failed", exc_info=True)
    return {
        "used": used,
        "cap": INVITE_DAILY_CAP,
        "remaining": max(INVITE_DAILY_CAP - used, 0),
        "day_utc": today,
    }


@router.post("/api/v1/admin/invites/cap-reset")
@limiter.limit("5/hour")
async def invite_cap_reset(request: Request):
    """Reset today's invite counter for the calling teacher to 0.

    Surgical fix for the demo-prep symptom where dry-runs against a
    noop emailer or a flaky Resend setup exhausted the local cap
    while no real emails left the server. Teacher-scoped (can only
    reset own row); rate-limited to 5/hour to prevent abuse.
    """
    from datetime import datetime, timezone
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        await _atable("invite_send_counters").delete()\
            .eq("teacher_id", tid).eq("day", today).execute()
    except Exception as e:
        logger.warning("admin_invites: cap-reset delete failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to reset cap")
    from ..constants import INVITE_DAILY_CAP
    return {"reset": True, "cap": INVITE_DAILY_CAP, "remaining": INVITE_DAILY_CAP}


@router.get("/api/v1/admin/invites")
@limiter.limit("30/minute")
async def list_invites(request: Request, exam_id: Optional[str] = None):
    # Org-rollup, scope-aware: an org admin sees co-teachers' invites (so a
    # co-teacher's exam selected in the org-wide dropdown shows its invites); a
    # plain teacher stays locked to their own.
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    base_url = _get_invite_base_url()

    query = apply_teacher_scope(
        _atable("student_invites").select("*").order("sent_at", desc=True), tids)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    result = await query.execute()

    invites = []
    for row in result.data or []:
        token = row.get("token") or ""
        invites.append({
            "id": row.get("id"),
            "email": row.get("email"),
            "full_name": row.get("full_name"),
            "roll_number": row.get("roll_number"),
            "exam_id": row.get("exam_id"),
            "token_prefix": token[:8] if token else "",
            "status": row.get("status"),
            "invite_url": f"{base_url}/invite/{token}" if token else "",
            "sent_at": row.get("sent_at"),
            "opened_at": row.get("opened_at"),
            "bounced_at": row.get("bounced_at"),
            "provider_msg_id": row.get("provider_msg_id"),
        })
    return {"invites": invites, "total": len(invites)}


@router.delete("/api/v1/admin/invites/{invite_id}")
@limiter.limit("20/hour")
async def revoke_invite(invite_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Atomic UPDATE filtered by BOTH id and teacher_id. The earlier code
    # did a SELECT-then-UPDATE pair; the UPDATE only filtered by id which
    # left a TOCTOU window — if the invite was reassigned between the
    # ownership check and the update, the update would still revoke a
    # row outside our scope. .data is the list of affected rows (under
    # the postgres adapter), so empty = either invite doesn't exist or
    # it belongs to another teacher. We collapse both to 404 to avoid
    # leaking which is which.
    result = (await _atable("student_invites")
              .update({"status": InviteStatus.REVOKED})
              .eq("id", invite_id)
              .eq("teacher_id", tid)
              .execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Invite not found")
    return {"ok": True, "invite_id": invite_id}


@router.post("/api/v1/templates")
@limiter.limit("10/hour")
async def save_template(request: Request, body: SaveTemplateIn):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    cfg = await _load_exam_config(tid, exam_id=body.exam_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Exam not found")

    template_data = {
        "exam_title": cfg.get("exam_title") or cfg.get("title") or "Exam",
        "duration_minutes": cfg.get("duration_minutes", 60),
        "access_code": cfg.get("access_code", ""),
        "shuffle_questions": cfg.get("shuffle_questions", False),
        "shuffle_options": cfg.get("shuffle_options", False),
    }

    questions = []
    if body.include_questions:
        questions = await _load_questions(tid, exam_id=body.exam_id)

    row = {
        "teacher_id": tid,
        "template_name": body.template_name.strip(),
        "exam_title": template_data["exam_title"],
        "duration_minutes": template_data["duration_minutes"],
        "access_code": template_data["access_code"],
        "shuffle_questions": template_data["shuffle_questions"],
        "shuffle_options": template_data["shuffle_options"],
        "questions": questions,
    }
    result = (await _atable("exam_templates")
              .insert(row)
              .execute())
    template_id = result.data[0]["id"] if result.data else None
    return {"ok": True, "template_id": template_id, "questions_count": len(questions)}


@router.get("/api/v1/templates")
@limiter.limit("60/minute")
async def list_templates(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    result = (await _atable("exam_templates")
              .select("id,template_name,exam_title,duration_minutes,access_code,"
                      "shuffle_questions,shuffle_options,created_at,questions")
              .eq("teacher_id", tid)
              .order("created_at", desc=True)
              .execute())
    templates = []
    for t in (result.data or []):
        templates.append({
            "id": t["id"],
            "template_name": t.get("template_name", ""),
            "exam_title": t.get("exam_title", ""),
            "duration_minutes": t.get("duration_minutes", 60),
            "access_code_required": bool(t.get("access_code", "").strip()),
            "shuffle_questions": t.get("shuffle_questions", False),
            "shuffle_options": t.get("shuffle_options", False),
            "questions_count": len(t.get("questions") or []),
            "created_at": fmt_ist(t.get("created_at", "")),
        })
    return {"templates": templates}


@router.post("/api/v1/templates/{template_id}/create-exam")
@limiter.limit("10/hour")
async def create_exam_from_template(template_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    t_result = (await _atable("exam_templates")
                .select("*")
                .eq("id", template_id)
                .eq("teacher_id", tid)
                .execute())
    if not t_result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    tmpl = t_result.data[0]

    import uuid as _uuid_mod
    new_exam_id = str(_uuid_mod.uuid4())

    config_row = {
        "exam_id": new_exam_id,
        "teacher_id": tid,
        "exam_title": tmpl.get("exam_title", "New Exam"),
        "duration_minutes": tmpl.get("duration_minutes", 60),
        "access_code": tmpl.get("access_code", ""),
        "shuffle_questions": tmpl.get("shuffle_questions", False),
        "shuffle_options": tmpl.get("shuffle_options", False),
    }
    (await _atable("exam_config")
     .insert(config_row)
     .execute())

    questions = tmpl.get("questions") or []
    if questions:
        # Re-stamp every question onto the NEW exam + owner. Without this the
        # rows carry the template's stale teacher_id/exam_id (or none at all)
        # and orphan — invisible to the new exam and to every teacher.
        for q in questions:
            q["id"] = str(_uuid_mod.uuid4())
            q["teacher_id"] = tid
            q["exam_id"] = new_exam_id
        (await _atable("questions")
         .insert(questions)
         .execute())

    return {
        "ok": True,
        "exam_id": new_exam_id,
        "exam_title": config_row["exam_title"],
        "questions_copied": len(questions),
    }


@router.delete("/api/v1/templates/{template_id}")
@limiter.limit("20/hour")
async def delete_template(template_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    result = (await _atable("exam_templates")
              .delete()
              .eq("id", template_id)
              .eq("teacher_id", tid)
              .execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


__all__ = ["router"]
