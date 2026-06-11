"""Student appeals — dispute violations or grades."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict

from ..auth import require_admin, require_student_account
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.risk import compute_risk_score, generate_session_summary
from ..services.false_positive import explain_flag

_log = logging.getLogger("appeals")

router = APIRouter(prefix="/api/v1", tags=["appeals"])


class AppealIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_key: str
    appeal_type: str  # 'violation' | 'grade' | 'other'
    description: str
    # Optional: dispute ONE specific flag. NULL keeps the legacy session-
    # level appeal (whole-session grade/other). When set, accepting the
    # appeal dismisses exactly this violation and recomputes the score.
    violation_id: Optional[str] = None


class AppealResolveIn(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str  # 'accepted' | 'rejected'
    teacher_note: str = ""


# ── Student: submit an appeal ──────────────────────────────────────


@router.post("/student/appeal")
@limiter.limit("5/hour")
async def submit_appeal(body: AppealIn, request: Request):
    """Submit a dispute for a violation or grade."""
    account = await require_student_account(request)
    student_id = str(account.get("id", ""))
    student_email = account.get("email", "")

    # Verify the session belongs to this student
    session = await _atable("exam_sessions").select("*").eq("session_key", body.session_key).limit(1).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")
    s = session.data[0]
    if str(s.get("student_id", "")).upper() != student_id.upper() or \
       str(s.get("email", "")).lower() != student_email.lower():
        raise HTTPException(status_code=403, detail="Session does not belong to you")

    teacher_id = s.get("teacher_id", "")
    exam_id = s.get("exam_id", "")

    # If the student is disputing a specific flag, verify that flag exists
    # AND belongs to this same session. Without this check a student could
    # link any violation_id (including another student's flag) to their
    # appeal, and an accepted resolution would then dismiss a stranger's
    # violation. Scope the lookup to body.session_key (already proven to be
    # this student's session above) so the link can only point at their own
    # flags.
    violation_id = None
    if body.violation_id:
        v = await _atable("violations").select("id")\
            .eq("id", body.violation_id)\
            .eq("session_key", body.session_key)\
            .limit(1).execute()
        if not v.data:
            raise HTTPException(status_code=404, detail="Flag not found for this session")
        violation_id = body.violation_id

    # NOTE: the appeals table has no `email` column (see phase51 schema)
    # — student identity is session_key + student_id + roll_number, and
    # the teacher resolves email separately. Inserting email here raised
    # UndefinedColumnError → 500 on every appeal submission.
    await _atable("appeals").insert({
        "session_key":  body.session_key,
        "student_id":   student_id,
        "exam_id":      exam_id,
        "teacher_id":   teacher_id,
        "appeal_type":  body.appeal_type,
        "description":  body.description[:1000],
        "status":       "pending",
        "violation_id": violation_id,
    }).execute()

    return {"status": "submitted", "message": "Your appeal has been submitted for teacher review."}


# ── Student: read back appeal statuses ────────────────────────────


@router.get("/student/appeals")
@limiter.limit("20/minute")
async def list_student_appeals(request: Request):
    """Return all appeals submitted by the logged-in student, newest first."""
    account = await require_student_account(request)
    student_id = str(account.get("id", ""))

    # `violation_id` + `resolution` are surfaced so the student can see WHICH
    # flag they disputed and the concrete outcome ('flag_dismissed') — this
    # polled list is the in-app notification surface (students have no live
    # SSE channel; the appeals view re-fetches and reflects the resolution).
    result = await _atable("appeals")\
        .select("id,session_key,exam_id,appeal_type,description,status,teacher_note,resolution,violation_id,created_at,resolved_at")\
        .eq("student_id", student_id)\
        .order("created_at", desc=True)\
        .limit(50)\
        .execute()
    return {"appeals": result.data or []}


# ── Student: explainable evidence for their own session ───────────


@router.get("/student/session/{session_key}/evidence")
@limiter.limit("20/minute")
async def student_session_evidence(session_key: str, request: Request):
    """Show the student WHY their session was flagged — the same explainable
    evidence the teacher already sees, scoped strictly to their own session.

    PRIVACY BOUNDARY (a selling point — keep it intact): this endpoint returns
    ONLY flag metadata (type, severity, time, calibrated confidence), the risk
    breakdown, and the narrative — all already computed server-side. It NEVER
    returns frames, audio, or any media. Per-flag `id` is included so the
    student can dispute one specific flag (pre-fills violation_id on the appeal).
    """
    account = await require_student_account(request)
    student_id = str(account.get("id", ""))
    student_email = account.get("email", "")

    session = await _atable("exam_sessions").select("*").eq("session_key", session_key).limit(1).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")
    s = session.data[0]
    if str(s.get("student_id", "")).upper() != student_id.upper() or \
       str(s.get("email", "")).lower() != student_email.lower():
        raise HTTPException(status_code=403, detail="Session does not belong to you")
    teacher_id = str(s.get("teacher_id", ""))

    viol = await _atable("violations")\
        .select("id,violation_type,severity,detection_confidence,created_at,dismissed_at,dismissed_reason")\
        .eq("session_key", session_key)\
        .order("created_at")\
        .execute()
    rows = viol.data or []

    # Sensitivity defaults to 'balanced' — explain_flag's threshold notes are
    # framed against this preset. (Per-exam sensitivity override is a future
    # enhancement; defaulting keeps the student view honest without an extra
    # query that could fail and block the page.)
    flags = []
    for r in rows:
        exp = explain_flag(r, sensitivity="balanced")
        flags.append({
            "id": r.get("id"),
            "type": r.get("violation_type"),
            "severity": r.get("severity"),
            "at": r.get("created_at"),
            "dismissed": bool(r.get("dismissed_at")),
            "dismissed_reason": r.get("dismissed_reason"),
            # Student-helpful subset of explain_flag — omits teacher-only
            # fields like human_review_recommended.
            "confidence_label": exp["confidence_label"],
            "reliability": exp["reliability"],
            "reason_codes": exp["reason_codes"],
            "explanation": exp["explanation"],
        })

    risk = await compute_risk_score(session_key, teacher_id=teacher_id)
    summary = generate_session_summary(rows, {"risk_score": risk.get("risk_score")})

    return {
        "session_key": session_key,
        "risk": {
            "score": risk.get("risk_score"),
            "label": risk.get("label"),
            "breakdown": risk.get("breakdown", {}),
        },
        "summary": {
            "narrative": summary.get("narrative"),
            "severity": summary.get("severity"),
        },
        "flags": flags,
    }


# ── Teacher: list appeals ──────────────────────────────────────────


@router.get("/admin/appeals")
@limiter.limit("30/minute")
async def list_appeals(request: Request, exam_id: str = None, status: str = None):
    """List appeals for the caller's scope (own for a teacher, org-wide for an admin)."""
    from ..auth.scope import resolve_scope, scope_to_teacher_ids, apply_teacher_scope
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    q = apply_teacher_scope(_atable("appeals").select("*"), tids)
    if exam_id:
        q = q.eq("exam_id", exam_id)
    if status:
        q = q.eq("status", status)
    q = q.order("created_at", desc=True)

    result = await q.execute()
    return {"appeals": result.data or []}


# ── Teacher: resolve an appeal ─────────────────────────────────────


@router.post("/admin/appeals/{appeal_id}/resolve")
@limiter.limit("30/minute")
async def resolve_appeal(appeal_id: str, body: AppealResolveIn, request: Request):
    """Accept or reject a student appeal.

    When a flag-linked appeal is ACCEPTED, this closes the due-process loop:
      1. the disputed violation is dismissed (dismissed_reason='appeal_accepted'),
      2. the cached risk score is invalidated and recomputed so the score
         actually drops (dismissed flags no longer count — see risk.py),
      3. the resolution is recorded both on the appeal row (resolution=
         'flag_dismissed') and as an append-only admin_audit_log entry.
    Rejections and session-level appeals (no violation_id) just stamp status.
    """
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    appeal = await _atable("appeals").select("*").eq("id", appeal_id).eq("teacher_id", tid).limit(1).execute()
    if not appeal.data:
        raise HTTPException(status_code=404, detail="Appeal not found")
    appeal_row = appeal.data[0]
    violation_id = appeal_row.get("violation_id")
    session_key = appeal_row.get("session_key", "")
    prev_status = appeal_row.get("status", "pending")

    now = datetime.now(timezone.utc).isoformat()
    resolution = None
    new_risk = None

    # ── Remediation hook: accepting a flag-linked appeal actually clears it ──
    if body.status == "accepted" and violation_id:
        # Dismiss the disputed flag, scoped to this teacher for authz. The
        # is_("dismissed_at","null") guard makes the write idempotent — a
        # re-resolve won't re-stamp an already-dismissed flag.
        try:
            await _atable("violations").update({
                "dismissed_at": now,
                "dismissed_reason": "appeal_accepted",
            }).eq("id", violation_id)\
              .eq("teacher_id", tid)\
              .is_("dismissed_at", "null")\
              .execute()
            resolution = "flag_dismissed"
        except Exception:
            _log.warning("resolve_appeal: dismiss of violation %s failed", violation_id, exc_info=True)

        # Invalidate the cached risk score then recompute so the dashboard
        # response reflects the cleared flag immediately (no reload).
        if session_key:
            try:
                from .. import cache as _cache
                if _cache:
                    _cache.delete(f"risk_score:{session_key}")
            except Exception:
                _log.debug("resolve_appeal: risk cache invalidate failed", exc_info=True)
            try:
                new_risk = await compute_risk_score(session_key, teacher_id=tid)
            except Exception:
                _log.debug("resolve_appeal: risk recompute failed", exc_info=True)

    await _atable("appeals").update({
        "status": body.status,
        "teacher_note": body.teacher_note[:500],
        "resolution": resolution,
        "resolved_at": now,
    }).eq("id", appeal_id).execute()

    # ── Accountability: append-only audit row (best-effort, never raises) ──
    try:
        from ..services.admin_audit import log_admin_action
        await log_admin_action(
            teacher_id=tid,
            action="resolve_appeal",
            target_type="appeal",
            target_id=appeal_id,
            before_data={"status": prev_status, "violation_id": violation_id},
            after_data={"status": body.status, "resolution": resolution},
            details={"session_key": session_key},
            request=request,
        )
    except Exception:
        _log.debug("resolve_appeal: audit log failed", exc_info=True)

    out = {"status": "resolved", "resolution": resolution}
    if new_risk is not None:
        out["risk_score"] = new_risk.get("risk_score")
        out["risk_label"] = new_risk.get("label")
    return out
