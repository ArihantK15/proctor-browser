"""Session management router — sessions list, results, clear, force-submit, recalibration, triage."""
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Body

from ...auth import require_admin
from ...repositories.sessions import (
    assert_session_owned as _assert_session_owned,
    fetch_all_results as _fetch_all_results,
)
from ...services.sessions import (
    build_sessions_payload as _build_sessions_payload,
    partition_live_sessions as _partition_live_sessions,
    clear_token_issue as _clear_token_issue,
    clear_token_consume as _clear_token_consume,
)
from ...constants import _CLEAR_TOKEN_TTL, _CLEAR_ACTIVE_WINDOW, SCREENSHOTS_DIR
from ...database import async_table as _atable
from ...limiter import limiter
from ... import cache as _cache
from ...utils import now_ist
from ...repositories.questions import load_exam_config as _load_exam_config
from ...services.risk import compute_risk_score
from ...services.scoring import recalculate_score as _recalculate_score
from ...models import SessionStatus
from ...models import ClearSessionsIn

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.get("/api/v1/admin/sessions")
@limiter.limit("60/minute")
async def get_all_sessions(request: Request, exam_id: str = None, page: int = 1, page_size: int = 50):
    teacher = await require_admin(request)
    tid = teacher["id"]
    try:
        payload = await _build_sessions_payload(str(tid), exam_id=exam_id)
        start = (page - 1) * page_size
        end = start + page_size
        all_sessions = payload.get("sessions", [])
        return {
            **payload,
            "sessions": all_sessions[start:end],
            "page": page,
            "page_size": page_size,
            "total": len(all_sessions),
        }
    except Exception as e:
        _admin_log.error("[Sessions] ERROR: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/results")
@limiter.limit("60/minute")
async def get_all_results(request: Request, exam_id: str = None, page: int = 1, page_size: int = 50):
    teacher = await require_admin(request)
    all_results = await _fetch_all_results(teacher["id"], exam_id=exam_id)
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
        .eq("teacher_id", tid).eq("status", SessionStatus.COMPLETED)
    if exam_id_scope:
        q = q.eq("exam_id", exam_id_scope)
    return (await q.execute()).data or []


@router.post("/api/v1/admin/clear-live-sessions")
@limiter.limit("5/minute")
async def clear_live_sessions(request: Request, body: ClearSessionsIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    step = body.step.lower().strip()
    raw_eid = body.exam_id
    exam_id_scope: str | None = raw_eid.strip() or None if raw_eid else None

    if step == "request":
        return await _clear_request_preview(
            tid, body.include_active, body.include_completed, exam_id_scope)
    if step == "confirm":
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
async def admin_submit(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]

    existing_session = await _assert_session_owned(session_id, tid)
    if existing_session.get("status") == SessionStatus.COMPLETED:
        return {"status": "already_submitted"}

    ev_result = await _atable("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at").execute()
    events = ev_result.data or []
    if not events:
        raise HTTPException(status_code=404, detail="Session not found")

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
                pass

    try:
        s_result = await _atable("students").select("*")\
            .eq("roll_number", roll_number)\
            .eq("teacher_id", str(tid))\
            .execute()
        if s_result.data:
            full_name = s_result.data[0].get("full_name", full_name)
            email     = s_result.data[0].get("email", email)
    except Exception:
        pass

    answers_map: dict = {}
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
                pass

    existing_eid = existing_session.get("exam_id")
    score, total = await _recalculate_score(session_id, answers_map, tid, exam_id=existing_eid)

    pct        = round((score / max(total, 1)) * 100, 1)
    now        = now_ist()
    violations = [e for e in events
                  if e["severity"] in ("high", "medium")
                  and True]
    risk = await compute_risk_score(session_id, teacher_id=tid)

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
        "status":          SessionStatus.COMPLETED,
        "submitted_at":    now.isoformat(),
        "risk_score":      risk["risk_score"],
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

    await _atable("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Admin force-submitted | Violations:{len(violations)} | Risk:{risk['risk_score']}/100",
    }).execute()

    _admin_log.info("[ForceSubmit] %s score:%d/%d risk:%d/100", session_id, score, total, risk['risk_score'])
    return {
        "status":          SessionStatus.FORCE_SUBMITTED,
        "session_id":      session_id,
        "score":           score,
        "total":           total,
        "violation_count": len(violations),
        "risk_score":      risk["risk_score"],
        "risk_label":      risk["label"],
    }


@router.post("/api/v1/admin/sessions/{session_id:path}/request-recalibration")
@limiter.limit("10/minute")
async def request_recalibration(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]

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
        _admin_log.warning("[recalibration] audit log failed sid=%s: %s", session_id, e)

    if _cache:
        try:
            _cache.delete(f"cal_quality:{session_id}")
        except Exception:
            pass

    return {"ok": True, "session_id": session_id, "status": "recalibration_requested"}


@router.get("/api/v1/admin/sessions/{session_id:path}/triage")
@limiter.limit("10/minute")
async def live_risk_triage_endpoint(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)

    cache_key = f"triage:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("summary"):
            return {**cached, "cached": True}

    try:
        sess = (await _atable("exam_sessions").select(
                "session_key,roll_number,full_name,exam_id,started_at,current_question")
                .eq("session_key", session_id).eq("teacher_id", tid)
                .limit(1).execute()).data or []
    except Exception as e:
        _admin_log.warning("[triage] session lookup failed sid=%s: %s", session_id, e)
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
            pass

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

    from llm import live_risk_triage as _triage
    summary = _triage(session_meta, viol_rows)

    payload = {
        "summary": summary,
        "generated_at": now_ist().isoformat(),
        "violation_count": len(viol_rows),
    }
    if _cache and summary:
        _cache.set(cache_key, payload, ttl=60)
    return {**payload, "cached": False}
