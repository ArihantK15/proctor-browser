"""Admin router — all teacher-facing endpoints.

Extracted from main.py. Domain-specific routes have been split into sub-routers:
  admin_exams.py, admin_students.py, admin_scorecards.py, admin_invites.py,
  admin_settings.py, admin_org.py, admin_verification.py, admin_media.py,
  admin_sessions.py, admin_liveview.py
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException

from ..auth import require_admin
from ..auth.scope import (
    resolve_scope,
    scope_to_teacher_ids,
    assert_session_accessible,
)
from ..utils import fmt_ist, now_ist
from ..repositories.sessions import assert_session_owned as _assert_session_owned, fetch_all_results as _fetch_all_results
from ..repositories.questions import load_exam_config as _load_exam_config
from ..services.sessions import collect_session_screenshots as _collect_session_screenshots
from ..services.risk import compute_risk_score, _is_violation, generate_session_summary
from ..services.calibration import get_calibration_quality
from ..services.false_positive import explain_flag, normalize_sensitivity, SENSITIVITY_PRESETS
from ..services.sessions import match_screenshot_for_violation as _match_screenshot_for_violation
from ..services.sessions import match_room_screenshot_for_violation as _match_room_screenshot_for_violation
from ..database import supabase, async_table as _atable
from ..limiter import limiter
from ..constants import SCREENSHOTS_DIR, S3_LOCAL_CACHE_DAYS
from ..services.object_store import is_enabled as _s3_enabled
from ..models import SessionStatus

from .admin_settings import router as settings_router
from .admin_invites import router as invites_router
from .admin_scorecards import router as scorecards_router
from .admin_exams import router as exams_router
from .admin_students import router as students_router
from .admin_org import router as org_router
from .admin_verification import router as verification_router
from .admin_media import router as media_router
from .admin_sessions import router as sessions_router
from .admin_liveview import router as liveview_router

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")
router.include_router(settings_router)
router.include_router(invites_router)
router.include_router(scorecards_router)
router.include_router(exams_router)
router.include_router(students_router)
router.include_router(org_router)
router.include_router(verification_router)
router.include_router(media_router)
router.include_router(sessions_router)
router.include_router(liveview_router)


# ─── RISK SCORE ─────────────────────────────────

@router.get("/api/v1/risk-score/{session_id:path}")
@limiter.limit("60/minute")
async def get_risk_score(session_id: str, request: Request):
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    sess = await assert_session_accessible(session_id, scope)
    # Risk score is computed against the session's actual teacher_id, not
    # the caller's — needed so an org admin viewing Teacher B's session
    # sees Teacher B's calibration/sensitivity profile.
    result = await compute_risk_score(session_id, teacher_id=str(sess["teacher_id"]))
    result["session_id"] = session_id
    return result


# ─── TIMELINE ─────────────────────────────────

@router.get("/api/v1/admin/timeline/{session_id:path}")
@limiter.limit("60/minute")
async def get_timeline(session_id: str, request: Request):
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    session_info = await assert_session_accessible(session_id, scope)
    # All downstream lookups use the session's own teacher_id so org
    # admins / superadmins see the originating teacher's data, not their own.
    tid = str(session_info["teacher_id"])
    viol_result = await _atable("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at")\
        .execute()
    events = viol_result.data or []
    calibration_quality = await get_calibration_quality(session_id, teacher_id=tid)
    config = {}
    exam_id = session_info.get("exam_id")
    if exam_id:
        try:
            config = await _load_exam_config(str(tid), exam_id=exam_id)
        except Exception as e:
            logger.debug("[timeline] exam config lookup failed for %s: %s", exam_id, e)
    sensitivity = normalize_sensitivity(config.get("proctoring_sensitivity"))

    roll = session_info.get("roll_number") or (
        session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
    )
    screenshot_paths = _collect_session_screenshots(roll, str(tid))
    # Carry the session_id so the screenshot endpoint can re-derive the
    # OWNING teacher_id (str(tid) above) via the scope spine. Without it,
    # get_screenshot falls back to the caller's own tid and an org admin's
    # per-teacher roll-up can't reach an org-member's screenshots.
    _sid_q = quote(session_id, safe="")
    screenshot_urls = {
        fname: f"/api/v1/admin/screenshot/{roll}/{fname}?session_id={_sid_q}"
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
            "detection_confidence": e.get("detection_confidence"),
            "false_positive_review": explain_flag(
                e,
                calibration=calibration_quality,
                sensitivity=sensitivity,
            ),
        }
        match = _match_screenshot_for_violation(e, screenshot_paths)
        if match is not None:
            entry["screenshot"] = screenshot_urls[match.name]
        # Phone-cam companion captured at the same instant — the timeline
        # shows both cameras side by side for the flag (None if no phone).
        room_match = _match_room_screenshot_for_violation(e, screenshot_paths)
        if room_match is not None:
            entry["room_screenshot"] = screenshot_urls[room_match.name]
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
        "sensitivity_profile": {
            "value": sensitivity,
            **SENSITIVITY_PRESETS[sensitivity],
        },
        "calibration_quality": calibration_quality,
        "total_events": len(events),
        "timeline":    timeline,
        "screenshots": list(screenshot_urls.values()),
        "summary":     generate_session_summary(events, session_info),
    }


# ─── CLEANUP ─────────────────────────────────

@router.post("/api/v1/admin-cleanup")
@limiter.limit("10/minute")
async def admin_cleanup(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    deleted = 0
    # Manual "free disk" purge: when S3 is the system-of-record, local is a
    # short-lived cache (default 7d).  Without S3, the local disk is the
    # one-and-only copy and retention matches the DPA (30d).
    retention = S3_LOCAL_CACHE_DAYS if _s3_enabled() else 30
    cutoff  = now_ist() - timedelta(days=retention)
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


# ─── BACKFILL RISK SCORES ──────────────────────────────

@router.post("/api/v1/admin/backfill-risk-scores")
@limiter.limit("10/minute")
async def backfill_risk_scores(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = teacher["id"]
    query = _atable("exam_sessions").select("session_key")\
        .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])\
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


@router.get("/api/v1/admin/live-monitor")
@limiter.limit("10/minute")
async def live_monitor(request: Request):
    """Return active sessions in scope: teacher → own; admin → org-wide
    (optionally narrowed via ?teacher_id=); superadmin → unrestricted."""
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    q = _atable("exam_sessions").select(
        "session_key,roll_number,full_name,email,status,risk_score,started_at,exam_id,teacher_id"
    ).eq("status", SessionStatus.IN_PROGRESS)
    if tids is not None:
        # Collapse to .eq() for the single-teacher case (test stubs only mock .eq()).
        if not tids:
            q = q.eq("teacher_id", "__none__")
        elif len(tids) == 1:
            q = q.eq("teacher_id", str(tids[0]))
        else:
            q = q.in_("teacher_id", tids)
    sessions = (await q.order("started_at", desc=True).execute()).data or []

    # Attach latest violation for each session
    sks = [s["session_key"] for s in sessions]
    if sks:
        viols = (await _atable("violations")
            .select("session_key,violation_type,severity,created_at,details,detection_confidence")
            .in_("session_key", sks)
            .order("created_at", desc=True)
            .execute()).data or []
        latest_viol = {}
        for v in viols:
            sk = v.get("session_key")
            if sk and sk not in latest_viol:
                latest_viol[sk] = v
        for s in sessions:
            v = latest_viol.get(s["session_key"])
            s["latest_violation"] = v.get("violation_type") if v else None
            s["latest_violation_at"] = v.get("created_at") if v else None

    return {"sessions": sessions, "total": len(sessions)}


@router.get("/api/v1/admin/all-teachers")
@limiter.limit("30/minute")
async def list_all_teachers(request: Request):
    """Super-admin only: list every teacher across every org for the
    "filter by teacher" dropdown. Returns id + name + email + org."""
    teacher = await require_admin(request)
    if (teacher.get("org_role") or "").lower() != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    rows = (await _atable("teachers")
            .select("id,full_name,email,org_id")
            .order("full_name")
            .execute()).data or []
    # Resolve org_id → org name in one batch lookup.
    org_ids = list({r.get("org_id") for r in rows if r.get("org_id")})
    org_map: dict[str, str] = {}
    if org_ids:
        org_rows = (await _atable("organizations")
                    .select("id,name").in_("id", org_ids).execute()).data or []
        org_map = {str(o["id"]): o.get("name", "") for o in org_rows}
    return {
        "teachers": [
            {
                "id": str(r["id"]),
                "full_name": r.get("full_name", ""),
                "email": r.get("email", ""),
                "org_id": str(r.get("org_id") or ""),
                "org_name": org_map.get(str(r.get("org_id") or ""), ""),
            }
            for r in rows
        ]
    }


@router.post("/api/v1/admin/sessions/{session_id}/terminate")
@limiter.limit("10/minute")
async def terminate_session(session_id: str, request: Request):
    """Force-terminate a stuck session. EMERGENCY RECOVERY — owner OR an org
    admin whose org contains the session may terminate it, so a frozen session
    can be rescued when the owning teacher is unavailable (centralised exam
    control). The evidence row records WHO terminated it (owner vs admin).

    Superadmin is deliberately EXCLUDED: it is a cross-org *monitor-only* role
    (it can VIEW any session, but never disrupts a live exam). The routine
    live-control actions (pause / reset / warn / force-submit / recalibration)
    likewise stay owner-only — only this recovery action is shared, and only
    within an org."""
    teacher = await require_admin(request)   # superadmin POSTs are 403'd here
    tid = str(teacher["id"])
    scope = await resolve_scope(teacher, request)
    sess = await assert_session_accessible(session_id, scope)  # 404s cross-tenant
    # The write is scoped to the session OWNER's tid (defense-in-depth against a
    # TOCTOU reassignment). Synthetic/orphan rows may carry no tid → "" makes
    # the .eq match nothing on a real row.
    sess_tid = str(sess.get("teacher_id") or "")

    # Attribution: was this the owner, or an admin acting on a co-teacher's
    # session? Recorded in the evidence row so the forensic timeline is honest.
    actor = "owner" if (sess_tid and sess_tid == tid) else scope.get("role", "admin")

    now = now_ist().isoformat()
    upd_q = _atable("exam_sessions").update({
        "status": SessionStatus.FORCE_SUBMITTED,
        "submitted_at": now,
    }).eq("session_key", session_id)
    if sess_tid:
        upd_q = upd_q.eq("teacher_id", sess_tid)
    await upd_q.execute()

    viol_row = {
        "session_key": session_id,
        "violation_type": "session_terminated",
        "severity": "high",
        "details": f"Session force-terminated by {actor} (teacher {tid})",
    }
    if sess_tid:
        viol_row["teacher_id"] = sess_tid     # file under the OWNER, not the actor
    await _atable("violations").insert(viol_row).execute()

    return {"status": "terminated", "session_id": session_id}
