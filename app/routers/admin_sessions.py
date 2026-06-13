"""Session management router — sessions list, results, clear, force-submit, recalibration, triage."""
from ..log_safe import safe
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException, Body

from ..auth import require_admin
from ..repositories.sessions import (
    assert_session_owned as _assert_session_owned,
    fetch_all_results as _fetch_all_results,
)
from ..services.sessions import (
    build_sessions_payload as _build_sessions_payload,
    partition_live_sessions as _partition_live_sessions,
    clear_token_issue as _clear_token_issue,
    clear_token_consume as _clear_token_consume,
)
from ..constants import _CLEAR_TOKEN_TTL, _CLEAR_ACTIVE_WINDOW
from ..database import async_table as _atable
from ..limiter import limiter
from .. import cache as _cache
from ..utils import now_ist
from ..repositories.questions import load_exam_config as _load_exam_config
from ..services.risk import compute_risk_score
from ..services.scoring import recalculate_score as _recalculate_score
from ..models import SessionStatus, RESULT_STATUSES
from ..models import (
    ClearSessionsIn,
    SESSION_END_REASON_CODES, TEACHER_WARN_CHIP_CODES,
    TeacherWarnIn, SessionTerminateIn,
)

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)


async def _bus_async_publish(channel: str, payload: dict) -> None:
    """Publish to SSE/event bus when Redis support is installed."""
    try:
        from ..event_bus import async_publish
    except Exception as exc:
        if not getattr(_bus_async_publish, "_warned", False):
            logger.warning("event_bus unavailable; SSE publishes disabled: %s", exc.__class__.__name__)
            setattr(_bus_async_publish, "_warned", True)
        return
    await async_publish(channel, payload)


router = APIRouter(prefix="")


@router.get("/api/v1/admin/sessions")
@limiter.limit("60/minute")
async def get_all_sessions(request: Request, exam_id: str = None, page: int = 1, page_size: int = 50):
    teacher = await require_admin(request)
    from ..auth.scope import resolve_scope, scope_to_teacher_ids
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    try:
        # tids=None for superadmin unrestricted; list of ids otherwise.
        payload = await _build_sessions_payload(str(teacher["id"]), exam_id=exam_id, tids=tids)
        # The live monitor needs the COMPLETE active list, never a page of it.
        # `all_sessions` is already returned in full and the dashboard renders the
        # table from it; slicing `sessions` (the active list) only capped the
        # "Live Now" count at page_size and made it flicker against the SSE path,
        # which always sends the full list. Return both in full. page/page_size
        # remain accepted for back-compat but no longer truncate the live view.
        active = payload.get("sessions", [])
        return {
            **payload,
            "page": 1,
            "page_size": len(active),
            "total": len(active),
        }
    except Exception as e:
        _admin_log.error("[Sessions] ERROR: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/results")
@limiter.limit("60/minute")
async def get_all_results(request: Request, exam_id: str = None, page: int = 1, page_size: int = 50):
    teacher = await require_admin(request)
    from ..auth.scope import resolve_scope, scope_to_teacher_ids
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    all_results = await _fetch_all_results(
        teacher_id=str(teacher["id"]), exam_id=exam_id, teacher_ids=tids,
    )
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "results": all_results[start:end],
        "page": page,
        "page_size": page_size,
        "total": len(all_results),
    }


async def _fetch_completed_sessions(tid: str, exam_id_scope: str | None,
                                    fields: str) -> list[dict]:
    q = _atable("exam_sessions").select(fields)\
        .eq("teacher_id", tid).in_("status", list(RESULT_STATUSES))
    if exam_id_scope:
        q = q.eq("exam_id", exam_id_scope)
    return (await q.execute()).data or []


@router.post("/api/v1/admin/clear-live-sessions")
@limiter.limit("5/minute")
async def clear_live_sessions(request: Request, body: ClearSessionsIn = Body(...)):
    # P1.6: intentionally SELF-SCOPED — every caller can only clear
    # their own teacher_id's sessions. Org-wide clear-by-admin would
    # need a real-time impersonation model and a much larger blast
    # radius for a destructive action; we don't want a single admin
    # click nuking other teachers' live sessions. UI surface is
    # already restricted to the teacher's own Tools tab (legacy
    # dashboard data-roles="teacher", React TABS roles=['teacher'])
    # so admins/superadmins can't trigger this from the dashboard
    # anyway — but enforcing self-scope at the endpoint is the
    # belt-and-suspenders.
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    step = body.step.lower().strip()
    raw_eid = body.exam_id
    exam_id_scope: str | None = raw_eid.strip() or None if raw_eid else None

    if step == "request":
        return await _clear_request_preview(
            tid, body.include_active, body.include_completed, exam_id_scope)
    if step == "confirm":
        # P1.2 consolidated reauth gate. The Pydantic body model already
        # makes reauth_token a field, so we hand it through the shared
        # helper instead of duplicating the verify_reauth_token check
        # that lived inline here. Same fail-closed semantics.
        from ..auth.admin_auth import require_reauth_or_403
        require_reauth_or_403(
            {"reauth_token": body.reauth_token} if body.reauth_token else None,
            tid,
            request=request,
        )
        return await _clear_confirm_execute(tid, body, exam_id_scope)

    raise HTTPException(status_code=400,
                        detail="'step' must be 'request' or 'confirm'")


async def _clear_request_preview(tid: str, include_active: bool,
                                 include_completed: bool,
                                 exam_id_scope: str | None) -> dict:
    active, stale = await _partition_live_sessions(
        tid, exam_id=exam_id_scope, include_active=include_active)
    completed_rows = (await _fetch_completed_sessions(
        tid, exam_id_scope,
        "session_key,roll_number,full_name,started_at,submitted_at,exam_id")
        if include_completed else [])
    token = _clear_token_issue(tid)
    return {
        "step": "request", "token": token,
        "expires_in": _CLEAR_TOKEN_TTL,
        "active_window_s": _CLEAR_ACTIVE_WINDOW,
        "include_completed": include_completed,
        "include_active": include_active,
        "exam_id": exam_id_scope or "",
        "count": len(stale) + len(completed_rows),
        "stale_count": len(stale),
        "active_count": len(active),
        "completed_count": len(completed_rows),
        "preview": [{"session_key": r["session_key"],
                     "roll_number": r.get("roll_number"),
                     "full_name": r.get("full_name"),
                     "started_at": r.get("started_at"),
                     "last_heartbeat": r.get("last_heartbeat")}
                    for r in stale[:20]],
        "active_preview": [{"session_key": r["session_key"],
                            "roll_number": r.get("roll_number"),
                            "full_name": r.get("full_name"),
                            "last_heartbeat": r.get("last_heartbeat")}
                           for r in active[:20]],
        "completed_preview": [{"session_key": r["session_key"],
                               "roll_number": r.get("roll_number"),
                               "full_name": r.get("full_name"),
                               "submitted_at": r.get("submitted_at")}
                              for r in completed_rows[:20]],
    }


async def _clear_confirm_execute(tid: str, body: ClearSessionsIn,
                                 exam_id_scope: str | None) -> dict:
    ack = body.ack
    if ack != "DELETE":
        raise HTTPException(status_code=400,
                            detail="Missing or incorrect ack — expected 'DELETE'")
    if not _clear_token_consume(body.token, tid):
        raise HTTPException(status_code=400,
                            detail="Invalid, expired, or stale clear token — re-request preview")

    active, stale = await _partition_live_sessions(
        tid, exam_id=exam_id_scope, include_active=body.include_active)
    comp_data = (await _fetch_completed_sessions(
        tid, exam_id_scope, "session_key,roll_number,exam_id")
        if body.include_completed else [])
    completed_keys = [r["session_key"] for r in comp_data]

    if not stale and not completed_keys:
        skipped = [{"session_key": r["session_key"],
                    "roll_number": r.get("roll_number"),
                    "full_name": r.get("full_name")}
                   for r in active]
        return {"step": "confirm", "cleared": 0, "sessions": 0,
                "answers": 0, "violations": 0, "screenshots": 0,
                "skipped_active": len(active), "skipped": skipped,
                "note": ("No sessions to clear"
                         + (" — active students were protected" if active else ""))}

    session_keys = [r["session_key"] for r in stale] + completed_keys
    _sk_tid = {r["session_key"]: r.get("teacher_id") or "" for r in stale}

    skipped_active = [{"session_key": r["session_key"],
                       "roll_number": r.get("roll_number"),
                       "full_name": r.get("full_name")}
                      for r in active]
    if active:
        _admin_log.info("[ClearLive] teacher=%s protecting %d active session(s) from wipe",
                        tid, len(active))

    ans_deleted = viol_deleted = sess_deleted = ans_failures = viol_failures = sess_failures = 0
    for sk in session_keys:
        sk_tid = _sk_tid.get(sk, tid)
        try:
            r = await _atable("answers").delete().eq("session_key", sk)\
                .eq("teacher_id", sk_tid).execute()
            ans_deleted += len(r.data or [])
        except Exception as e:
            ans_failures += 1
            _admin_log.warning("[ClearLive] answer delete failed %s: %s", sk, e)
        try:
            r = await _atable("violations").delete().eq("session_key", sk)\
                .eq("teacher_id", sk_tid).execute()
            viol_deleted += len(r.data or [])
        except Exception as e:
            viol_failures += 1
            _admin_log.warning("[ClearLive] violation delete failed %s: %s", sk, e)

    for sk in session_keys:
        try:
            r = await _atable("exam_sessions").delete().eq("session_key", sk)\
                .eq("teacher_id", _sk_tid.get(sk, tid)).execute()
            sess_deleted += len(r.data or [])
        except Exception as e:
            sess_failures += 1
            _admin_log.warning("[ClearLive] session delete failed %s: %s", sk, e)

    resp: dict = {"step": "confirm", "cleared": len(session_keys),
                  "sessions": len(session_keys), "answers": ans_deleted,
                  "violations": viol_deleted, "screenshots": 0,
                  "skipped_active": len(active), "skipped": skipped_active}
    total_fails = ans_failures + viol_failures + sess_failures
    if total_fails:
        resp["partial_failures"] = total_fails
        resp["failure_details"] = {"answers": ans_failures,
                                   "violations": viol_failures,
                                   "sessions": sess_failures}
    return resp


@router.post("/api/v1/admin-submit/{session_id}")
@limiter.limit("10/minute")
async def admin_submit(session_id: str, request: Request, body: dict = Body(default_factory=dict)):
    """Force-submit a student's exam.

    Reauth gate (P1.2) consolidated to the shared helper from
    app/auth/admin_auth.py. Previously a 3-line inline check; the
    helper centralises the body-vs-X-Reauth-Token-header handling so
    new destructive endpoints can opt in with a single line.

    Phase 74 — also accepts optional reason_code + reason_text. The
    code is validated against SESSION_END_REASON_CODES (empty allowed
    for back-compat with single-click force-submit callers; "other"
    requires non-empty text). Both fields are persisted on the
    exam_sessions row (terminated_by + termination_reason_code +
    termination_reason_text) so the scorecard PDF, CSV export and
    forensic timeline can show WHY the session was closed.
    """
    from ..auth.admin_auth import require_reauth_or_403
    teacher = await require_admin(request)
    tid = teacher["id"]
    require_reauth_or_403(body, str(tid), request=request)

    # Reason validation — same shape as ID-verify reject. Both fields
    # optional so existing single-click force-submit callers keep
    # working; new dashboard flow always passes a chip.
    reason_code = (body.get("reason_code") or "").strip()
    reason_text = (body.get("reason_text") or "").strip()[:500]
    if reason_code and reason_code not in SESSION_END_REASON_CODES:
        raise HTTPException(status_code=400, detail="Invalid reason_code")
    if reason_code == "other" and not reason_text:
        raise HTTPException(status_code=400,
                            detail="reason_text required when reason_code is 'other'")

    existing_session = await _assert_session_owned(session_id, tid)
    if existing_session.get("status") in (SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED):
        return {"status": "already_submitted"}

    ev_result = await _atable("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at").execute()
    events = ev_result.data or []

    roll_number = existing_session.get("roll_number") or session_id.rsplit("_", 1)[0]
    full_name   = existing_session.get("full_name") or "Unknown"
    email       = existing_session.get("email") or "unknown@exam.com"
    for e in events:
        if e["violation_type"] == "enrollment_started" and e.get("details"):
            try:
                parts = e["details"].replace("Student: ", "")
                if "(" in parts:
                    full_name   = parts.split("(")[0].strip()
                    roll_number = parts.split("(")[1].replace(")", "").strip()
            except Exception:
                logger.debug("admin_sessions: student details parse failed", exc_info=True)

    try:
        s_result = await _atable("students").select("*")\
            .eq("roll_number", roll_number)\
            .eq("teacher_id", str(tid))\
            .execute()
        if s_result.data:
            full_name = s_result.data[0].get("full_name", full_name)
            email     = s_result.data[0].get("email", email)
    except Exception:
        logger.warning("admin_sessions: student row fetch failed", exc_info=True)

    # Prefer answers table — authoritative source; violation parsing
    # is a lossy fallback used only when answers were never persisted.
    answers_map: dict = {}
    try:
        ans_rows = (await _atable("answers").select("question_id,answer")
                     .eq("session_key", session_id)
                     .eq("teacher_id", str(tid)).execute()).data or []
        for row in ans_rows:
            answers_map[row["question_id"]] = row["answer"]
    except Exception:
        logger.warning("admin_sessions: answers table lookup failed", exc_info=True)

    if not answers_map:
        for e in events:
            if e["violation_type"] == "answer_selected" and e.get("details"):
                try:
                    parts = {}
                    for segment in e["details"].split("|"):
                        k, _, v = segment.partition(":")
                        parts[k.strip()] = v.strip()
                    if "q" in parts and "a" in parts:
                        answers_map[parts["q"]] = parts["a"]
                except Exception:
                    logger.debug("admin_sessions: answer segment parse failed", exc_info=True)

    existing_eid = existing_session.get("exam_id")
    score, total = await _recalculate_score(session_id, answers_map, tid, exam_id=existing_eid)

    pct        = round((score / max(total, 1)) * 100, 1)
    now        = now_ist()
    violations = [e for e in events
                  if e["severity"] in ("high", "medium")]
    risk = await compute_risk_score(session_id, teacher_id=tid)

    # Termination metadata (phase 74). Snapshot the teacher's name so a
    # later teacher account deletion doesn't erase the audit trail.
    decided_by = teacher.get("full_name") or teacher.get("email") or str(tid)
    sess_row = {
        "session_key":     session_id,
        "teacher_id":      str(tid),
        "roll_number":     roll_number,
        "full_name":       full_name,
        "email":           email,
        "score":           score,
        "total":           total,
        "percentage":      pct,
        "time_taken_secs": 0,
        "status":          SessionStatus.FORCE_SUBMITTED,
        "submitted_at":    now.isoformat(),
        "risk_score":      risk["risk_score"],
        "terminated_by":   decided_by,
        "termination_reason_code": reason_code,
        "termination_reason_text": reason_text,
    }
    if existing_eid:
        sess_row["exam_id"] = existing_eid
    await _atable("exam_sessions").upsert(sess_row).execute()

    if answers_map:
        ans_rows = []
        for qid, ans in answers_map.items():
            row = {"session_key": session_id, "teacher_id": str(tid),
                   "question_id": qid, "answer": ans}
            if existing_eid:
                row["exam_id"] = existing_eid
            ans_rows.append(row)
        await _atable("answers").upsert(ans_rows).execute()

    # Embed the reason in the audit-trail violation row so a forensic
    # timeline replay shows WHY the session was force-submitted, not
    # just that it was. Back-compat leading sentence preserved so
    # parsers / search continue to match "Admin force-submitted".
    audit_detail = (f"Admin force-submitted | Violations:{len(violations)} "
                    f"| Risk:{risk['risk_score']}/100 | by:{decided_by}")
    if reason_code or reason_text:
        audit_detail += f" | reason:{reason_code or 'free-text'}"
        if reason_text:
            audit_detail += f" ({reason_text})"
    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        audit_detail,
    }).execute()

    # Push terminate directive over the chat WS so the student renderer
    # can drop the terminal screen without waiting for its next poll.
    # Fire-and-forget — if the student already disconnected (which is
    # the common case for "ended after a long disengagement"), the
    # send fails silently and the next reconnect attempt sees the
    # terminal status. Importing here avoids a top-level import cycle.
    try:
        from .chat import chat_hub
        await chat_hub.teacher_send(
            str(tid), session_id,
            text="Your examiner has ended this exam.",
            kind="terminate_directive",
            extra={"reason_code": reason_code, "reason_text": reason_text},
        )
    except Exception:
        logger.debug("admin_submit: terminate_directive push failed", exc_info=True)

    _admin_log.info("[ForceSubmit] %s score:%d/%d risk:%d/100 reason:%s",
                    session_id, score, total, risk['risk_score'], reason_code or "-")

    # Publish to dashboard SSE so the teacher sees the update in real-time
    try:
        await _bus_async_publish(f"sessions:{tid}", {"kind": "submitted", "session_id": session_id})
    except Exception:
        logger.debug("admin_submit: SSE publish failed", exc_info=True)

    return {
        "status":          SessionStatus.FORCE_SUBMITTED,
        "session_id":      session_id,
        "score":           score,
        "total":           total,
        "violation_count": len(violations),
        "risk_score":      risk["risk_score"],
        "risk_label":      risk["label"],
        "reason_code":     reason_code,
        "reason_text":     reason_text,
    }


# ─── Live teacher intervention (phase 74) ────────────────────────────
#
# Three new endpoints for the layered teacher response:
#   /warn     — non-destructive: pushes an amber-bordered system_warning
#               banner to the student, inserts a low-severity audit row.
#               No reauth gate (one-click, recoverable).
#   /pause    — locks the student UI + stops their timer. Recoverable
#               via /resume. No reauth gate.
#   /resume   — closes the current pause window, adds the elapsed
#               seconds to paused_secs_total, sets status back to
#               IN_PROGRESS.
#
# The destructive End action lives at /admin-submit (extended above).

@router.post("/api/v1/admin/session/{session_id:path}/warn")
@limiter.limit("30/minute")
async def session_warn(session_id: str, request: Request,
                       data: TeacherWarnIn):
    """Send a system warning to the student over the chat WS.

    Validation: at least one of chip_code or text must be present.
    chip_code (if set) is allowlisted; text is capped at 500 chars.
    Inserts a `teacher_warning` violation row for the audit trail —
    zero risk weight (it's a teacher action, not a cheat signal),
    severity "low".
    """
    teacher = await require_admin(request)
    tid = teacher["id"]
    await _assert_session_owned(session_id, tid)
    chip_code = (data.chip_code or "").strip()
    text      = (data.text or "").strip()[:500]
    if chip_code and chip_code not in TEACHER_WARN_CHIP_CODES:
        raise HTTPException(status_code=400, detail="Invalid chip_code")
    if not chip_code and not text:
        raise HTTPException(status_code=400,
                            detail="At least one of chip_code or text is required")
    decided_by = teacher.get("full_name") or teacher.get("email") or str(tid)

    audit_detail = f"Teacher warning by {decided_by}"
    if chip_code:
        audit_detail += f" | chip:{chip_code}"
    if text:
        audit_detail += f" ({text})"
    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "teacher_warning",
        "severity":       "low",
        "details":        audit_detail,
    }).execute()

    # Compose the user-visible text. Renderer also receives chip_code
    # so it can render the human label without depending on this text
    # staying parseable.
    visible_text = text or ""
    try:
        from .chat import chat_hub
        await chat_hub.teacher_send(
            str(tid), session_id,
            text=visible_text or "Your examiner has flagged something — please re-check the camera.",
            kind="system_warning",
            extra={"chip_code": chip_code},
        )
    except Exception:
        logger.debug("session_warn: WS push failed", exc_info=True)

    _admin_log.info("[Warn] %s chip:%s by:%s", session_id, chip_code or "-", decided_by)
    return {"status": "ok", "chip_code": chip_code, "text": text}


@router.post("/api/v1/admin/session/{session_id:path}/pause")
@limiter.limit("30/minute")
async def session_pause(session_id: str, request: Request,
                        body: dict = Body(default_factory=dict)):
    """Pause an in-progress exam.

    Sets status=PAUSED and paused_at=now() on exam_sessions. Pushes a
    pause_directive over the chat WS so the student renderer drops the
    full-screen overlay. Idempotent: if the session is already paused,
    returns 200 without re-stamping.

    Optional body.note (≤ 200 chars) is shown to the student inside
    the pause overlay so a single click communicates WHY they're
    paused without forcing a separate Warn click first.

    No reauth gate — pause is recoverable. The teacher can resume,
    and even a wrong pause only costs the student a few seconds (the
    timer is also stopped).
    """
    teacher = await require_admin(request)
    tid = teacher["id"]
    sess = await _assert_session_owned(session_id, tid)
    status = sess.get("status")
    if status == SessionStatus.PAUSED:
        return {"status": "already_paused"}
    if status != SessionStatus.IN_PROGRESS:
        raise HTTPException(status_code=409,
                            detail=f"Cannot pause a session in status '{status}'")

    note = (body.get("note") or "").strip()[:200]
    now = now_ist()
    await _atable("exam_sessions").update({
        "status":    SessionStatus.PAUSED,
        "paused_at": now.isoformat(),
    }).eq("session_key", session_id).eq("teacher_id", str(tid)).execute()

    decided_by = teacher.get('full_name') or teacher.get('email') or tid
    audit_detail = f"Paused by {decided_by}"
    if note:
        audit_detail += f" — note: {note}"
    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "session_paused",
        "severity":       "low",
        "details":        audit_detail,
    }).execute()

    try:
        from .chat import chat_hub
        await chat_hub.teacher_send(
            str(tid), session_id,
            text="Your examiner has paused your exam. Please wait — your clock has stopped.",
            kind="pause_directive",
            extra={"note": note},
        )
    except Exception:
        logger.debug("session_pause: WS push failed", exc_info=True)

    _admin_log.info("[Pause] %s by:%s note:%s", session_id, tid, "y" if note else "-")
    return {"status": SessionStatus.PAUSED, "paused_at": now.isoformat(), "note": note}


@router.post("/api/v1/admin/session/{session_id:path}/reset")
@limiter.limit("30/minute")
async def session_reset(session_id: str, request: Request,
                        body: dict = Body(default_factory=dict)):
    """Re-open a CLOSED session so the student can re-enter.

    The heartbeat reaper closes a session as ABANDONED after a long
    disconnection (and auto-scores it), which then blocks re-entry as "already
    submitted". For a genuine disconnect — or any terminal session the teacher
    chooses to let the student redo — this flips it back to IN_PROGRESS, clears
    the submission/score the reaper stamped, and refreshes last_heartbeat so the
    reaper doesn't immediately re-abandon it before the student reconnects. The
    student's saved answers are NOT touched, so they resume where they left off.

    Teacher-scoped (you can only reset your own session) and audited. Refuses an
    already-active session — use pause/resume/end for those.
    """
    teacher = await require_admin(request)
    tid = teacher["id"]
    sess = await _assert_session_owned(session_id, tid)
    status = (sess.get("status") or "").lower()
    if status in (SessionStatus.IN_PROGRESS, SessionStatus.PAUSED):
        raise HTTPException(
            status_code=409,
            detail=f"Session is still active ('{status}'). Use pause/resume or end instead of reset.",
        )

    now = now_ist()
    await _atable("exam_sessions").update({
        "status":         SessionStatus.IN_PROGRESS,
        "submitted_at":   None,
        "score":          None,
        "total":          None,
        "percentage":     None,
        "paused_at":      None,
        "last_heartbeat": now.isoformat(),
    }).eq("session_key", session_id).eq("teacher_id", str(tid)).execute()

    # The auto-scored result we just cleared was cached — drop it.
    try:
        if _cache:
            _cache.delete(f"risk_score:{session_id}")
    except Exception:
        logger.debug("session_reset: risk cache invalidate failed", exc_info=True)

    decided_by = teacher.get("full_name") or teacher.get("email") or tid
    note = (body.get("note") or "").strip()[:200]
    audit_detail = f"Session reset (re-opened from '{status}') by {decided_by}"
    if note:
        audit_detail += f" — note: {note}"
    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "session_reset",
        "severity":       "low",
        "details":        audit_detail,
    }).execute()

    # Nudge the live view so the row flips back to active without a reload.
    try:
        await _bus_async_publish(f"sessions:{tid}", {"type": "session_reset", "session_id": session_id})
    except Exception:
        logger.debug("session_reset: bus publish failed", exc_info=True)

    _admin_log.info("[Reset] %s from:%s by:%s", session_id, status, tid)
    return {"status": SessionStatus.IN_PROGRESS, "reset_from": status}


@router.post("/api/v1/admin/session/{session_id:path}/resume")
@limiter.limit("30/minute")
async def session_resume(session_id: str, request: Request,
                         body: dict = Body(default_factory=dict)):
    """Resume a paused exam.

    Computes the current pause window's elapsed seconds, adds to
    paused_secs_total, sets status back to IN_PROGRESS and nulls
    paused_at. Pushes resume_directive over chat WS. Idempotent: if
    the session was not paused, returns "not_paused".
    """
    teacher = await require_admin(request)
    tid = teacher["id"]
    sess = await _assert_session_owned(session_id, tid)
    if sess.get("status") != SessionStatus.PAUSED:
        return {"status": "not_paused", "current_status": sess.get("status")}

    paused_at_raw = sess.get("paused_at")
    paused_secs_total = int(sess.get("paused_secs_total") or 0)
    elapsed = 0
    if paused_at_raw:
        try:
            paused_at = datetime.fromisoformat(str(paused_at_raw).replace("Z", "+00:00"))
            elapsed = max(0, int((datetime.now(timezone.utc) - paused_at).total_seconds()))
        except (ValueError, TypeError):
            logger.warning("session_resume: malformed paused_at on %s", safe(session_id))

    new_total = paused_secs_total + elapsed
    await _atable("exam_sessions").update({
        "status":            SessionStatus.IN_PROGRESS,
        "paused_at":         None,
        "paused_secs_total": new_total,
    }).eq("session_key", session_id).eq("teacher_id", str(tid)).execute()

    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "session_resumed",
        "severity":       "low",
        "details":        f"Resumed by {teacher.get('full_name') or teacher.get('email') or tid} "
                          f"after {elapsed}s (total paused {new_total}s)",
    }).execute()

    try:
        from .chat import chat_hub
        await chat_hub.teacher_send(
            str(tid), session_id,
            text="Your examiner has resumed your exam. You can continue.",
            kind="resume_directive",
            extra={"paused_secs_this_window": elapsed},
        )
    except Exception:
        logger.debug("session_resume: WS push failed", exc_info=True)

    _admin_log.info("[Resume] %s elapsed:%ds total:%ds by:%s",
                    session_id, elapsed, new_total, tid)
    return {
        "status":                   SessionStatus.IN_PROGRESS,
        "paused_secs_this_window":  elapsed,
        "paused_secs_total":        new_total,
    }


@router.post("/api/v1/admin/sessions/{session_id:path}/request-recalibration")
@limiter.limit("10/minute")
async def request_recalibration(session_id: str, request: Request, body: dict = Body(default_factory=dict)):
    from ..auth.admin_auth import require_reauth_or_403
    teacher = await require_admin(request)
    tid = teacher["id"]
    require_reauth_or_403(body, str(tid), request=request)

    sess = await _assert_session_owned(session_id, tid)
    status = (sess.get("status") or "").lower()
    if status in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED):
        raise HTTPException(status_code=409,
            detail="Session is already submitted; recalibration not applicable.")

    if status != SessionStatus.ABANDONED:
        try:
            await _atable("exam_sessions")\
             .update({"status": SessionStatus.ABANDONED})\
             .eq("session_key", session_id)\
             .eq("teacher_id", str(tid)).execute()
        except Exception as e:
            _admin_log.warning("[recalibration] status update failed sid=%s: %s", session_id, e)

    msg = ("Your teacher has requested re-calibration. Please close "
           "this exam window and re-launch from the lobby — your "
           "answers so far have been saved, but calibration will run "
           "again to recheck your gaze setup.")
    try:
        from ..routers.chat import chat_hub
        await chat_hub.teacher_send(str(tid), session_id, msg)
    except Exception as e:
        _admin_log.warning("[recalibration] chat notify failed sid=%s: %s", session_id, e)

    audit_ok = True
    try:
        viol_row = {
            "session_key":    session_id,
            "violation_type": "recalibration_requested",
            "severity":       "low",
            "details":        f"Teacher requested re-calibration. Session marked abandoned.",
            "teacher_id":     str(tid),
        }
        await _atable("violations").insert(viol_row).execute()
    except Exception as e:
        _admin_log.error("[recalibration] audit log failed sid=%s: %s", session_id, e)
        audit_ok = False

    if _cache:
        try:
            _cache.delete(f"cal_quality:{session_id}")
        except Exception:
            logger.debug("admin_sessions: cal_quality cache delete failed", exc_info=True)

    resp = {"ok": True, "session_id": session_id, "status": "recalibration_requested"}
    if not audit_ok:
        resp["warnings"] = ["Audit log failed to record"]
    return resp


@router.get("/api/v1/admin/sessions/{session_id:path}/triage")
@limiter.limit("10/minute")
async def live_risk_triage_endpoint(session_id: str, request: Request):
    teacher = await require_admin(request)
    # Org-admin roll-up: resolve via the scope spine (404s cross-tenant) and key
    # reads on the session OWNER's tid so an admin can triage a co-teacher's
    # live session. Matches the live-view / results roll-up.
    from ..auth.scope import resolve_scope, assert_session_accessible
    scope = await resolve_scope(teacher, request)
    _sess_acc = await assert_session_accessible(session_id, scope)
    tid = str(_sess_acc.get("teacher_id") or "")
    # Ownerless/orphan session → no derivable owner. Empty teacher_id is treated
    # as "no filter" by load_exam_config, so bail rather than risk leaking a
    # co-tenant's exam config (and to avoid a pointless LLM call).
    if not tid:
        raise HTTPException(status_code=404, detail="Session not found")

    cache_key = f"triage:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("summary"):
            return {**cached, "cached": True}

    try:
        # NB: do NOT add current_question here — exam_sessions has no such column
        # and nothing writes it, so including it made this whole select raise
        # UndefinedColumnError, silently breaking the triage lookup entirely.
        sess = (await _atable("exam_sessions").select(
                "session_key,roll_number,full_name,exam_id,started_at")
                .eq("session_key", session_id).eq("teacher_id", tid)
                .limit(1).execute()).data or []
    except Exception as e:
        _admin_log.warning("[triage] session lookup failed sid=%s: %s", safe(session_id), safe(e))
        sess = []
    sess_row = sess[0] if sess else {}

    exam_id = sess_row.get("exam_id")
    exam_title = exam_id or "Exam"
    try:
        cfg = await _load_exam_config(teacher_id=tid, exam_id=exam_id) if exam_id else None
        if cfg:
            exam_title = cfg.get("exam_title") or cfg.get("title") or exam_title
    except Exception as e:
        logger.debug("Failed to load exam config for live-view: %s", e)

    elapsed_minutes = None
    started_at = sess_row.get("started_at")
    if started_at:
        try:
            t0 = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            elapsed_minutes = max(0, int((datetime.now(timezone.utc) - t0).total_seconds() // 60))
        except Exception:
            logger.debug("admin_sessions: started_at parse failed", exc_info=True)

    session_meta = {
        "roll_number": sess_row.get("roll_number"),
        "full_name": sess_row.get("full_name"),
        "exam_title": exam_title,
        "elapsed_minutes": elapsed_minutes,
        "current_question": sess_row.get("current_question"),
    }

    try:
        viol_rows = (await _atable("violations").select("*")
                     .eq("session_key", session_id).eq("teacher_id", tid)
                     .order("created_at", desc=True).limit(80)
                     .execute()).data or []
    except Exception as e:
        _admin_log.warning("[triage] violation lookup failed sid=%s: %s", session_id, e)
        viol_rows = []

    from ..llm import live_risk_triage as _triage
    summary = await _triage(session_meta, viol_rows)

    payload = {
        "summary": summary,
        "generated_at": now_ist().isoformat(),
        "violation_count": len(viol_rows),
    }
    if _cache and summary:
        _cache.set(cache_key, payload, ttl=60)
    return {**payload, "cached": False}
