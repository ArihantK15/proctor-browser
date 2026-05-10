"""Admin router — all teacher-facing endpoints.

Extracted from main.py. Imports shared dependencies from `dependencies`.
Domain-specific routes have been split into sub-routers:
  admin_exams.py, admin_students.py, admin_scorecards.py,
  admin_invites.py, admin_settings.py
"""

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import FileResponse, StreamingResponse
from starlette.responses import Response

from ..dependencies import (
    supabase, get_logger, fmt_ist, require_admin, require_auth,
    now_ist, _assert_session_owned, _load_exam_config, _load_questions,
    _recalculate_score, _safe_filename,
    compute_risk_score, _build_sessions_payload, _partition_live_sessions,
    _clear_token_issue, _clear_token_consume, _CLEAR_TOKEN_TTL, _CLEAR_ACTIVE_WINDOW,
    SCREENSHOTS_DIR, QUESTION_IMG_DIR, _cache, _atable, limiter,
    _collect_session_screenshots, _is_violation, _match_screenshot_for_violation,
    _get_invite_base_url, _get_teacher_by_id,
    INVITE_DAILY_CAP, _new_invite_token, _uuid, _claim_and_bump_cap,
    _safe_path_component, _assert_within_directory, _html_escape,
    _violation_counts_by_session,
    generate_session_summary,
    SessionStatus, InviteStatus, VerificationStatus,
    SECRET_KEY,     _risk_label,
    verify_admin_token, _fetch_all_results,
)
from ..models import (
    IdDecisionIn, ClearSessionsIn, EmailScorecardsIn,
    ScheduleIn, ShuffleIn, AccessCodeIn,
    BulkRegisterIn, BulkStudentIn, CreateExamIn,
    CreateGroupIn, RenameGroupIn, GroupMembersIn, ExamGroupAssignIn,
    UploadQuestionImageIn,
    InviteRecipient, SendInvitesBody,
    SaveTemplateIn,
)
from ..services.scorecard import _build_scorecard_pdf

from .admin_settings import router as settings_router
from .admin_invites import router as invites_router
from .admin_scorecards import router as scorecards_router
from .admin_exams import router as exams_router
from .admin_students import router as students_router

_admin_log = logging.getLogger("admin")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")
router.include_router(settings_router)
router.include_router(invites_router)
router.include_router(scorecards_router)
router.include_router(exams_router)
router.include_router(students_router)


# ─── 1. PENDING ID VERIFICATIONS ─────────────────────────

@router.get("/api/v1/admin/pending-verifications")
@limiter.limit("30/minute")
async def pending_verifications(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = teacher["id"]
    import json as _json
    query = _atable("violations")\
        .select("*")\
        .eq("teacher_id", str(tid))\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)
    result = await query.execute()

    legacy_session_keys = None
    if exam_id:
        es = await _atable("exam_sessions").select("session_key")\
            .eq("teacher_id", str(tid)).eq("exam_id", exam_id).execute()
        legacy_session_keys = {r["session_key"] for r in (es.data or [])}

    pending = []
    for row in (result.data or []):
        try:
            obj = json.loads(row.get("details", "{}"))
        except Exception:
            continue
        if obj.get("status") != VerificationStatus.PENDING:
            continue
        if exam_id:
            stamped_eid = obj.get("exam_id") or ""
            if stamped_eid:
                if stamped_eid != exam_id:
                    continue
            else:
                if row.get("session_key") not in (legacy_session_keys or set()):
                    continue
        roll = obj.get("roll_number", "")
        pending.append({
            "id":           row.get("id"),
            "session_key":  row.get("session_key"),
            "roll_number":  roll,
            "full_name":    obj.get("full_name", ""),
            "selfie_url":   f"/api/v1/admin/screenshot/{roll}/{obj['selfie_file']}"
                            if obj.get("selfie_file") else None,
            "id_url":       f"/api/v1/admin/screenshot/{roll}/{obj['id_file']}"
                            if obj.get("id_file") else None,
            "created_at":   fmt_ist(row.get("created_at", "")),
        })
    return {"pending": pending}


# ─── 2. ID DECISION ─────────────────────────────────

@router.post("/api/v1/admin/id-decision")
@limiter.limit("20/minute")
async def id_decision(data: IdDecisionIn, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    if data.decision not in ("approved", "retake", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    import json as _json
    result = await _atable("violations")\
        .select("*")\
        .eq("id", data.violation_id)\
        .eq("teacher_id", str(tid))\
        .limit(1)\
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Verification not found")
    row = result.data[0]
    try:
        obj = json.loads(row.get("details", "{}"))
    except Exception:
        obj = {}
    obj["status"] = data.decision
    obj["decided_by"] = teacher.get("full_name", teacher.get("email", ""))
    obj["decided_at"] = now_ist().isoformat()
    await _atable("violations")\
        .update({"details": json.dumps(obj)})\
        .eq("id", data.violation_id)\
        .execute()

    if data.decision == "rejected":
        reject_row = {
            "session_key":    data.session_key,
            "violation_type": "id_rejected",
            "severity":       "high",
            "details":        f"Teacher rejected student identity — "
                              f"decided by {obj['decided_by']}",
        }
        if tid:
            reject_row["teacher_id"] = str(tid)
        await _atable("violations").insert(reject_row).execute()
        if _cache:
            _cache.delete(f"risk_score:{data.session_key}")
        try:
            await _atable("exam_sessions").update({
                "status":       SessionStatus.REJECTED,
                "submitted_at": now_ist().isoformat(),
            }).eq("session_key", data.session_key).execute()
        except Exception as e:
            logger.debug("Failed to update session status to rejected: %s", e)

    return {"status": "ok", "decision": data.decision}


# ─── 3. RISK SCORE ─────────────────────────────────

@router.get("/api/v1/risk-score/{session_id:path}")
@limiter.limit("60/minute")
async def get_risk_score(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    await _assert_session_owned(session_id, tid)
    result = await compute_risk_score(session_id, teacher_id=tid)
    result["session_id"] = session_id
    return result


# ─── 4. TIMELINE ─────────────────────────────────

@router.get("/api/v1/admin/timeline/{session_id:path}")
@limiter.limit("60/minute")
async def get_timeline(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    session_info = await _assert_session_owned(session_id, tid)
    viol_result = await _atable("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at")\
        .execute()
    events = viol_result.data or []

    roll = session_info.get("roll_number") or (
        session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
    )
    screenshot_paths = _collect_session_screenshots(roll, str(tid))
    screenshot_urls = {
        fname: f"/api/v1/admin/screenshot/{roll}/{fname}"
        for fname in screenshot_paths
    }

    timeline = []
    for e in events:
        entry = {
            "id":        e.get("id"),
            "type":      e["violation_type"],
            "severity":  e["severity"],
            "timestamp": fmt_ist(e.get("created_at", "")),
            "raw_ts":    e.get("created_at", ""),
            "details":   e.get("details"),
            "is_violation": _is_violation(e["violation_type"]),
        }
        match = _match_screenshot_for_violation(e, screenshot_paths)
        if match is not None:
            entry["screenshot"] = screenshot_urls[match.name]
        timeline.append(entry)

    return {
        "session_id":  session_id,
        "roll_number": session_info.get("roll_number", roll),
        "full_name":   session_info.get("full_name", ""),
        "status":      session_info.get("status", "unknown"),
        "started_at":  fmt_ist(session_info.get("started_at", "")),
        "submitted_at": fmt_ist(session_info.get("submitted_at", "")),
        "score":       session_info.get("score"),
        "total":       session_info.get("total"),
        "risk_score":  session_info.get("risk_score"),
        "total_events": len(events),
        "timeline":    timeline,
        "screenshots": list(screenshot_urls.values()),
        "summary":     generate_session_summary(events, session_info),
    }


# ─── 5. UPLOAD QUESTION IMAGE ─────────────────────────

@router.post("/api/v1/admin/upload-question-image")
@limiter.limit("30/minute")
async def upload_question_image(request: Request, body: UploadQuestionImageIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    raw = body.data_url or ""
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail="Missing 'image' (base64)")
    if raw.startswith("data:"):
        try:
            _, raw = raw.split(",", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Malformed data URL")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image payload")
    if len(blob) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 4MB)")

    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
        media = "image/png"
    elif blob[:3] == b"\xff\xd8\xff":
        ext = "jpg"
        media = "image/jpeg"
    elif blob[:6] in (b"GIF87a", b"GIF89a"):
        ext = "gif"
        media = "image/gif"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        ext = "webp"
        media = "image/webp"
    else:
        raise HTTPException(status_code=400, detail="Unsupported image format (PNG/JPEG/GIF/WebP only)")

    digest = hashlib.sha1(blob).hexdigest()[:24]
    filename = f"{digest}.{ext}"
    tdir = Path(QUESTION_IMG_DIR) / tid
    tdir.mkdir(parents=True, exist_ok=True)
    fpath = tdir / filename
    if not fpath.exists():
        try:
            with open(fpath, "wb") as f:
                f.write(blob)
        except OSError as e:
            _admin_log.error("[QImage] write failed: %s", e)
            raise HTTPException(status_code=500, detail="Failed to store image")

    url = f"/api/v1/question-image/{tid}/{filename}"
    return {"url": url, "bytes": len(blob), "media_type": media}


# ─── 6. SERVE QUESTION IMAGE ────────────────────────────

@router.get("/api/v1/question-image/{tid}/{filename}")
@limiter.limit("60/minute")
async def get_question_image(tid: str, filename: str, request: Request):
    from jose import jwt, JWTError
    auth = request.headers.get("Authorization", "")
    allowed = False
    if auth.startswith("Bearer "):
        tok = auth[7:]
        try:
            teacher = await verify_admin_token(tok)
            if str(teacher.get("id")) == str(tid):
                allowed = True
        except HTTPException:
            pass
        if not allowed:
            try:
                payload = jwt.decode(
                    tok, SECRET_KEY, algorithms=["HS256"],
                    options={"verify_aud": False, "require": ["exp"]},
                )
                if str(payload.get("tid") or "") == str(tid):
                    allowed = True
            except JWTError:
                pass
    if not allowed:
        raise HTTPException(status_code=401, detail="Authentication required")

    safe_tid = _safe_path_component(tid)
    safe_file = _safe_path_component(filename)
    fpath = Path(QUESTION_IMG_DIR) / safe_tid / safe_file
    try:
        _assert_within_directory(fpath, Path(QUESTION_IMG_DIR))
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Image not found")
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    suffix = fpath.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
    media = media_map.get(suffix, "application/octet-stream")
    return FileResponse(str(fpath), media_type=media)


# ─── 7. SERVE SCREENSHOT ──────────────────────────────

@router.get("/api/v1/admin/screenshot/{roll}/{filename}")
@limiter.limit("60/minute")
async def get_screenshot(roll: str, filename: str, request: Request):
    teacher = await require_admin(request)
    safe_roll = _safe_path_component(roll)
    safe_file = _safe_path_component(filename)
    tid = str(teacher["id"])
    fpath = Path(SCREENSHOTS_DIR) / tid / safe_roll / safe_file
    try:
        _assert_within_directory(fpath, Path(SCREENSHOTS_DIR) / tid)
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    suffix = fpath.suffix.lower()
    media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(fpath), media_type=media,
                        headers={"Cache-Control": "private, max-age=3600"})


# ─── 8. LIVE SESSIONS VIEW ──────────────────────────────

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


# ─── 9. RESULTS ─────────────────────────────────────────

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


# ─── 16. FAILED SESSIONS ────────────────────────────────

@router.post("/api/v1/admin-cleanup")
@limiter.limit("10/minute")
async def admin_cleanup(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    deleted = 0
    cutoff  = now_ist() - timedelta(hours=48)
    teacher_root = Path(SCREENSHOTS_DIR) / tid
    if not teacher_root.is_dir():
        return {"deleted": 0}
    try:
        for student_dir in teacher_root.iterdir():
            if student_dir.is_dir():
                for f in student_dir.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                        f.unlink()
                        deleted += 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": deleted}


# ─── 18. CLEAR LIVE SESSIONS ─────────────────────────────

@router.post("/api/v1/admin/clear-live-sessions")
@limiter.limit("5/minute")
async def clear_live_sessions(request: Request, body: ClearSessionsIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    step = body.step.lower().strip()

    include_completed = body.include_completed
    include_active = body.include_active
    raw_eid = body.exam_id
    exam_id_scope: str | None = raw_eid.strip() or None if raw_eid else None

    if step == "request":
        active, stale = await _partition_live_sessions(
            tid, exam_id=exam_id_scope, include_active=include_active,
        )
        completed_rows: list[dict] = []
        if include_completed:
            comp_q = _atable("exam_sessions")\
                .select("session_key,roll_number,full_name,started_at,submitted_at,exam_id")\
                .eq("teacher_id", tid)\
                .eq("status", SessionStatus.COMPLETED)
            if exam_id_scope:
                comp_q = comp_q.eq("exam_id", exam_id_scope)
            comp = await comp_q.execute()
            completed_rows = comp.data or []
        token = _clear_token_issue(tid)
        return {
            "step":          "request",
            "token":          token,
            "expires_in":     _CLEAR_TOKEN_TTL,
            "active_window_s": _CLEAR_ACTIVE_WINDOW,
            "include_completed": include_completed,
            "include_active":    include_active,
            "exam_id":           exam_id_scope or "",
            "count":          len(stale) + len(completed_rows),
            "stale_count":    len(stale),
            "active_count":   len(active),
            "completed_count": len(completed_rows),
            "preview":    [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "started_at":  r.get("started_at"),
                 "last_heartbeat": r.get("last_heartbeat")}
                for r in stale[:20]
            ],
            "active_preview": [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "last_heartbeat": r.get("last_heartbeat")}
                for r in active[:20]
            ],
            "completed_preview": [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "submitted_at": r.get("submitted_at")}
                for r in completed_rows[:20]
            ],
        }

    if step == "confirm":
        token = body.token
        ack   = body.ack
        if ack != "DELETE":
            raise HTTPException(status_code=400,
                detail="Missing or incorrect ack — expected 'DELETE'")
        if not _clear_token_consume(token, tid):
            raise HTTPException(status_code=400,
                detail="Confirmation token is invalid or expired — restart the clear flow")

        active, stale = await _partition_live_sessions(
            tid, exam_id=exam_id_scope, include_active=include_active,
        )

        completed_keys: list[str] = []
        comp = None
        if include_completed:
            comp_q = _atable("exam_sessions")\
                .select("session_key,roll_number,exam_id")\
                .eq("teacher_id", tid)\
                .eq("status", SessionStatus.COMPLETED)
            if exam_id_scope:
                comp_q = comp_q.eq("exam_id", exam_id_scope)
            comp = await comp_q.execute()
            completed_keys = [r["session_key"] for r in (comp.data or [])]

        if not stale and not completed_keys:
            skipped_active = [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name")}
                for r in active
            ]
            return {"step": "confirm", "cleared": 0, "sessions": 0,
                    "answers": 0, "violations": 0, "screenshots": 0,
                    "skipped_active": len(active), "skipped": skipped_active,
                    "note": ("No sessions to clear"
                             + (" — active students were protected"
                                if active else ""))}

        session_keys = [r["session_key"] for r in stale] + completed_keys
        rolls_seen = set()
        for r in stale:
            if r.get("roll_number"):
                rolls_seen.add(r["roll_number"])
        if include_completed:
            for r in (comp.data or []):
                if r.get("roll_number"):
                    rolls_seen.add(r["roll_number"])

        skipped_active = [
            {"session_key": r["session_key"],
             "roll_number": r.get("roll_number"),
             "full_name":   r.get("full_name")}
            for r in active
        ]
        if active:
            _admin_log.info("[ClearLive] teacher=%s protecting %d active session(s) from wipe", tid, len(active))

        ans_deleted = 0
        viol_deleted = 0
        scr_deleted = 0
        ans_failures = 0
        viol_failures = 0
        sess_failures = 0
        scr_failures = 0

        _sk_tid = {r["session_key"]: r.get("teacher_id") or ""
                   for r in stale}
        _ghost_keys = {r["session_key"] for r in stale if r.get("_ghost")}

        for sk in session_keys:
            sk_tid = _sk_tid.get(sk, tid)
            is_ghost = sk in _ghost_keys
            try:
                q = _atable("answers").delete().eq("session_key", sk)
                if sk_tid and not is_ghost:
                    q = q.eq("teacher_id", sk_tid)
                r = await q.execute()
                ans_deleted += len(r.data or [])
            except Exception as e:
                ans_failures += 1
                _admin_log.warning("[ClearLive] answer delete failed %s: %s", sk, e)
            try:
                q = _atable("violations").delete().eq("session_key", sk)
                if sk_tid and not is_ghost:
                    q = q.eq("teacher_id", sk_tid)
                r = await q.execute()
                viol_deleted += len(r.data or [])
            except Exception as e:
                viol_failures += 1
                _admin_log.warning("[ClearLive] violation delete failed %s: %s", sk, e)

        stale_key_set = {r["session_key"] for r in stale}
        sess_deleted = 0
        for sk in session_keys:
            try:
                if sk in _ghost_keys:
                    await _atable("exam_sessions").delete()\
                        .eq("session_key", sk).execute()
                else:
                    q = _atable("exam_sessions").delete()\
                        .eq("session_key", sk)
                    sk_tid = _sk_tid.get(sk, tid)
                    if sk_tid:
                        q = q.eq("teacher_id", sk_tid)
                    if sk in stale_key_set:
                        q = q.eq("status", SessionStatus.IN_PROGRESS)
                    else:
                        q = q.eq("status", SessionStatus.COMPLETED)
                    await q.execute()
                sess_deleted += 1
            except Exception as e:
                sess_failures += 1
                _admin_log.warning("[ClearLive] session delete failed %s: %s", sk, e)

        active_rolls = {r.get("roll_number") for r in active if r.get("roll_number")}
        t_screens = Path(SCREENSHOTS_DIR) / tid
        if t_screens.is_dir():
            for roll in rolls_seen:
                if not roll:
                    continue
                if roll in active_rolls:
                    continue
                safe = _safe_path_component(roll)
                rdir = t_screens / safe
                if not rdir.is_dir():
                    continue
                if not include_completed:
                    comp_chk = await _atable("exam_sessions")\
                        .select("session_key", count="exact")\
                        .eq("teacher_id", tid)\
                        .eq("roll_number", roll)\
                        .eq("status", SessionStatus.COMPLETED)\
                        .execute()
                    if (comp_chk.count or 0) > 0:
                        continue
                try:
                    for f in rdir.iterdir():
                        if f.is_file():
                            f.unlink()
                            scr_deleted += 1
                    rdir.rmdir()
                except Exception as e:
                    scr_failures += 1
                    _admin_log.warning("[ClearLive] screenshot cleanup failed %s: %s", rdir, e)

        _admin_log.info("[ClearLive] teacher=%s sessions=%d (completed=%d) answers=%d violations=%d screenshots=%d protected_active=%d", tid, sess_deleted, len(completed_keys), ans_deleted, viol_deleted, scr_deleted, len(active))
        resp = {
            "step":           "confirm",
            "cleared":        sess_deleted,
            "sessions":       sess_deleted,
            "answers":        ans_deleted,
            "violations":     viol_deleted,
            "screenshots":    scr_deleted,
            "completed_cleared": len(completed_keys),
            "skipped_active": len(active),
            "skipped":        skipped_active,
        }
        total_failures = ans_failures + viol_failures + sess_failures + scr_failures
        if total_failures > 0:
            resp["partial_failures"] = total_failures
            resp["failure_details"] = {
                "answers": ans_failures,
                "violations": viol_failures,
                "sessions": sess_failures,
                "screenshots": scr_failures,
            }
        return resp

    raise HTTPException(status_code=400,
        detail="'step' must be 'request' or 'confirm'")


# ─── 19. BACKFILL RISK SCORES ──────────────────────────────

@router.post("/api/v1/admin/backfill-risk-scores")
@limiter.limit("10/minute")
async def backfill_risk_scores(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = teacher["id"]
    query = _atable("exam_sessions").select("session_key")\
        .eq("status", SessionStatus.COMPLETED)\
        .eq("teacher_id", str(tid))
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sessions = await query.execute()
    count = 0
    for s in (sessions.data or []):
        risk = await compute_risk_score(s["session_key"], teacher_id=tid)
        await _atable("exam_sessions").update(
            {"risk_score": risk["risk_score"]}
        ).eq("session_key", s["session_key"])\
             .eq("teacher_id", str(tid))\
             .execute()
        count += 1
    return {"backfilled": count}


# ─── 43. ADMIN FORCE-SUBMIT ────────────────────────

@router.post("/api/v1/admin-submit/{session_id}")
@limiter.limit("10/minute")
async def admin_submit(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]

    existing_session = await _assert_session_owned(session_id, tid)
    if existing_session.get("status") == SessionStatus.COMPLETED:
        return {"status": "already_submitted"}

    from ..dependencies import _recalculate_score

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


# ─── 44. REQUEST RECALIBRATION ─────────────────────────

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


# ─── 45. LIVE VIEW START ─────────────────────────

@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/start")
@limiter.limit("30/minute")
async def live_view_start(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.set(f"liveview:{session_id}", {"tid": tid, "started_at": now_ist().isoformat()},
                   ttl=60)
    return {"ok": True, "session_id": session_id, "ttl_sec": 60}


# ─── 46. LIVE RISK TRIAGE ─────────────────────────

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


# ─── 47. LIVE VIEW KEEPALIVE ────────────────────────

@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/keepalive")
@limiter.limit("60/minute")
async def live_view_keepalive(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.set(f"liveview:{session_id}", {"tid": tid, "renewed_at": now_ist().isoformat()},
                   ttl=60)
    return {"ok": True}


# ─── 48. LIVE VIEW STOP ─────────────────────────

@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/stop")
@limiter.limit("30/minute")
async def live_view_stop(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.delete(f"liveview:{session_id}")
        _cache.delete(f"liveframe:{session_id}")
    return {"ok": True}


# ─── 49. LIVE FRAME ─────────────────────────

@router.get("/api/v1/admin/sessions/{session_id:path}/live-frame")
@limiter.limit("30/minute")
async def live_view_frame(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if not _cache:
        return Response(status_code=204)
    payload = _cache.get(f"liveframe:{session_id}")
    if not payload or not isinstance(payload, dict):
        return Response(status_code=204)

    jpeg = payload.get("jpeg_bytes")
    if jpeg:
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store, max-age=0"})

    b64 = payload.get("jpeg_b64")
    if not b64:
        return Response(status_code=204)
    try:
        jpeg = base64.b64decode(b64)
    except Exception:
        return Response(status_code=204)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, max-age=0"})


# ─── 50. LIVE VIEW FORCE STOP ────────────────────────

@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/force-stop")
@limiter.limit("10/minute")
async def live_view_force_stop(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.delete(f"liveview:{session_id}")
        _cache.delete(f"liveframe:{session_id}")
    return {"ok": True}
