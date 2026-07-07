"""Student appeals — dispute violations or grades."""

import json
import logging
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Any, Optional, Literal

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from ..auth import require_admin, require_student_account
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.risk import compute_risk_score, generate_session_summary
from ..services.false_positive import explain_flag
from ..services.sessions import (
    collect_session_screenshots as _collect_session_screenshots,
    match_screenshot_for_violation as _match_primary_screenshot,
    match_context_screenshots_for_violation as _match_context_screenshots,
)

_log = logging.getLogger("appeals")

router = APIRouter(prefix="/api/v1", tags=["appeals"])


class AppealIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_key: str
    # Allowlist — reject arbitrary appeal_type values instead of storing them.
    appeal_type: Literal["violation", "grade", "other"]
    description: str
    # Optional: dispute ONE specific flag. NULL keeps the legacy session-
    # level appeal (whole-session grade/other). When set, accepting the
    # appeal dismisses exactly this violation and recomputes the score.
    # violations.id is BIGINT (see phase147), so this is an int. The client
    # (student-app.js) sends it as a STRING, so coerce str→int before strict
    # validation; empty/None → None (session-level); non-numeric → 422 rather
    # than a downstream 500.
    violation_id: int | None = None

    @field_validator("violation_id", mode="before")
    @classmethod
    def _coerce_violation_id(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError("violation_id must be an integer flag id")


class AppealResolveIn(BaseModel):
    model_config = ConfigDict(strict=True)
    # Allowlist — only these two resolutions are valid.
    status: Literal["accepted", "rejected"]
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
    # Ownership: a match on EITHER the account's student_id OR email proves the
    # session is theirs (both are unique to the account). Requiring BOTH — the
    # old behaviour — wrongly 403'd sessions created via the exam/invite flow that
    # stored only one (e.g. a null `email` on the session row), even though the
    # student's own history (roll + teacher based) lists the session — so the
    # "Why flagged?" and "Appeal" buttons appeared but their endpoints rejected.
    _sid_ok = bool(student_id) and str(s.get("student_id", "")).upper() == student_id.upper()
    _email_ok = bool(student_email) and str(s.get("email", "")).lower() == student_email.lower()
    if not (_sid_ok or _email_ok):
        raise HTTPException(status_code=403, detail="Session does not belong to you")

    # Coerce empty → None: a session row with a NULL teacher_id/exam_id yields
    # "" here, and inserting "" into the appeals UUID columns raises asyncpg
    # DataError ("invalid UUID ''"). NULL is the correct representation.
    teacher_id = s.get("teacher_id") or None
    exam_id = s.get("exam_id") or None

    # If the student is disputing a specific flag, verify that flag exists
    # AND belongs to this same session. Without this check a student could
    # link any violation_id (including another student's flag) to their
    # appeal, and an accepted resolution would then dismiss a stranger's
    # violation. Scope the lookup to body.session_key (already proven to be
    # this student's session above) so the link can only point at their own
    # flags.
    rid = getattr(request.state, "request_id", "") or "-"
    try:
        violation_id = None
        if body.violation_id:
            v = await _atable("violations").select("id")\
                .eq("id", body.violation_id)\
                .eq("session_key", body.session_key)\
                .limit(1).execute()
            if not v.data:
                raise HTTPException(status_code=404, detail="Flag not found for this session")
            violation_id = body.violation_id

        # Reject a duplicate: one pending appeal per target is enough. A double-
        # click or double-submit shouldn't spam the teacher with identical
        # pending rows (the 5/hour limiter is a coarse backstop; this is the
        # precise one). A flag-linked appeal (violation_id set) and the session-
        # level appeal (violation_id null) are DISTINCT disputes and may coexist,
        # so the duplicate test is per-target. session_key already proves the
        # session is this student's, so scope by it rather than student_id (which
        # may be blank when ownership matched by email only).
        pending = (await _atable("appeals").select("id,violation_id")
                   .eq("session_key", body.session_key)
                   .eq("status", "pending")
                   .execute()).data or []
        if any((p.get("violation_id") or None) == (violation_id or None) for p in pending):
            raise HTTPException(
                status_code=409,
                detail="You already have a pending appeal for this — a teacher will review it soon.")

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
    except HTTPException:
        raise
    except Exception as e:
        err_lower = str(e).lower()
        # uq_appeals_session_student_type_pending (phase88) already makes
        # concurrent double-submits atomic at the DB level — a race that
        # loses just hits this unique-violation, which used to fall through
        # to a raw 500 instead of a message the student can act on.
        if "duplicate key" in err_lower or "unique constraint" in err_lower:
            raise HTTPException(status_code=409,
                detail="You already have a pending appeal for this — a teacher will review it soon.")
        _log.error("[student/appeal] submit failed (rid=%s, session=%s): %s",
                   rid, body.session_key, e, exc_info=True)
        raise HTTPException(status_code=500,
            detail=f"Failed to submit appeal ({type(e).__name__}). request_id: {rid}")

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
    # Ownership: a match on EITHER the account's student_id OR email proves the
    # session is theirs (both are unique to the account). Requiring BOTH — the
    # old behaviour — wrongly 403'd sessions created via the exam/invite flow that
    # stored only one (e.g. a null `email` on the session row), even though the
    # student's own history (roll + teacher based) lists the session — so the
    # "Why flagged?" and "Appeal" buttons appeared but their endpoints rejected.
    _sid_ok = bool(student_id) and str(s.get("student_id", "")).upper() == student_id.upper()
    _email_ok = bool(student_email) and str(s.get("email", "")).lower() == student_email.lower()
    if not (_sid_ok or _email_ok):
        raise HTTPException(status_code=403, detail="Session does not belong to you")
    teacher_id = str(s.get("teacher_id", ""))

    # Wrap the evidence computation (risk scoring + narrative generation) so an
    # unhandled throw in compute_risk_score / generate_session_summary leaves a
    # traceable breadcrumb instead of an opaque 500 — same hardening student_exams
    # got after a real incident. Ownership 404/403 above stay as proper 4xx.
    rid = getattr(request.state, "request_id", "") or "-"
    try:
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
    except Exception as e:
        _log.error("[student/evidence] failed (rid=%s, session=%s): %s",
                   rid, session_key, e, exc_info=True)
        raise HTTPException(status_code=500,
            detail=f"Failed to load evidence ({type(e).__name__}). request_id: {rid}")


# ── Teacher: list appeals ──────────────────────────────────────────


@router.get("/admin/appeals")
@limiter.limit("30/minute")
async def list_appeals(request: Request, exam_id: Optional[str] = None, status: Optional[str] = None):
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
    appeals = result.data or []

    # Attach the disputed flag's evidence (primary frame + pre-violation context
    # strip) to each flag-linked appeal, so the teacher adjudicates inline without
    # leaving the list. Same matchers + auth-gated screenshot URL shape the
    # forensics timeline uses (admin.py get_timeline); the URLs stream through
    # /admin/screenshot, which re-checks teacher scope — no media is inlined here.
    flagged = [a for a in appeals if a.get("violation_id") and a.get("roll_number")]
    if flagged:
        try:
            vids = list({str(a["violation_id"]) for a in flagged})
            vres = await _atable("violations")\
                .select("id,violation_type,created_at")\
                .in_("id", vids[:200])\
                .execute()
            vmap = {str(v["id"]): v for v in (vres.data or [])}
        except Exception:
            _log.debug("[admin/appeals] evidence flag lookup failed", exc_info=True)
            vmap = {}
        _ss_cache: dict[Any, Any] = {}
        _MAX_DIR_SCANS = 100  # bound filesystem scans for very large appeal lists
        scans = 0
        for a in flagged:
            v = vmap.get(str(a["violation_id"]))
            if not v:
                continue
            roll = a.get("roll_number") or ""
            owner_tid = str(a.get("teacher_id") or "")
            key = (roll, owner_tid)
            paths = _ss_cache.get(key)
            if paths is None:
                if scans >= _MAX_DIR_SCANS:
                    continue  # timeline "Open" button still serves these appeals
                try:
                    paths = _collect_session_screenshots(roll, owner_tid)
                except Exception:
                    paths = {}
                _ss_cache[key] = paths
                scans += 1
            if not paths:
                continue
            sid_q = quote(str(a.get("session_key", "")), safe="")
            primary = _match_primary_screenshot(v, paths)
            if primary is not None:
                a["evidence_primary"] = f"/api/v1/admin/screenshot/{roll}/{primary.name}?session_id={sid_q}"
            ctx = _match_context_screenshots(v, paths)
            if ctx:
                a["evidence_context"] = [
                    f"/api/v1/admin/screenshot/{roll}/{m.name}?session_id={sid_q}" for m in ctx
                ]

    return {"appeals": appeals}


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
    }).eq("id", appeal_id).eq("teacher_id", tid).execute()

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
