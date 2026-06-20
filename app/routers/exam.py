"""Student exam flow endpoints."""

from ..log_safe import safe
import asyncio
import base64
import inspect
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import Request, HTTPException, Body

_exam_log = logging.getLogger("exam")

from ..database import supabase, async_table as _atable
from .. import cache as _cache
from ..models import EventIn, ValidateIn, ResultIn, AnswerIn, BulkAnswerIn, FrameIn, IdVerifyIn, AttestIn
from ..auth import require_auth, require_teacher_auth, create_token, _check_session_ownership, _get_teacher_by_id
from ..utils import fmt_ist, now_ist, ts_to_id
from ..services.practice import is_practice, _practice_validate_response, PRACTICE_QUESTIONS
from ..services.time_extension import get_time_extension
from ..repositories.questions import load_questions as _load_questions, load_exam_config as _load_exam_config, get_access_code as _get_access_code
from ..repositories.sessions import check_group_access as _check_group_access, assert_session_owned as _assert_session_owned
from ..services.scoring import build_shuffle_view as _build_shuffle_view, get_shuffle_flags as _get_shuffle_flags, recalculate_score as _recalculate_score, canonicalise_student_answer as _canonicalise_student_answer
from ..services.autosave import (
    AUTOSAVE_QUEUE_NAME,
    cache_autosave_snapshot,
    flush_answers_to_db,
    load_autosave_snapshot,
    merge_answer_snapshot,
)
from ..services.risk import compute_risk_score, publish_critical_alert
from ..jobs import enqueue_job, flush_autosave_job, _rq_enabled
from ..constants import ROOM_CAM_SIGNING_KEY, SCREENSHOTS_DIR, KIOSK_ATTESTATION_SECRET, KIOSK_ATTESTATION_ENFORCED
from ..services.kiosk_attest import verify_attestation as _verify_attestation
from ..services.object_store import is_enabled as _s3_enabled


async def _bus_async_publish(channel: str, payload: dict) -> None:
    """Publish to SSE/event bus when Redis support is installed.

    Keep the optional Redis dependency off the router import path so the exam
    API can boot in local/dev environments without event-bus extras.
    """
    try:
        from ..event_bus import async_publish
    except Exception as exc:
        if not getattr(_bus_async_publish, "_warned", False):
            _exam_log.warning("event_bus unavailable; live SSE publishes disabled: %s", exc.__class__.__name__)
            setattr(_bus_async_publish, "_warned", True)
        return
    await async_publish(channel, payload)
from ..logger import get_logger
from ..limiter import limiter
from ..models import SessionStatus, InviteStatus

from fastapi import APIRouter

logger = logging.getLogger(__name__)

_MAX_FRAME_BASE64_LEN = 500_000  # ~375KB decoded, enough for a JPEG frame

BLOCKING_TYPES = {"vm_detected", "remote_desktop_detected", "vpn_detected",
                  "proxy_detected", "debugger_detected"}

router = APIRouter(prefix="")

# ─── STUDENT ENDPOINTS (require JWT) ─────────────────────────────

_CACHE_TTL = 600  # 10 minutes


async def _assert_student_session_access(claims: dict, session_id: str) -> dict | None:
    """Verify a student token may access an existing session.

    Legacy clients construct session keys client-side, so the first request
    before an exam_sessions row exists still falls back to the signed token
    roll check. Once the row exists, compare DB ownership fields instead of
    trusting the predictable session-key prefix.
    """
    _check_session_ownership(claims, session_id)
    try:
        executed = _atable("exam_sessions").select(
            "session_key,roll_number,teacher_id,exam_id,student_id"
        ).eq("session_key", session_id).limit(1).execute()
        row = await executed if inspect.isawaitable(executed) else executed
    except Exception:
        # Test doubles and transient lookup failures should not turn a
        # signed-token ownership pass into a false 403. The row check is a
        # defense-in-depth layer when the DB row is available.
        return None
    if not isinstance(getattr(row, "data", None), list) or not row.data:
        return None
    sess = row.data[0]
    if not isinstance(sess, dict):
        return None
    row_roll = sess.get("roll_number")
    if row_roll and str(row_roll).upper() != str(claims.get("roll") or "").upper():
        raise HTTPException(status_code=403, detail="Access denied")
    for claim_key, row_key in (("tid", "teacher_id"), ("eid", "exam_id"), ("sid", "student_id")):
        claim_val = claims.get(claim_key)
        row_val = sess.get(row_key)
        if claim_val and row_val and str(claim_val) != str(row_val):
            raise HTTPException(status_code=403, detail="Access denied")
    return sess


_terminal_statuses = (SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
                      SessionStatus.REJECTED, SessionStatus.ABANDONED)


async def _reject_if_terminal(session_id: str) -> None:
    """Raise 409 if the session is in a terminal state."""
    try:
        row = (await _atable("exam_sessions").select("status")
               .eq("session_key", session_id).limit(1).execute()).data
        if row and row[0].get("status") in _terminal_statuses:
            raise HTTPException(status_code=409, detail="Exam already submitted")
    except HTTPException:
        raise
    except Exception:
        logger.warning("_reject_if_terminal: session check failed", exc_info=True)


async def _require_attested(session_id: str, tid: str | None) -> None:
    """Raise 403 if attestation is enforced and the session is not attested."""
    if not KIOSK_ATTESTATION_ENFORCED:
        return
    try:
        row = (await _atable("exam_sessions").select("kiosk_attested")
               .eq("session_key", session_id).limit(1).execute()).data
        if row and row[0].get("kiosk_attested") is True:
            return
    except Exception:
        logger.warning("_require_attested: attestation check failed", exc_info=True)
    raise HTTPException(
        status_code=403,
        detail="Please update to the latest Procta secure browser to take this "
               "exam. Download it at https://procta.net/download — the app also "
               "auto-updates when you reopen it.",
    )


def _cache_validate(key: str, resp: dict) -> None:
    if not _cache:
        return
    try:
        _cache.set(key, resp, ttl=_CACHE_TTL)
    except Exception:
        logger.debug("exam: validate-cache write failed", exc_info=True)


@router.post("/api/v1/validate-student")
@limiter.limit("300/minute")
async def validate_student(request: Request, body: ValidateIn):
    exam_id = body.exam_id
    roll_upper = body.roll_number.strip().upper()
    provided_code = (body.access_code or "").strip().upper()
    provided_teacher_id = (body.teacher_id or "").strip() if body.teacher_id else ""

    # Practice sandbox: short-circuit before any DB lookups
    if is_practice(body.roll_number):
        return _practice_validate_response(roll_upper)

    # Redis cache: 10-minute TTL for validated student lookups
    cache_key = f"validate:{provided_teacher_id or ''}:{roll_upper}:{exam_id or ''}:{provided_code}"
    try:
        cached = _cache.get(cache_key) if _cache else None
        if cached and isinstance(cached, dict) and cached.get("valid"):
            # server_now must be FRESH per request — the lobby countdown is
            # computed off it, so a cached (stale) value would mis-time Begin.
            cached["server_now"] = datetime.now(timezone.utc).isoformat()
            return cached
    except Exception:
        logger.debug("exam: validate-cache read failed", exc_info=True)

    # Resolve teacher + student. Exam window errors are intentionally
    # user-facing; identity/access-code failures below are deliberately
    # collapsed to avoid roll-number enumeration.
    pre_tid, pre_exam_id = await _resolve_teacher(roll_upper, exam_id, provided_code, provided_teacher_id)
    config = await _load_exam_config(pre_tid, exam_id=exam_id)
    # Entry uses the LOBBY gate: open from starts_at - early_join_minutes so
    # students can verify early. The exam questions stay locked until starts_at
    # via _check_exam_started in GET /api/v1/questions.
    _check_lobby_window(config)
    # Block entry to an archived exam. In-flight sessions are unaffected
    # because this check only runs during validate_student (new start).
    if config.get("archived_at"):
        raise HTTPException(status_code=403, detail="This exam is no longer available.")
    # Block entry to an exam that has NO questions. Otherwise the student passes
    # ID-verify + calibration and only then hits get-questions' 404, getting
    # stuck on a cryptic "Could not load questions" with no way forward. Fail
    # early with an actionable message (409, not collapsed to the generic 403).
    _q_exam_id = exam_id or pre_exam_id
    if _q_exam_id:
        try:
            _exam_questions = await _load_questions(pre_tid, exam_id=_q_exam_id)
        except Exception:
            _exam_questions = None  # don't block on a transient lookup error
        if _exam_questions is not None and len(_exam_questions) == 0:
            raise HTTPException(
                status_code=409,
                detail="This exam has no questions yet. Ask your examiner to add "
                       "questions before starting.")
    try:
        student, student_tid, matched_invite_id = await _find_or_enroll_student(
            roll_upper, pre_tid, pre_exam_id)
        # _validate_access_code returns the invite the student matched via a
        # per-student access code; capture it. matched_invite_id from enrollment
        # is always None (that path marks its own invite internally), so without
        # this the acceptance update below was dead code and access-code invites
        # were never recorded as accepted.
        matched_invite_id = await _validate_access_code(
            provided_code, student_tid, student, exam_id) or matched_invite_id
        await _check_group_restrictions(student, student_tid, exam_id)
        await _check_guardian_consent(student)
        existing_key = await _check_existing_session(student, student_tid, exam_id)
        if not existing_key:
            await _check_concurrent_exam_limit(student, student_tid)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            raise HTTPException(
                status_code=403,
                detail="Invalid student details, invite status, or access code.",
            ) from exc
        raise
    if _q_exam_id and pre_tid:
        extra = await get_time_extension(pre_tid, _q_exam_id, roll_upper)
    else:
        extra = 0
    base_duration = config.get("duration_minutes", 60)
    effective_duration = base_duration + extra

    if existing_key:
        resp = _build_validate_response(student, student_tid, _q_exam_id, existing_key, duration_minutes=effective_duration, config=config)
        _cache_validate(cache_key, resp)
        return resp

    if matched_invite_id:
        try:
            await _atable("student_invites").update({
                "status": InviteStatus.ACCEPTED,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", matched_invite_id).execute()
        except Exception as e:
            logger.debug("Failed to mark invite as accepted: %s", e)

    resp = _build_validate_response(student, student_tid, _q_exam_id, duration_minutes=effective_duration, config=config)
    _cache_validate(cache_key, resp)
    return resp


# ─── validate_student helpers ───────────────────────────────────────

def _parse_exam_dt(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _early_join_minutes(config: dict) -> int:
    """Per-exam early-join lead time, clamped to a sane 0..240 min."""
    try:
        early = int(config.get("early_join_minutes") or 0)
    except (ValueError, TypeError):
        early = 0
    return max(0, min(early, 240))


def _check_exam_window_closed(config: dict, now_utc: datetime) -> None:
    """403 once the exam's end time has passed (shared by both gates)."""
    ends = _parse_exam_dt(config.get("ends_at"))
    if ends is not None and now_utc > ends:
        raise HTTPException(status_code=403, detail=f"The exam window has closed. It ended at {fmt_ist(config['ends_at'])}.")


def _check_lobby_window(config: dict) -> None:
    """Entry gate (validate_student). Opens the lobby early so students can
    finish ID + phone-camera verification before the scheduled start: entry is
    allowed from starts_at - early_join_minutes onward. Still 403s once ends_at
    passes. No starts_at => unscheduled exam, always open. A late join
    (now >= starts_at) is always allowed — the window only gates EARLY entry."""
    now_utc = datetime.now(timezone.utc)
    starts = _parse_exam_dt(config.get("starts_at"))
    if starts is not None:
        lobby_open = starts - timedelta(minutes=_early_join_minutes(config))
        if now_utc < lobby_open:
            raise HTTPException(
                status_code=403,
                detail=f"The exam lobby opens at {fmt_ist(lobby_open.isoformat())}. Please come back then.")
    _check_exam_window_closed(config, now_utc)


def _check_exam_started(config: dict) -> None:
    """Hard START gate for the exam itself (GET /api/v1/questions). The lobby may
    be open for early verification, but the questions stay locked until starts_at.

    Start-only by design: ends_at is enforced at entry (_check_lobby_window) and
    submit, NOT here — get_questions can be refetched mid-exam on reconnect, and
    a session legitimately running up to ends_at must not be blocked."""
    now_utc = datetime.now(timezone.utc)
    starts = _parse_exam_dt(config.get("starts_at"))
    if starts is not None and now_utc < starts:
        raise HTTPException(
            status_code=403,
            detail=f"The exam begins at {fmt_ist(config['starts_at'])}. Please wait — you can finish verification meanwhile.")


async def _resolve_teacher(roll_upper: str, exam_id: str, provided_code: str, provided_teacher_id: str = "") -> tuple:
    """Resolve teacher_id and exam_id via access_code, exam, or fallback."""
    pre_tid = provided_teacher_id or None
    pre_exam_id = exam_id

    if pre_tid and pre_exam_id:
        cfg = (await _atable("exam_config")
               .select("teacher_id,exam_id")
               .eq("teacher_id", str(pre_tid))
               .eq("exam_id", pre_exam_id)
               .limit(1)
               .execute()).data or []
        if not cfg:
            logger.warning(
                "[validate_student] teacher/exam mismatch denied tid=%s exam=%s roll=%s",
                safe(pre_tid), safe(pre_exam_id), safe(roll_upper),
            )
            raise HTTPException(status_code=403, detail="Invalid exam launch context.")

    # 1) access_code → invite → teacher_id + exam_id
    if provided_code and not exam_id:
        inv_by_code = await _atable("student_invites").select("teacher_id,exam_id").eq("access_code", provided_code).limit(1).execute()
        if inv_by_code.data:
            pre_tid = str(inv_by_code.data[0]["teacher_id"])
            pre_exam_id = inv_by_code.data[0].get("exam_id")

    # 2) exam_id-scoped: roll_number + exam_id → teacher_id
    if pre_tid is None and pre_exam_id:
        inv_pre = await _atable("student_invites").select("teacher_id,exam_id").eq("roll_number", roll_upper).eq("exam_id", pre_exam_id).limit(1).execute()
        if inv_pre.data:
            pre_tid = inv_pre.data[0].get("teacher_id")
        else:
            cfg = await _load_exam_config(None, exam_id=pre_exam_id)
            if cfg and cfg.get("teacher_id"):
                pre_tid = str(cfg["teacher_id"])

    # 3) Unscoped fallback — warn if ambiguous
    if pre_tid is None:
        pre_check = await _atable("students").select("teacher_id").eq("roll_number", roll_upper).limit(2).execute()
        if pre_check.data:
            if len(pre_check.data) > 1:
                logger.warning("[validate_student] roll %s matched %d teachers — ambiguous, denying", safe(roll_upper), len(pre_check.data))
                raise HTTPException(status_code=403, detail="Roll number is ambiguous. Use the access code from your exam invite.")
            pre_tid = pre_check.data[0].get("teacher_id")

    if pre_tid is None:
        inv_pre = await _atable("student_invites").select("teacher_id,exam_id").eq("roll_number", roll_upper).limit(2).execute()
        if inv_pre.data:
            if len(inv_pre.data) > 1:
                logger.warning("[validate_student] roll %s matched %d invites across teachers — ambiguous", safe(roll_upper), len(inv_pre.data))
                raise HTTPException(status_code=403, detail="Roll number is ambiguous. Use the access code from your exam invite.")
            pre_tid = inv_pre.data[0].get("teacher_id")

    return pre_tid, pre_exam_id


async def _find_or_enroll_student(roll_upper: str, pre_tid: str, pre_exam_id: str) -> tuple:
    """Look up student scoped by teacher_id; auto-enroll from invite if needed.
    Returns (student_dict, teacher_id, matched_invite_id)."""
    result_q = _atable("students").select("id,roll_number,full_name,email,phone,teacher_id,account_id,guardian_email,guardian_consent_granted_at,date_of_birth").eq("roll_number", roll_upper)
    if pre_tid:
        result_q = result_q.eq("teacher_id", str(pre_tid))
    result = await result_q.execute()
    if result.data:
        student = result.data[0]
        return student, student.get("teacher_id"), None

    # Auto-enroll from invite
    # NOTE: student_invites has NO `phone` column (see phase10 schema) —
    # selecting it raised UndefinedColumnError → 500 on the auto-enroll
    # path (the second half of the same 2f2d5af select(*)→explicit
    # regression as students.exam_id). inv.get("phone") below safely
    # yields None, and students.phone is nullable.
    inv_q = _atable("student_invites").select("id,teacher_id,exam_id,roll_number,full_name,email,status,expires_at,access_code").eq("roll_number", roll_upper)
    if pre_tid:
        inv_q = inv_q.eq("teacher_id", str(pre_tid))
    if pre_exam_id:
        inv_q = inv_q.eq("exam_id", pre_exam_id)
    inv_result = await inv_q.execute()
    if not inv_result.data:
        raise HTTPException(status_code=404, detail="Roll number not found. Please complete registration first.")

    inv = inv_result.data[0]
    inv_status = (inv.get("status") or "").lower()
    if inv_status == InviteStatus.REVOKED:
        raise HTTPException(status_code=403, detail="This invite has been revoked. Contact your teacher.")
    exp = inv.get("expires_at")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                raise HTTPException(status_code=403, detail="This invite has expired. Contact your teacher.")
        except HTTPException:
            raise
        except Exception:
            logger.warning("validate_student: bad expires_at on invite %s — treating as expired", inv.get("id"))
            raise HTTPException(status_code=403, detail="This invite has expired. Contact your teacher.")

    inv_teacher = await _get_teacher_by_id(str(inv["teacher_id"]))
    if inv_teacher and inv_teacher.get("org_id"):
        from ..services.sessions import check_org_limits
        await check_org_limits({"org_id": inv_teacher["org_id"], "org_role": inv_teacher.get("org_role", "teacher")}, delta=1)

    student_row = {
        "roll_number": inv["roll_number"],
        "full_name": inv.get("full_name", ""),
        "email": inv.get("email", ""),
        "phone": inv.get("phone") or None,
        "teacher_id": str(inv["teacher_id"]),
    }
    # NOTE: the `students` table has NO `exam_id` column — per-exam
    # association lives in student_invites / exam_sessions (see commit
    # a99797b). Writing exam_id here raised UndefinedColumnError → 500
    # on the auto-enroll path. Enrollment is teacher-scoped only.
    try:
        enroll_result = await _atable("students").insert(student_row).execute()
        if enroll_result.data:
            student = enroll_result.data[0]
            if inv_status != InviteStatus.ACCEPTED:
                await _atable("student_invites").update({
                    "status": InviteStatus.ACCEPTED,
                    "accepted_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", inv["id"]).execute()
            return student, student.get("teacher_id"), None
        # Fallback re-query
        recheck_q = _atable("students").select("id,roll_number,full_name,email,phone,teacher_id,account_id,guardian_email,guardian_consent_granted_at,date_of_birth").eq("roll_number", roll_upper)
        if "teacher_id" in student_row:
            recheck_q = recheck_q.eq("teacher_id", str(student_row["teacher_id"]))
        recheck = await recheck_q.execute()
        if recheck.data:
            return recheck.data[0], recheck.data[0].get("teacher_id"), None
        raise HTTPException(status_code=500, detail="Failed to create student record.")
    except HTTPException:
        raise
    except Exception as e:
        # phase90/91 quota trigger: if the org has hit max_students,
        # surface a clean 403 with the right phrasing for a student
        # mid-invite-accept instead of a generic 500.
        from ..services.quota import is_quota_error
        if is_quota_error(e):
            raise HTTPException(
                status_code=403,
                detail=(
                    "This exam's organization has reached its student "
                    "limit. Contact your teacher — they need to upgrade "
                    "their plan or remove an unused enrollment before "
                    "you can register."
                ),
            )
        err = str(e).lower()
        if "duplicate" in err or "unique" in err:
            recheck_q = _atable("students").select("id,roll_number,full_name,email,phone,teacher_id,account_id").eq("roll_number", roll_upper)
            if "teacher_id" in inv:
                recheck_q = recheck_q.eq("teacher_id", str(inv["teacher_id"]))
            recheck = await recheck_q.execute()
            if recheck.data:
                return recheck.data[0], recheck.data[0].get("teacher_id"), None
            raise HTTPException(status_code=500, detail="Failed to create student record.")
        raise HTTPException(status_code=500, detail="Registration failed.")


async def _validate_access_code(provided_code: str, student_tid: str, student: dict, exam_id: str) -> str:
    """Check exam access code, returning matched_invite_id or None."""
    current_code = await _get_access_code(student_tid, exam_id=exam_id)
    if not current_code:
        return None
    shared_ok = bool(provided_code) and secrets.compare_digest(
        str(provided_code), str(current_code)
    )
    if shared_ok:
        return None
    if not provided_code or not student_tid:
        raise HTTPException(status_code=403, detail="Invalid exam access code. Ask your examiner for the correct code.")
    inv_q = _atable("student_invites").select("id,access_code,status,expires_at,exam_id").eq("teacher_id", str(student_tid)).eq("roll_number", student["roll_number"])
    if exam_id:
        inv_q.eq("exam_id", exam_id)
    for inv in ((await inv_q.execute()).data or []):
        code = (inv.get("access_code") or "").upper()
        if not code or not secrets.compare_digest(str(code), str(provided_code)):
            continue
        if (inv.get("status") or "") == InviteStatus.REVOKED:
            continue
        exp = inv.get("expires_at")
        if exp:
            try:
                if datetime.now(timezone.utc) > datetime.fromisoformat(str(exp).replace("Z", "+00:00")):
                    continue
            except Exception:
                logger.warning("validate_student: bad expires_at on code-invite %s", inv.get("id"))
        return inv["id"]
    raise HTTPException(status_code=403, detail="Invalid exam access code. Ask your examiner for the correct code.")


async def _check_group_restrictions(student: dict, student_tid: str, exam_id: str) -> None:
    """Raise 403 if student is not in a group assigned to this exam."""
    if exam_id and student_tid:
        from ..repositories.sessions import check_group_access as _check_group_access
        if not await _check_group_access(student["roll_number"], str(student_tid), exam_id):
            raise HTTPException(status_code=403, detail="You are not in a group or batch assigned to this exam.")


async def _check_guardian_consent(student: dict) -> None:
    """Raise 403 if the student is a minor and consent has not been granted.

    Minor = date_of_birth → age < 18. Legacy rows with NULL date_of_birth
    are allowed through with a log warning (teacher-managed consent).
    """
    dob_str = student.get("date_of_birth")
    if not dob_str:
        return  # legacy row — allow
    try:
        if isinstance(dob_str, str):
            dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
        else:
            dob = dob_str
        # Calendar age — NOT days//365 (overestimates and would let a
        # 17-year-old near their birthday skip the minor gate).
        _today = datetime.now(timezone.utc).date()
        age = _today.year - dob.year - ((_today.month, _today.day) < (dob.month, dob.day))
    except (ValueError, TypeError):
        _exam_log.warning("[consent] bad date_of_birth on roll=%s — allowing",
                          safe(student.get("roll_number", "")))
        return

    if age >= 18:
        return  # adult — no gate

    consent_granted = student.get("guardian_consent_granted_at")
    if not consent_granted:
        raise HTTPException(
            status_code=403,
            detail="Guardian consent is required before you can start this exam. "
                   "Please ask your parent or guardian to check their email for the consent request.",
        )


async def _check_concurrent_exam_limit(student: dict, student_tid: str) -> None:
    """Reject if the student already has an IN_PROGRESS or PAUSED session
    in ANY exam. Prevents taking two exams simultaneously in separate tabs.
    """
    q = _atable("exam_sessions").select("session_key").eq("roll_number", student["roll_number"])\
        .in_("status", [SessionStatus.IN_PROGRESS, SessionStatus.PAUSED])
    if student_tid:
        q.eq("teacher_id", str(student_tid))
    active = (await q.limit(1).execute()).data
    if active:
        raise HTTPException(status_code=409,
            detail="You already have an active exam session. Complete or submit it before starting a new exam.")


async def _check_existing_session(student: dict, student_tid: str, exam_id: str) -> str | None:
    """Return existing session_key if student has a completed or in-progress session, else None."""
    # H46: Check all terminal statuses — COMPLETED, FORCE_SUBMITTED, REJECTED, ABANDONED
    terminal = (SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
                SessionStatus.REJECTED, SessionStatus.ABANDONED)
    q = _atable("exam_sessions").select("session_key,status").eq("roll_number", student["roll_number"]).in_("status", terminal)
    if student_tid:
        q.eq("teacher_id", str(student_tid))
    if exam_id:
        q.eq("exam_id", exam_id)
    prior = (await q.execute()).data
    if prior:
        # 409 (not 403) on purpose: validate_student collapses 403/404 into a
        # generic "invalid details" message to prevent roll-number enumeration.
        # A terminal session leaks nothing the student doesn't already know, so
        # 409 passes through the collapse and the actionable reason reaches them
        # — instead of "invalid student details" (which left students, and us
        # while testing, with no idea a prior session was the real block).
        #
        # ABANDONED ≠ submitted: the heartbeat reaper closes a session after a
        # long disconnection (and auto-scores it), so the student didn't submit
        # — they dropped off. Say so, and point at the teacher reset path,
        # instead of the misleading "already submitted".
        st = (prior[0].get("status") or "").lower()
        if st == SessionStatus.ABANDONED:
            raise HTTPException(
                status_code=409,
                detail="Your previous session was closed after a long disconnection. "
                       "Ask your examiner to reset it so you can re-enter.",
            )
        raise HTTPException(status_code=409, detail="You have already submitted this exam.")

    q2 = _atable("exam_sessions").select("session_key,status").eq("roll_number", student["roll_number"]).eq("status", SessionStatus.IN_PROGRESS)
    if student_tid:
        q2.eq("teacher_id", str(student_tid))
    if exam_id:
        q2.eq("exam_id", exam_id)
    in_progress = (await q2.execute()).data
    return in_progress[0]["session_key"] if in_progress else None


def _build_validate_response(student: dict, student_tid: str, exam_id: str, existing_session: str = None, duration_minutes: int = None, config: dict = None) -> dict:
    """Build the standard validate-student response dict."""
    # Observability for the #1 cause of "the student started but never showed
    # up on the teacher's dashboard": a token minted WITHOUT a teacher_id (tid)
    # publishes to no `sessions:{tid}` channel AND writes a teacher-less session
    # row, so it never reaches live monitoring. A token without exam_id (eid)
    # leaves the session un-tagged, breaking per-exam results/history/live.
    # create_token silently drops empty claims, so surface them loudly (Sentry-visible, now
    # that Sentry is on) with the roll so they're traceable — but still mint the
    # token so the student can sit the exam.
    if not student_tid:
        _exam_log.error(
            "[validate] EMPTY teacher_id minting token for roll=%s exam=%s — this "
            "session will NOT appear in the teacher live view (no tenant scope)",
            safe(student.get("roll_number")), safe(exam_id))
    elif not exam_id:
        _exam_log.warning(
            "[validate] no exam_id minting token for roll=%s (teacher=%s) — session "
            "will be un-tagged for per-exam results/history/live",
            safe(student.get("roll_number")), safe(student_tid))
    resp = {
        "valid": True,
        "full_name": student["full_name"],
        "email": student.get("email", ""),
        "phone": student.get("phone", ""),
        "roll_number": student["roll_number"],
        # Mint with the RESOLVED exam id the caller passes in (body.exam_id or the
        # exam resolved from the invite/enrollment), NOT a raw null. A null here
        # left the token — and every session row created from it — un-tagged
        # whenever the student validated without an explicit exam but their invite
        # carried one, which is exactly what made strict per-exam live filtering
        # drop the student.
        "token": create_token(student["roll_number"], student_tid, exam_id=exam_id,
                              student_id=student.get("account_id")),
    }
    if duration_minutes is not None:
        resp["duration_minutes"] = duration_minutes
    if existing_session:
        resp["existing_session"] = existing_session
    # Schedule context so the Electron lobby can render an authoritative
    # countdown ("exam begins in MM:SS") off SERVER time — never the local
    # clock, which a student could roll back to unlock Begin early.
    if config is not None:
        resp["starts_at"] = config.get("starts_at")
        resp["ends_at"] = config.get("ends_at")
        resp["early_join_minutes"] = _early_join_minutes(config)
    resp["server_now"] = datetime.now(timezone.utc).isoformat()
    return resp


@router.get("/api/v1/questions")
@limiter.limit("30/minute")
async def get_questions(request: Request):
    # Practice mode: serve the canned mock question set
    sid = (request.query_params.get("session_id") or "").strip()
    if is_practice(sid):
        return {"questions": PRACTICE_QUESTIONS, "practice": True}

    claims = require_auth(request)
    tid = claims.get("tid")
    eid = claims.get("eid")
    session_id = (request.query_params.get("session_id") or "").strip()
    await _require_attested(session_id, tid)
    questions = await _load_questions(tid, exam_id=eid)
    if not questions:
        raise HTTPException(status_code=404, detail="Questions not found")
    config = await _load_exam_config(tid, exam_id=eid)
    # Hard start gate: the lobby may already be open for early verification,
    # but questions stay locked until the scheduled start time.
    _check_exam_started(config)

    # Deterministic per-session shuffle
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    shuffled, _ = _build_shuffle_view(
        questions, session_id, str(tid or ""),
        shuffle_q=shuffle_q, shuffle_o=shuffle_o)

    # Strip correct answers — students must never see them
    safe_questions = [{k: v for k, v in q.items() if k != "correct"} for q in shuffled]
    base = config.get("duration_minutes")
    if base is not None and eid and tid:
        extra = await get_time_extension(tid, eid, (claims.get("roll") or "").upper())
        effective_duration = base + extra
    else:
        effective_duration = base
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "duration_minutes": effective_duration,
        "questions": safe_questions,
        "phone_camera_enabled": config.get("phone_camera_enabled", False),
    }


@router.get("/api/v1/exam/audio-config")
@limiter.limit("60/minute")
async def get_audio_config(request: Request):
    """Return the audio keyword + language config for the JWT-bearer's
    exam. Called by proctor.py at startup so the AudioProcessor knows
    which built-in defaults + custom keywords to merge.

    Student-authed. NULL audio_keywords on the exam_config row →
    empty list (proctor falls back to its compiled-in defaults).
    """
    sid = (request.query_params.get("session_id") or "").strip()
    if is_practice(sid):
        return {"audio_keywords": [], "audio_keywords_language": "en"}
    claims = require_auth(request)
    tid = claims.get("tid")
    eid = claims.get("eid")
    config = await _load_exam_config(tid, exam_id=eid)
    raw = config.get("audio_keywords")
    keywords: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                keywords = [str(k) for k in parsed if isinstance(k, str)]
        except (ValueError, TypeError):
            keywords = []
    lang = (config.get("audio_keywords_language") or "en").strip()
    if lang not in ("en", "hi", "en+hi"):
        lang = "en"
    return {"audio_keywords": keywords, "audio_keywords_language": lang}


@router.get("/api/v1/exam/attest-challenge")
@limiter.limit("20/minute")
async def attest_challenge(request: Request, session_id: str = ""):
    """Issue a single-use nonce for the caller's session.

    The client must call this before POST /api/v1/exam/attest to obtain
    a fresh nonce that is included in the signed attestation payload.
    Each nonce can be used once and expires after 300 seconds.
    """
    claims = require_auth(request)
    if not session_id:
        session_id = claims.get("sid") or ""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    await _assert_student_session_access(claims, session_id)

    nonce = secrets.token_urlsafe(32)
    issued_at = datetime.now(timezone.utc).isoformat()
    await _atable("exam_sessions").eq("session_key", session_id).update({
        "attest_nonce": nonce,
        "attest_nonce_issued_at": issued_at,
    }).execute()
    return {"nonce": nonce}


@router.post("/api/v1/exam/attest")
@limiter.limit("10/minute")
async def attest_kiosk(body: AttestIn, request: Request):
    """Verify a kiosk attestation and stamp the session row.

    The Electron client signs a canonical payload with the shared
    KIOSK_ATTESTATION_SECRET.  On success the session row is marked
    as attested.  On failure a high-severity violation is recorded.
    """
    claims = require_auth(request)
    session_id = body.att.get("session_key", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="missing session_key in attestation")
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")
    expected_roll = (claims.get("roll") or "").upper()

    # Fetch session row for nonce validation
    sess = (await _atable("exam_sessions").select("attest_nonce,attest_nonce_issued_at")
            .eq("session_key", session_id).limit(1).execute()).data
    expected_nonce = sess[0].get("attest_nonce") if sess else None
    nonce_issued_at = sess[0].get("attest_nonce_issued_at") if sess else None

    ok, reason = _verify_attestation(
        body.att, body.sig,
        expected_session_key=session_id,
        expected_roll=expected_roll,
        expected_nonce=expected_nonce,
        nonce_issued_at=nonce_issued_at,
    )
    if ok:
        # Single-use: clear the nonce so replaying the same attestation fails
        await _atable("exam_sessions").eq("session_key", session_id).update({
            "kiosk_attested": True,
            "client_version": body.att.get("client_version", ""),
            "attested_at":    now_ist().isoformat(),
            "attest_nonce":   None,
        }).execute()
        return {"ok": True}
    viol_row = {
        "session_key": session_id,
        "violation_type": "kiosk_attestation_failed",
        "severity":       "high",
        "details":        reason,
    }
    if tid:
        viol_row["teacher_id"] = tid
    await _atable("violations").insert(viol_row).execute()
    raise HTTPException(status_code=403, detail=reason)


@router.get("/api/v1/check-session/{roll_number}")
@limiter.limit("30/minute")
async def check_session(roll_number: str, request: Request):
    """Check if student has an in-progress session to resume."""
    # Practice mode never has a resumable session
    if is_practice(roll_number):
        return {"exists": False}

    claims = require_auth(request)
    if claims.get("roll") != roll_number:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid")
    eid = claims.get("eid")
    sess_query = _atable("exam_sessions").select("session_key,roll_number,status,started_at,submitted_at,score,total,percentage,time_taken_secs,full_name,email,teacher_id,student_id")\
        .eq("roll_number", roll_number)\
        .eq("status", SessionStatus.IN_PROGRESS)
    if tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    if eid:
        sess_query = sess_query.eq("exam_id", eid)
    result = await sess_query.order("started_at", desc=True).limit(1).execute()
    if not result.data:
        return {"exists": False}
    session = result.data[0]
    ans_query = _atable("answers").select("session_key,question_id,answer,teacher_id,exam_id")\
        .eq("session_key", session["session_key"])
    if tid:
        ans_query = ans_query.eq("teacher_id", str(tid))

    answers = await ans_query.execute()

    # Build reverse map for shuffled options
    session_key = session["session_key"]
    config = await _load_exam_config(str(tid or ""), exam_id=eid)
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    reverse: dict[str, dict[str, str]] = {}
    if shuffle_o:
        try:
            questions = await _load_questions(str(tid or ""), exam_id=eid)
            _, label_maps = _build_shuffle_view(
                questions, session_key, str(tid or ""),
                shuffle_q=shuffle_q, shuffle_o=shuffle_o)
            for qid, qmap in label_maps.items():
                reverse[qid] = {orig: disp for disp, orig in qmap.items()}
        except Exception as e:
            _exam_log.warning("[Resume] reverse map failed: %s", e)

    resumed = {}
    for r in (answers.data or []):
        qid = str(r["question_id"])
        canonical = str(r["answer"])
        disp = reverse.get(qid, {}).get(canonical, canonical)
        resumed[qid] = disp

    return {
        "exists":      True,
        "session_key": session_key,
        "answer_count": len(resumed),
        "answers":     resumed,
        "started_at":  session.get("started_at"),
    }


# ── INTEGRITY REPORT (pre-exam security check) ──────────────────

@router.post("/api/v1/integrity-report")
@limiter.limit("10/minute")
async def integrity_report(request: Request):
    """Accept a batch of integrity flags from the Electron client."""
    body = await request.json()
    session_id = body.get("session_id", "")
    # Practice sandbox: don't enforce integrity blocks
    if is_practice(session_id):
        return {"allowed": True, "blocked_reasons": []}

    claims = require_auth(request)
    flags = body.get("flags", [])
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")

    blocked_reasons = []
    for f in flags:
        ftype = f.get("type", "")
        sev = f.get("severity", "low")
        details = f.get("details", "")
        # Log each flag as a violation
        viol_row = {
            "session_key":    session_id,
            "violation_type": ftype,
            "severity":       sev,
            "details":        f"[Integrity] {details}",
        }
        conf = f.get("confidence")
        if conf is not None:
            viol_row["detection_confidence"] = float(conf)
        if tid:
            viol_row["teacher_id"] = tid
        await _atable("violations").insert(viol_row).execute()
        # Check if this flag should block exam start
        if ftype in BLOCKING_TYPES and sev == "high":
            blocked_reasons.append(f"{ftype}: {details}")

    # Publish summary to teacher dashboard
    if tid and flags:
        summary = {
            "type": "integrity_report",
            "severity": "high" if blocked_reasons else "medium",
            "session_id": session_id,
            "details": f"{len(flags)} integrity flag(s), {len(blocked_reasons)} blocking",
            "flags": [{"type": f.get("type"), "severity": f.get("severity"),
                       "details": f.get("details")} for f in flags],
        }
        await _bus_async_publish(f"sessions:{tid}", {**summary, "kind": "violation"})

    allowed = len(blocked_reasons) == 0
    return {"allowed": allowed, "blocked_reasons": blocked_reasons,
            "flags_received": len(flags)}


# Legacy alias for desktop clients (v2.x.x and earlier) whose proctor.py
# was wired with PROCTOR_SERVER_URL=<base>/event before the API got the
# /api/v1 prefix. Without this alias every AI-detection event from the
# Python subprocess (face_lost, gaze_off_screen, multiple_voices_detected,
# keyword_uttered, etc.) silently 404s and never reaches the dashboard,
# because proctor.py wraps the POST in try/except. The renderer-side
# events still work because they call /api/v1/event directly. Both the
# desktop client and proctor.py will move to /api/v1/event in a follow-up,
# but the alias unblocks every already-installed client today.
@router.post("/api/v1/event")
@router.post("/event")
@limiter.limit("120/minute")
async def log_event(event: EventIn, request: Request):
    # Practice sandbox: log to stdout only
    if is_practice(event.session_id):
        return {"status": "logged", "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    get_logger(event.session_id).info(
        f"[{safe(event.severity).upper()}] {safe(event.event_type)} | {safe(event.details)}")

    # When exam starts, create in-progress session record
    if event.event_type == "exam_started":
        # Do not resurrect a terminal session
        existing = await _atable("exam_sessions").select("status")\
            .eq("session_key", event.session_id).limit(1).execute()
        if existing.data and existing.data[0].get("status") in _terminal_statuses:
            return {"status": "blocked"}
        sid = claims.get("sid")
        row = {
            "session_key": event.session_id,
            "roll_number": event.session_id.rsplit("_", 1)[0],
            "status":      SessionStatus.IN_PROGRESS,
            "started_at":  now_ist().isoformat(),
        }
        if sid:
            row["student_id"] = sid
        if tid:
            row["teacher_id"] = tid
        if eid:
            row["exam_id"] = eid
        await _atable("exam_sessions").upsert(row).execute()

    # Alert on submission failure
    if event.event_type == "submit_failed":
        _exam_log.error("[ALERT] SUBMIT FAILED for session %s — use /api/v1/admin-submit/%s to recover", safe(event.session_id), safe(event.session_id))

    # End the room camera when the exam ends abruptly. Submit / force-submit
    # flip the session status terminal, which the room-cam WS loop detects on
    # its own; a panic/emergency exit doesn't, so close the phone stream here.
    if event.event_type in ("panic_unlock", "emergency_exit", "exam_exit"):
        try:
            from .sse import close_room_cam_ws
            await close_room_cam_ws(event.session_id)
        except Exception:
            _exam_log.debug("room-cam close on %s failed", event.event_type, exc_info=True)

    viol_row = {
        "session_key":    event.session_id,
        "violation_type": event.event_type,
        "severity":       event.severity,
        "details":        event.details,
    }
    if event.detection_confidence is not None:
        viol_row["detection_confidence"] = event.detection_confidence
    if tid:
        viol_row["teacher_id"] = tid

    # Audit-row write. SYNCHRONOUS for two event types where
    # durability ordering matters:
    #   - exam_started: the exam_sessions row was already upserted
    #     above; the matching audit row should be in place before
    #     any subsequent /event call can reach this branch
    #   - submit_failed: low-volume, critical, ops paths read it
    #     before responding to /admin-submit
    # All other event types (the high-volume proctoring detections —
    # face_lost, multiple_faces, tab_switch, etc) get enqueued to
    # the autosave worker queue. The Redis pub/sub publish below
    # is unaffected — dashboards still see the event in real-time.
    if event.event_type in ("exam_started", "submit_failed"):
        await _atable("violations").insert(viol_row).execute()
    else:
        from ..jobs.event_jobs import record_violation_job
        from ..jobs.helpers import enqueue_job
        enqueue_job(record_violation_job, viol_row, queue_name="autosave")

    # Invalidate cached risk score for this session
    if _cache:
        _cache.delete(f"risk_score:{event.session_id}")

    # Publish to Redis for SSE subscribers
    evt_payload = {"type": event.event_type, "severity": event.severity,
                   "details": event.details, "session_id": event.session_id}
    if tid:
        await _bus_async_publish(f"events:{tid}:{event.session_id}", evt_payload)
        await _bus_async_publish(f"sessions:{tid}", {**evt_payload, "kind": "violation"})

        # Publish critical alert for immediate teacher notification
        roll = event.session_id.rsplit("_", 1)[0] if "_" in event.session_id else ""
        await publish_critical_alert(
            teacher_id=str(tid),
            session_id=event.session_id,
            violation_type=event.event_type,
            severity=event.severity,
            details=event.details,
            roll_number=roll,
        )

    return {"status": "logged"}


# Legacy /heartbeat alias for the same reason as /event above —
# proctor.py's _heartbeat_loop POSTs to f"{SERVER_BASE}/heartbeat",
# which derived to /heartbeat (no /api/v1 prefix) on every desktop
# client shipped before today's fix lands.
@router.post("/api/v1/heartbeat")
@router.post("/heartbeat")
@limiter.limit("30/minute")
async def heartbeat(event: EventIn, request: Request):
    # Practice sandbox: don't track heartbeats
    if is_practice(event.session_id):
        return {"ok": True, "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")

    # Check if session already exists and is in a terminal state
    existing = await _atable("exam_sessions").select("status,exam_id")\
        .eq("session_key", event.session_id).execute()

    if existing.data and existing.data[0].get("status") in (
        SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
        SessionStatus.REJECTED, SessionStatus.ABANDONED,
    ):
        return {"ok": True}

    if existing.data:
        # Session exists — UPDATE heartbeat + status. Self-heal: if the row was
        # created with a NULL exam_id (e.g. an ID-verify ensure-session row, or a
        # legacy untagged session) and this token carries one, backfill it now so
        # strict per-exam live filtering never loses the student. Costs nothing on
        # the common path (already-tagged rows skip the write).
        upd = {
            "last_heartbeat": now_ist().isoformat(),
            "status":         SessionStatus.IN_PROGRESS,
        }
        if eid and not existing.data[0].get("exam_id"):
            upd["exam_id"] = eid
        await _atable("exam_sessions").eq("session_key", event.session_id)\
            .update(upd).execute()
    else:
        # No session row yet — INSERT
        row = {
            "session_key":    event.session_id,
            "roll_number":    event.session_id.rsplit("_", 1)[0],
            "last_heartbeat": now_ist().isoformat(),
            "status":         SessionStatus.IN_PROGRESS,
        }
        if tid:
            row["teacher_id"] = tid
        if eid:
            row["exam_id"] = eid
        await _atable("exam_sessions").upsert(row).execute()

    # ── Heartbeat re-attestation ──────────────────────────────────────
    # The Electron client includes a fresh signed {kiosk, ts} on each
    # heartbeat so the server can detect a kiosk-exit mid-exam.
    hb_att = event.att
    hb_sig = event.sig
    if hb_att and hb_sig:
        if KIOSK_ATTESTATION_SECRET:
            hb_ok, hb_reason = _verify_attestation(hb_att, hb_sig)
            if hb_ok and hb_att.get("kiosk") is not True:
                viol_row = {
                    "session_key":    event.session_id,
                    "violation_type": "kiosk_attestation_failed",
                    "severity":       "high",
                    "details":        "kiosk exited mid-exam",
                }
                if tid:
                    viol_row["teacher_id"] = tid
                await _atable("violations").insert(viol_row).execute()

    # Publish a heartbeat ping to the dashboard SSE — but COALESCE it per
    # teacher. Each student heartbeats ~every 30s, so an N-student exam would
    # otherwise fire N "refresh" pushes per cycle, and the dashboard does a
    # FULL live-sessions refetch (a 2000-row violations query) on EVERY push.
    # A short Redis throttle (≤1 heartbeat push per teacher per few seconds)
    # collapses that storm at the source; the DB last_heartbeat write above
    # still happens per beat, so liveness stays accurate. Violation pushes
    # (in log_event) are NEVER throttled — those must reach the teacher
    # immediately. When Redis is down the throttle no-ops and pub/sub is
    # already inert, so behaviour is unchanged.
    if tid:
        _hb_throttled = False
        try:
            if _cache and _cache.get(f"hb_pub_throttle:{tid}"):
                _hb_throttled = True
            elif _cache:
                _cache.set(f"hb_pub_throttle:{tid}", 1, ttl=3)
        except Exception:
            _hb_throttled = False
        if not _hb_throttled:
            await _bus_async_publish(f"sessions:{tid}", {"kind": "heartbeat",
                         "session_id": event.session_id})

    return {"ok": True}


@router.post("/api/v1/save-answer")
@limiter.limit("30/minute")
async def save_answer(body: AnswerIn, request: Request):
    # Practice sandbox: pretend the save succeeded
    if is_practice(body.session_id):
        return {"status": "saved", "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, body.session_id)
    await _reject_if_terminal(body.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    canonical = await _canonicalise_student_answer(
        body.session_id, str(tid or ""), str(body.question_id), str(body.answer),
        eid)
    row = {
        "session_key":  body.session_id,
        "question_id":  body.question_id,
        "answer":       canonical,
    }
    if tid:
        row["teacher_id"] = tid
    if eid:
        row["exam_id"] = eid
    await _atable("answers").upsert(row).execute()
    return {"status": "saved"}


async def _save_answers_bulk_to_db(
    session_id: str,
    answers: dict,
    *,
    teacher_id: str | None = None,
    exam_id: str | None = None,
) -> int:
    return await flush_answers_to_db(
        session_id,
        answers,
        teacher_id=teacher_id,
        exam_id=exam_id,
    )


def _enqueue_autosave_flush(session_id: str, *, delete_after: bool = False) -> bool:
    try:
        enqueue_job(
            flush_autosave_job,
            session_id,
            delete_after=delete_after,
            queue_name=AUTOSAVE_QUEUE_NAME,
        )
        return True
    except Exception as e:
        _exam_log.warning("[autosave] queue failed for %s: %s", safe(session_id), safe(e))
        return False


@router.post("/api/v1/save-answers-bulk")
@limiter.limit("10/minute")
async def save_answers_bulk(body: BulkAnswerIn, request: Request):
    """Periodic bulk save of all answers — safety net for failed individual saves."""
    if is_practice(body.session_id):
        return {"status": "saved", "saved": len(body.answers or {}), "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, body.session_id)
    await _reject_if_terminal(body.session_id)
    if not body.answers:
        return {"status": "empty", "saved": 0}
    tid = claims.get("tid")
    eid = claims.get("eid")
    sid = claims.get("sid")

    stored, should_flush = cache_autosave_snapshot(
        body.session_id,
        body.answers,
        teacher_id=tid,
        exam_id=eid,
        student_id=sid,
    )
    if stored:
        if should_flush and _rq_enabled():
            _enqueue_autosave_flush(body.session_id)
        return {"status": "queued", "saved": len(body.answers), "queued": True}

    saved = await _save_answers_bulk_to_db(
        body.session_id,
        body.answers,
        teacher_id=tid,
        exam_id=eid,
    )
    return {"status": "saved", "saved": saved, "queued": False}


class AgsTransientError(RuntimeError):
    """Raised by _try_ags_grade_passback when raise_on_failure is set
    and the AGS push hit a transient failure (access-token miss, LMS
    rejected the score). Lets the RQ job wrapper surface it as a job
    failure so the retry policy actually fires."""


async def _try_ags_grade_passback(
    roll_number: str,
    score: int,
    total: int,
    percentage: float,
    *,
    teacher_id: str | None = None,
    raise_on_failure: bool = False,
):
    """Attempt to push this student's score to their LMS via AGS.

    Two call patterns:
      - Inline fire-and-forget (`raise_on_failure=False`, the default).
        Used by submit_exam when async-scoring is disabled. Failures
        are swallowed because there's no retry path; the operator can
        re-push via the admin retry endpoint if needed.
      - RQ job wrapper (`raise_on_failure=True`). Raises AgsTransient
        Error on token-fetch or post_score=False so RQ's retry policy
        (3 attempts, 10/60/300s backoff) actually fires — without this,
        post_score returning False used to vanish into a logger.warning
        and the queued retries never triggered.

    Tenancy: ``teacher_id`` is REQUIRED for correctness. Two different
    teachers can each have a student row with the same ``roll_number``
    (the composite unique key is roll+teacher_id), and each row carries
    its own ``lti_user_id``. Without the scope filter on the students
    lookup, .limit(1) used to return an arbitrary teacher's row — so
    grade A could be pushed to teacher B's LMS user. That's a
    cross-tenant data + grade corruption bug. The teacher_id arrives
    via the JWT claims at the submit_exam call site and via the
    score_submission_job's session metadata, so callers always have
    it; we accept None for back-compat but log a warning when it's
    missing and bail to avoid the unscoped fallback.

    Missing-LTI-context paths (no lti_user_id, no AGS endpoint claim,
    no registration) are always silent returns — they aren't failures,
    they're just "nothing to do".
    """
    if not teacher_id:
        _exam_log.warning("[AGS] teacher_id missing on grade passback for roll=%s — bailing to avoid cross-tenant lookup", safe(roll_number))
        return
    try:
        student = (await _atable("students")
            .select("lti_user_id")
            .eq("roll_number", roll_number)
            .eq("teacher_id", str(teacher_id))
            .limit(1)
            .execute()).data
        if not student or not student[0].get("lti_user_id"):
            return

        lti_user_id = student[0]["lti_user_id"]
        from ..lti.launch import get_lti_student_context, get_ags_context
        from ..lti.registration import find_registration, load_registrations
        from ..lti.ags import get_access_token, post_score

        ctx = get_lti_student_context(lti_user_id)
        if not ctx:
            return

        iss = ctx.get("iss", "")
        sub = ctx.get("sub", "")
        deployment_id = ctx.get("deployment_id", "")
        if not iss or not sub:
            return

        ags_ctx = get_ags_context(iss, deployment_id)
        if not ags_ctx or not ags_ctx.get("ags_lineitems"):
            _exam_log.debug("[AGS] No AGS context for %s/%s", iss, deployment_id)
            return

        client_id = ctx.get("client_id", "")
        registration = find_registration(iss, client_id)
        if not registration:
            if not client_id:
                for r in load_registrations():
                    if r.issuer == iss:
                        registration = r
                        break
                if registration:
                    _exam_log.warning("[AGS] Empty client_id, matched issuer=%s by fallback (reg=%s)", iss, registration.client_id)
            if not registration:
                _exam_log.warning("[AGS] No registration for issuer=%s client_id=%s — skipping grade passback", iss, client_id)
                return

        access_token = await get_access_token(
            issuer=iss,
            client_id=registration.client_id,
            auth_token_url=registration.auth_token_url,
            scopes=ags_ctx.get("ags_scope") or [
                "https://purl.imsglobal.org/spec/lti-ags/scope/score",
            ],
        )
        if not access_token:
            _exam_log.warning("[AGS] Failed to get access token for %s", iss)
            if raise_on_failure:
                raise AgsTransientError(f"access token unavailable for {iss}")
            return

        # AGS requires scoreMaximum > 0 (Canvas/Moodle 4xx-reject a 0 maximum).
        # A 0-total exam (e.g. only short-answer questions, none auto-graded yet)
        # has no gradebook value to push — skip rather than send an invalid
        # payload that the LMS rejects and, under raise_on_failure, RQ then
        # retries forever (total never changes).
        if float(total) <= 0:
            _exam_log.info("[AGS] Skipping passback for %s — total is 0 (no auto-graded score)",
                           safe(roll_number))
            return
        ok = await post_score(
            lineitem_url=ags_ctx["ags_lineitems"],
            access_token=access_token,
            user_id=sub,
            score_given=float(score),
            score_maximum=float(total),
            timestamp=datetime.now(timezone.utc).isoformat(),
            comment=f"Score: {score}/{total} ({percentage}%)",
        )
        if ok:
            _exam_log.info("[AGS] Grade pushed for %s: %d/%d", roll_number, score, total)
        elif raise_on_failure:
            # post_score has already logged the underlying httpx error.
            # Surfacing it here lets RQ retry on the standard backoff.
            raise AgsTransientError(f"post_score returned False for {roll_number}")
    except AgsTransientError:
        raise
    except Exception as e:
        _exam_log.warning("[AGS] Grade passback failed for %s: %s", roll_number, e)
        if raise_on_failure:
            raise AgsTransientError(str(e)) from e


async def _try_google_grade_passback(
    roll_number: str,
    score: int,
    total: int,
    exam_id: str,
    *,
    teacher_id: str | None = None,
):
    """Best-effort: push this student's score to the linked Google Classroom course.

    Mirrors _try_ags_grade_passback but for Google Classroom. Silent no-op (not
    a failure) whenever there's nothing to do: the student wasn't synced from
    Classroom (no google_user_id), the exam isn't linked to a course, or the
    teacher hasn't connected Google. Never raises into the submit path — a
    Classroom hiccup must not fail exam submission; the score is already saved.

    Tenancy: teacher_id is REQUIRED — roll_number is only unique per teacher, and
    the Google token + course link are per-teacher.
    """
    if not teacher_id or not exam_id:
        return
    try:
        student = (await _atable("students")
            .select("google_user_id,google_course_id")
            .eq("roll_number", roll_number)
            .eq("teacher_id", str(teacher_id))
            .limit(1)
            .execute()).data
        if not student or not (student[0].get("google_user_id") or "").strip():
            return
        google_user_id = student[0]["google_user_id"]
        student_course_id = (student[0].get("google_course_id") or "").strip()

        # The teacher's connected Google account.
        tok = (await _atable("google_auth_tokens")
            .select("token_json")
            .eq("teacher_id", str(teacher_id))
            .limit(1)
            .execute()).data
        if not tok:
            return

        # Which linked course's gradebook? Prefer the course the student was
        # synced from; fall back to any course linked to this exam.
        link_q = (_atable("google_classroom_links")
            .select("id,google_course_id,course_work_id")
            .eq("teacher_id", str(teacher_id))
            .eq("exam_id", str(exam_id)))
        if student_course_id:
            link_q = link_q.eq("google_course_id", student_course_id)
        link = (await link_q.limit(1).execute()).data
        if not link:
            return
        link_row = link[0]
        course_id = link_row["google_course_id"]

        from ..services.crypto import decrypt_token
        from ..services.google_classroom import (
            _build_credentials, create_coursework, push_grade,
        )
        import json as _json
        token_dict = _json.loads(decrypt_token(tok[0].get("token_json", "{}")) or "{}")
        creds = _build_credentials(token_dict)
        if not creds:
            return

        # Lazily create the assignment the first time we grade this exam.
        # KNOWN LIMITATION: if two students submit the very first grades for a
        # never-graded linked exam within the creation window, both see an empty
        # course_work_id and each creates an assignment → grades split across
        # duplicates (last writer wins the stored id). Harmless for the common
        # case (assignment exists after the first push); a future hardening
        # would create the assignment at link-exam time or claim it atomically.
        course_work_id = (link_row.get("course_work_id") or "").strip()
        if not course_work_id:
            ex = (await _atable("exam_config")
                .select("exam_title")
                .eq("exam_id", str(exam_id))
                .eq("teacher_id", str(teacher_id))
                .limit(1)
                .execute()).data
            title = (ex[0].get("exam_title") if ex else "") or "Procta Exam"
            course_work_id = await create_coursework(creds, course_id, title, float(total))
            if not course_work_id:
                return
            await (_atable("google_classroom_links")
                .update({"course_work_id": course_work_id})
                .eq("id", link_row["id"])
                .execute())

        ok = await push_grade(creds, course_id, course_work_id, google_user_id,
                              score=float(score), max_score=float(total))
        if ok:
            _exam_log.info("[GClass] Grade pushed for %s: %d/%d", safe(roll_number), score, total)
    except Exception as e:
        _exam_log.warning("[GClass] Grade passback failed for %s: %s", safe(roll_number), safe(e))


def _async_scoring_enabled() -> bool:
    """Feature flag: when ON, /submit-exam returns 202 immediately and a
    background RQ job does scoring + risk + LMS passback. When OFF (default),
    the legacy inline-scoring path runs. Allows instant rollback if the
    async path misbehaves.
    """
    return os.environ.get("ASYNC_SCORING_ENABLED", "").lower() in ("1", "true", "yes")


@router.post("/api/v1/submit-exam")
@limiter.limit("5/minute")
async def submit_exam(result: ResultIn, request: Request):
    # Practice sandbox: grade against the in-memory PRACTICE_QUESTIONS
    if is_practice(result.session_id):
        correct = sum(
            1 for q in PRACTICE_QUESTIONS
            if str(result.answers.get(str(q["question_id"]),
                                      result.answers.get(str(q["id"]), ""))).upper()
               == str(q["correct"]).upper()
        )
        total = len(PRACTICE_QUESTIONS)
        pct = round((correct / max(total, 1)) * 100, 1)
        return {
            "status": SessionStatus.SUBMITTED,
            "score": correct,
            "total": total,
            "percentage": pct,
            "passed": True,
            "practice": True,
        }

    claims = require_auth(request)
    await _assert_student_session_access(claims, result.session_id)
    await _require_attested(result.session_id, claims.get("tid"))
    tid = claims.get("tid")
    eid = claims.get("eid")
    sid = claims.get("sid")
    now = now_ist()

    autosave_snapshot = load_autosave_snapshot(result.session_id)
    final_answers = merge_answer_snapshot(result.answers, autosave_snapshot)

    # SECURITY: Use JWT roll, not client-supplied fields (IDOR prevention)
    jwt_roll = claims.get("roll", "")
    trusted_roll = jwt_roll.upper()

    # Guard: Block re-submission of already-submitted-or-completed sessions.
    # With async scoring enabled, status sits in SUBMITTED for ~100 ms while
    # the RQ worker scores it — without this extra check, a rapid double-
    # click would enqueue two scoring jobs and double-insert violations /
    # SSE publishes / LMS passbacks. Catching SUBMITTED here keeps the
    # async flow strictly idempotent at the API edge.
    existing = await _atable("exam_sessions").select("status,started_at,full_name,email,submitted_at,score,total,percentage,risk_score,paused_secs_total")\
        .eq("session_key", result.session_id).execute()
    recovered_from_abandoned = False
    if existing.data:
        current_status = existing.data[0].get("status")
        if current_status in (SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
                              SessionStatus.REJECTED):
            raise HTTPException(status_code=409, detail="Exam already submitted")
        # ABANDONED is RECOVERABLE: the heartbeat reaper (or a disconnection)
        # closed the session, but the student is submitting valid, JWT-authed
        # answers — accept + score it rather than lose the attempt. The UPDATE
        # guards below allow ABANDONED→COMPLETED, and a 'session_recovered'
        # audit row is written so the teacher sees it was reopened by a late
        # submit. COMPLETED/FORCE_SUBMITTED/REJECTED stay terminal (real
        # double-submits / teacher-terminations are still rejected above).
        recovered_from_abandoned = (current_status == SessionStatus.ABANDONED)
        if current_status == SessionStatus.SUBMITTED:
            # Treat as a successful retry — return the same shape the renderer
            # expects so the user sees the "Calculating your score…" state
            # rather than an error. If scoring already finished between this
            # check and the SUBMITTED snapshot, send the final numbers.
            rec = existing.data[0]
            if rec.get("score") is not None:
                return {"status": SessionStatus.SUBMITTED,
                        "score": rec.get("score"), "total": rec.get("total"),
                        "percentage": rec.get("percentage"),
                        "risk_score": rec.get("risk_score") or 0,
                        "risk_label": ""}
            return {"status": SessionStatus.SUBMITTED, "scoring": "pending",
                    "message": "Submission already received. Calculating your score…",
                    "session_id": result.session_id}

    # ── Async-scoring fast path (Fix #2 — handles burst submit waves) ──
    # When the feature flag is on, persist the answers + mark the session
    # as 'submitted' (intermediate state), enqueue scoring to RQ, and
    # return 202 immediately. The client polls /api/v1/session-status to
    # learn the final score. Heavy work (scoring, risk, LMS passback)
    # moves off the request thread.
    if _async_scoring_enabled():
        try:
            # Persist answers (cache → DB) — same as Phase 2 in the legacy path
            final_cached, final_should_flush = cache_autosave_snapshot(
                result.session_id,
                final_answers,
                teacher_id=tid,
                exam_id=eid,
                student_id=sid,
                final=True,
            )
            queued = (
                final_cached
                and final_should_flush
                and _rq_enabled()
                and _enqueue_autosave_flush(result.session_id, delete_after=True)
            )
            if not queued:
                await _save_answers_bulk_to_db(
                    result.session_id,
                    final_answers,
                    teacher_id=tid,
                    exam_id=eid,
                )

            # Mark session 'submitted' (NOT 'completed' yet — scoring pending)
            interim_row = {
                "status":          SessionStatus.SUBMITTED,
                "submitted_at":    now.isoformat(),
                "time_taken_secs": result.time_taken_secs,
                "roll_number":     trusted_roll,
            }
            if tid:
                interim_row["teacher_id"] = tid
            if eid:
                interim_row["exam_id"] = eid
            await _atable("exam_sessions").update(interim_row)\
                .eq("session_key", result.session_id)\
                .not_.in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
                                      SessionStatus.REJECTED])\
                .execute()

            # Enqueue the scoring job — runs in RQ worker, never blocks this request
            from ..jobs import enqueue_job, score_submission_job
            enqueue_job(
                score_submission_job,
                session_id=result.session_id,
                teacher_id=tid,
                exam_id=eid,
                student_id=sid,
                roll_number=trusted_roll,
                time_taken_secs=result.time_taken_secs,
                queue_name="scoring",
            )

            _exam_log.info("[SUBMIT-ASYNC] %s queued for scoring (roll=%s)",
                           safe(result.session_id), safe(trusted_roll))
            return {
                "status": SessionStatus.SUBMITTED,
                "scoring": "pending",
                "message": "Submission received. Calculating your score…",
                "session_id": result.session_id,
            }
        except Exception as e:
            # If the async path blows up, fall through to the inline path
            # rather than 500-ing — this is the rollback-safety we want.
            _exam_log.error("[SUBMIT-ASYNC] fast-path failed for %s, "
                            "falling back to inline scoring: %s",
                            safe(result.session_id), safe(e))

    # Phase 1: Score + config in parallel
    score_fut = _recalculate_score(result.session_id, final_answers, teacher_id=tid, exam_id=eid)
    config_fut = _load_exam_config(teacher_id=tid, exam_id=eid)
    try:
        (server_score, server_total), config = await asyncio.gather(score_fut, config_fut)
    except RuntimeError as e:
        _exam_log.warning("[SUBMIT] Score calculation failed for %s: %s", safe(result.session_id), safe(e))
        raise HTTPException(status_code=503,
                            detail="Score calculation temporarily unavailable. Please retry.")

    if server_score == 0 and server_total == 0:
        _exam_log.warning("[SUBMIT] Score recalculation returned 0/0 for %s", safe(result.session_id))

    pct = round((server_score / max(server_total, 1)) * 100, 1)

    # Phase 2: Flush answers BEFORE marking session COMPLETED (C15 fix)
    final_cached, final_should_flush = cache_autosave_snapshot(
        result.session_id,
        final_answers,
        teacher_id=tid,
        exam_id=eid,
        student_id=sid,
        final=True,
    )
    try:
        queued = (
            final_cached
            and final_should_flush
            and _rq_enabled()
            and _enqueue_autosave_flush(result.session_id, delete_after=True)
        )
        if not queued:
            await _save_answers_bulk_to_db(
                result.session_id,
                final_answers,
                teacher_id=tid,
                exam_id=eid,
            )
    except Exception as e:
        _exam_log.error("[SUBMIT] final answer flush failed for %s: %s", safe(result.session_id), safe(e))
        raise HTTPException(status_code=500,
                            detail="Failed to save final answers. Please retry.")

    # Phase 3: Session upsert (COMPLETED) + submission log + time check in parallel
    # Use existing session data (created at exam start) for name/email — never
    # trust client-supplied result.full_name / result.email.
    existing_session = existing.data[0] if existing.data else {}
    session_row = {
        "session_key":     result.session_id,
        "roll_number":     trusted_roll,
        "full_name":       existing_session.get("full_name", result.full_name[:100]),
        "email":           existing_session.get("email", result.email[:120]),
        "score":           server_score,
        "total":           server_total,
        "percentage":      pct,
        "time_taken_secs": result.time_taken_secs,
        "status":          SessionStatus.COMPLETED,
        "submitted_at":    now.isoformat(),
    }
    if tid:
        session_row["teacher_id"] = tid
    if eid:
        session_row["exam_id"] = eid
    if sid:
        session_row["student_id"] = sid

    submit_viol = {
        "session_key":    result.session_id,
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Score:{server_score}/{server_total} ({pct}%)",
        "teacher_id":     tid or "",
    }

    parallel_ops = [
        # NOTE: ABANDONED is intentionally NOT in this guard — recover-on-submit
        # allows a reaper-closed session to finalize. COMPLETED/FORCE_SUBMITTED/
        # REJECTED stay protected against TOCTOU resurrection.
        _atable("exam_sessions").update(session_row)\
            .eq("session_key", result.session_id)\
            .not_.in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
                                  SessionStatus.REJECTED])\
            .execute(),
        _atable("violations").insert(submit_viol).execute(),
    ]
    if recovered_from_abandoned:
        parallel_ops.append(
            _atable("violations").insert({
                "session_key":    result.session_id,
                "violation_type": "session_recovered",
                "severity":       "low",
                "details":        "Recovered from ABANDONED by a valid late submission.",
                "teacher_id":     tid or "",
            }).execute())
        _exam_log.info("[SUBMIT-RECOVER] %s recovered from ABANDONED (roll=%s)",
                       safe(result.session_id), safe(trusted_roll))

    # Time exceeded check — use server-computed elapsed time (H44).
    # Phase 74: subtract paused_secs_total so teacher-pause windows
    # don't count against the student's clock.
    # Phase 113: add per-student time extension (accommodations).
    extra = await get_time_extension(tid, eid, trusted_roll) if tid and eid else 0
    allowed_secs = (config.get("duration_minutes", 60) + extra) * 60
    started_at_str = existing_session.get("started_at")
    if started_at_str:
        try:
            started = datetime.fromisoformat(str(started_at_str).replace("Z", "+00:00"))
            server_elapsed = (now - started).total_seconds()
        except (ValueError, TypeError):
            server_elapsed = result.time_taken_secs
    else:
        server_elapsed = result.time_taken_secs
    paused_secs_total = int(existing_session.get("paused_secs_total") or 0)
    if paused_secs_total:
        server_elapsed = max(0, server_elapsed - paused_secs_total)
    if server_elapsed > allowed_secs + 120:
        time_viol = {
            "session_key":    result.session_id,
            "violation_type": "time_exceeded",
            "severity":       "high",
            "details":        f"Submitted {int(server_elapsed - allowed_secs)}s past time limit",
            "teacher_id":     tid or "",
        }
        parallel_ops.append(_atable("violations").insert(time_viol).execute())

    # Execute parallel ops
    results = await asyncio.gather(*parallel_ops, return_exceptions=True)
    audit_warnings = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            _exam_log.error("[SUBMIT] Phase 3 op %d failed for %s: %s", i, safe(result.session_id), safe(r))
            if i == 0:
                raise HTTPException(status_code=500,
                                    detail="Failed to save exam submission. Please retry.")
            else:
                audit_warnings.append(f"Audit log entry {i} failed to record")
    # TOCTOU check: if the update affected no rows, the session was already COMPLETED
    update_result = results[0] if not isinstance(results[0], BaseException) else None
    if update_result is not None and not update_result.data:
        _exam_log.warning("[SUBMIT] TOCTOU: session %s already completed by another request", safe(result.session_id))
        raise HTTPException(status_code=409, detail="Exam already submitted")

    # Phase 4: Risk score (non-fatal — submission already saved)
    risk_score_val = 0
    risk_label = "Unknown"
    try:
        risk = await compute_risk_score(result.session_id, teacher_id=tid)
        risk_score_val = risk["risk_score"]
        risk_label = risk["label"]
        upd = _atable("exam_sessions").eq("session_key", result.session_id)
        if tid:
            upd = upd.eq("teacher_id", str(tid))
        await upd.update({"risk_score": risk_score_val}).execute()
    except Exception as e:
        _exam_log.warning("[SUBMIT] risk score failed for %s: %s", safe(result.session_id), safe(e))

    get_logger(result.session_id).info(
        f"[SUBMIT] {trusted_roll} score:{server_score}/{server_total} "
        f"risk:{risk_score_val}/100")

    # Publish submission to dashboard SSE
    if tid:
        pub_task = asyncio.create_task(_bus_async_publish(f"sessions:{tid}", {"kind": "submitted",
                     "session_id": result.session_id}))
        pub_task.add_done_callback(
            lambda t: _exam_log.warning("[submit] SSE publish failed: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )

    # Push grade to LMS via AGS if this is an LTI student. Routed
    # through RQ so a transient Canvas/Moodle 5xx during submission
    # gets the standard 3-attempt retry (10/60/300s backoff) instead
    # of disappearing into a never-awaited create_task. When RQ is
    # disabled, fall back to the old fire-and-forget asyncio.create_task
    # path so local dev keeps working without Redis.
    if trusted_roll and server_total > 0:
        # Local import: there's another `from ..jobs import enqueue_job`
        # earlier in this function (async-scoring branch), which makes
        # Python treat enqueue_job as a function-scoped local. A second
        # local import here keeps the name bound on the inline path too.
        from ..jobs import enqueue_job as _enqueue_job, ags_grade_passback_job
        try:
            if _rq_enabled():
                # enqueue_job returns immediately when RQ is actually
                # connected to Redis (job queued). In sync-fallback
                # mode (no Redis env), the job runs inline and any
                # AgsTransientError it raises would otherwise bubble up
                # and 500 the submit response — so the try/except here
                # absorbs the failure on the inline path while keeping
                # the queued path's normal behaviour.
                _enqueue_job(
                    ags_grade_passback_job,
                    trusted_roll, server_score, server_total, pct,
                    teacher_id=str(tid) if tid else "",
                    queue_name="default",
                )
            else:
                asyncio.create_task(_try_ags_grade_passback(
                    trusted_roll, server_score, server_total, pct,
                    teacher_id=str(tid) if tid else None,
                ))
        except Exception as e:
            _exam_log.warning("[AGS] inline grade passback failed for %s: %s",
                              safe(result.session_id), safe(e))
        # Google Classroom passback (best-effort, fire-and-forget). Independent
        # of the LMS/AGS path — a student may be from Classroom, an LMS, both,
        # or neither; each path no-ops when its context is absent.
        try:
            asyncio.create_task(_try_google_grade_passback(
                trusted_roll, server_score, server_total, eid,
                teacher_id=str(tid) if tid else None,
            ))
        except Exception as e:
            _exam_log.warning("[GClass] inline grade passback failed for %s: %s",
                              safe(result.session_id), safe(e))

    resp = {"status": SessionStatus.SUBMITTED, "score": server_score,
            "total": server_total, "percentage": pct,
            "risk_score": risk_score_val, "risk_label": risk_label}
    if audit_warnings:
        resp["warnings"] = audit_warnings
    return resp


@router.get("/api/v1/session-status")
@limiter.limit("60/minute")
async def session_status(request: Request, session_id: str):
    """Lightweight poll endpoint for the async-scoring flow.

    The renderer calls this every ~1.5 s after `/submit-exam` returns
    `scoring: pending`. Returns:
      - `scoring: 'pending'` while the RQ worker is still processing
      - `status: 'completed'` with score/percentage/risk_score once done

    Cheap enough (one DB lookup, no joins) that polling 5 000 students
    at 1.5 s intervals is ~3 300 req/s peak — well within the API's
    capacity for read-only queries.
    """
    if is_practice(session_id):
        return {"status": "completed", "score": 0, "total": 0,
                "percentage": 0, "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, session_id)

    row = await _atable("exam_sessions")\
        .select("status,score,total,percentage,risk_score")\
        .eq("session_key", session_id).limit(1).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Session not found")

    rec = row.data[0]
    status = rec.get("status", "")

    # Scoring still pending — session was marked 'submitted' but the
    # background job hasn't flipped it to 'completed' yet.
    if status == SessionStatus.SUBMITTED and rec.get("score") is None:
        return {"status": SessionStatus.SUBMITTED, "scoring": "pending"}

    # Done — return the final numbers
    return {
        "status":      status or SessionStatus.SUBMITTED,
        "scoring":     "done",
        "score":       rec.get("score") or 0,
        "total":       rec.get("total") or 0,
        "percentage":  rec.get("percentage") or 0,
        "risk_score":  rec.get("risk_score") or 0,
    }


def _enqueue_s3_upload(safe_teacher_id: str, roll: str, filename: str,
                       local_path: str, content_type: str = "image/jpeg") -> None:
    """If S3 is enabled, enqueue a background upload of *local_path* to S3.

    The worker reads the file from local disk (it's already been written by
    *_save_frame* / *_save_id_verification_images*).  Never blocks or raises.
    Key = {safe_teacher_id}/{roll}/{filename}.
    """
    if not _s3_enabled():
        return
    if not safe_teacher_id:
        return
    s3_key = f"{safe_teacher_id}/{roll}/{filename}"
    try:
        from ..jobs import enqueue_job as _ej, upload_screenshot_job
        _ej(upload_screenshot_job, s3_key=s3_key,
            local_path=local_path, content_type=content_type)
    except Exception:
        _exam_log.warning("[S3] failed to enqueue upload for %s", s3_key)


def _save_context_frames(student_dir: str, event_type: str | None,
                         context_frames: list, flag_dt: datetime) -> list[str]:
    """Save pre-violation context frames as ``ctx_<label>_<ts>.jpg`` with each
    timestamp derived as ``flag_dt - offset_ms`` so the timeline/PDF order them
    t-3s → t-1s ahead of the flag. The distinct ``ctx_`` prefix keeps them out
    of the existing 1:1 primary matcher; a dedicated list-matcher gathers them.
    Returns the filenames written (oldest-first). Best-effort per frame."""
    os.makedirs(student_dir, exist_ok=True)
    label = "".join(c if c.isalnum() or c in "_-" else "_"
                    for c in (event_type or "frame"))[:32] or "frame"
    written: list[str] = []
    for cf in context_frames:
        try:
            when = flag_dt - timedelta(milliseconds=max(0, int(cf.offset_ms)))
            ts = when.strftime("%Y%m%d_%H%M%S")
            fname = f"ctx_{label}_{ts}.jpg"
            with open(os.path.join(student_dir, fname), "wb") as f:
                f.write(base64.b64decode(cf.frame_b64))
            written.append(fname)
        except Exception:
            continue  # a bad context frame must never fail the flag upload
    return written


def _save_frame(student_dir: str, data: FrameIn, room_jpeg: bytes | None = None,
                when: datetime | None = None) -> tuple[str, str | None]:
    """Decode and save a frame to disk. Returns (primary_filename, room_filename_or_None).

    When a phone-cam frame is supplied (captured at the same instant from the
    roomframe cache), it's written as a ``room_<label>_<ts>.jpg`` companion with
    the SAME timestamp as the primary ``evt_<label>_<ts>.jpg``, so the evidence
    PDF and the live timeline can show both cameras side by side for a flag.

    ``when`` pins the filename timestamp (so context frames can anchor to the
    flag's instant); defaults to now (UTC).
    """
    os.makedirs(student_dir, exist_ok=True)
    # UTC — NOT now_ist(). The evidence matcher
    # (match_screenshot_for_violation) builds its filename window from the
    # violation's created_at converted to UTC, so an IST filename was always
    # ~5.5h off the window → screenshots NEVER matched → reports/scorecards
    # showed no evidence images.
    ts = (when or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    if data.event_type:
        label = "".join(
            c if c.isalnum() or c in "_-" else "_"
            for c in data.event_type
        )[:32]
        fname = f"evt_{label}_{ts}.jpg"
    else:
        label = "frame"
        fname = f"frame_{ts}.jpg"
    fpath = os.path.join(student_dir, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(data.frame))
    # Companion phone-cam frame at the SAME moment. Best-effort: a missing
    # phone (not paired) just means no companion and the UI shows the primary
    # frame alone.
    room_fname = None
    if room_jpeg:
        try:
            room_fname = f"room_{label}_{ts}.jpg"
            with open(os.path.join(student_dir, room_fname), "wb") as rf:
                rf.write(room_jpeg)
        except Exception:
            room_fname = None
    return fname, room_fname


@router.post("/api/v1/analyze-frame")
@limiter.limit("30/minute")
async def analyze_frame(data: FrameIn, request: Request):
    # Practice sandbox: don't run face detection or save screenshots
    if is_practice(data.session_id):
        return {"status": "ok", "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, data.session_id)
    tid = claims.get("tid")

    # Size limit: reject oversized payloads
    if len(data.frame) > _MAX_FRAME_BASE64_LEN:
        raise HTTPException(status_code=413,
                            detail=f"Frame too large ({len(data.frame)} chars). Max {_MAX_FRAME_BASE64_LEN}.")

    # Pre-violation context frames: bound count (<=5) + per-frame size so a
    # client can't balloon the payload. Oversized/extra frames are dropped (not
    # a 4xx) so an old/buggy client never fails the flag upload over context.
    ctx_frames = [c for c in (data.context_frames or [])[:5]
                  if len(c.frame_b64) <= _MAX_FRAME_BASE64_LEN]

    # Sanitize roll/tid for path safety
    raw_roll = data.session_id.rsplit("_", 1)[0] if "_" in data.session_id \
               else data.session_id[:20]
    roll = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw_roll)[:40]
    safe_teacher_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in (tid or ""))[:40]

    if safe_teacher_id:
        student_dir = os.path.join(SCREENSHOTS_DIR, safe_teacher_id, roll)
    else:
        student_dir = os.path.join(SCREENSHOTS_DIR, roll)

    # Verify resolved path is under SCREENSHOTS_DIR
    real_dir = os.path.realpath(student_dir)
    real_base = os.path.realpath(SCREENSHOTS_DIR)
    if not real_dir.startswith(real_base + os.sep) and real_dir != real_base:
        raise HTTPException(status_code=400, detail="Invalid session identifier")

    # Snapshot the student's phone-cam frame at this same instant (the phone
    # streams ~1fps into the roomframe cache) so a flagged moment carries BOTH
    # cameras. Read the same way the room-cam/frame endpoint does. Absent when
    # no phone is paired — degrades to primary-only.
    room_jpeg = None
    try:
        if _cache:
            _rp = _cache.get(f"roomframe:{data.session_id}")
            if isinstance(_rp, dict):
                room_jpeg = _rp.get("jpeg_bytes")
    except Exception:
        room_jpeg = None

    # Single flag instant — the primary frame and all context frames anchor to
    # it so the timeline orders them t-3s → t-1s → flag.
    flag_dt = datetime.now(timezone.utc)
    try:
        fname, room_fname = await asyncio.to_thread(_save_frame, student_dir, data, room_jpeg, flag_dt)
    except Exception as e:
        _exam_log.error("[Frame] Error saving frame for %s: %s", safe(data.session_id), safe(e))
        raise HTTPException(status_code=500, detail="Failed to save frame")

    # Pre-violation context frames (appeal-critical events only). Best-effort —
    # a failure here must never fail the flag upload itself.
    ctx_fnames: list[str] = []
    if ctx_frames:
        try:
            ctx_fnames = await asyncio.to_thread(
                _save_context_frames, student_dir, data.event_type, ctx_frames, flag_dt)
        except Exception as e:
            _exam_log.warning("[Frame] context-frame save failed for %s: %s",
                              safe(data.session_id), safe(e))

    # Dual-write to S3 (best-effort background upload)
    _enqueue_s3_upload(safe_teacher_id, roll, fname,
                       os.path.join(student_dir, fname), "image/jpeg")
    if room_fname:
        _enqueue_s3_upload(safe_teacher_id, roll, room_fname,
                           os.path.join(student_dir, room_fname), "image/jpeg")
    for _cfn in ctx_fnames:
        _enqueue_s3_upload(safe_teacher_id, roll, _cfn,
                           os.path.join(student_dir, _cfn), "image/jpeg")
    return {"status": "received"}


def _save_id_verification_images(student_dir: str, selfie_b64: str, id_b64: str) -> tuple[str, str]:
    """Save selfie and ID card images to disk. Returns (selfie_fname, id_fname)."""
    os.makedirs(student_dir, exist_ok=True)
    ts = now_ist().strftime("%Y%m%d_%H%M%S")
    selfie_fname = f"id_selfie_{ts}.jpg"
    id_fname     = f"id_card_{ts}.jpg"
    with open(os.path.join(student_dir, selfie_fname), "wb") as f:
        f.write(base64.b64decode(selfie_b64))
    with open(os.path.join(student_dir, id_fname), "wb") as f:
        f.write(base64.b64decode(id_b64))
    return selfie_fname, id_fname


@router.post("/api/v1/id-verification")
@limiter.limit("30/minute")
async def id_verification(data: IdVerifyIn, request: Request):
    """Store selfie + ID photos and create a pending verification for teacher review."""
    # Practice sandbox: pretend the verification was filed
    if is_practice(data.session_id):
        return {"status": SessionStatus.SUBMITTED, "verification_id": "practice", "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, data.session_id)
    tid = claims.get("tid")

    # Size guard
    for field_name, field_val in [("selfie_frame", data.selfie_frame), ("id_frame", data.id_frame)]:
        if len(field_val) > _MAX_FRAME_BASE64_LEN:
            raise HTTPException(status_code=413, detail=f"{field_name} too large")

    # Sanitize roll for filesystem safety
    raw_roll = data.roll_number.strip().upper() or "UNKNOWN"
    roll = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw_roll)[:40]
    safe_teacher_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in (tid or ""))[:40]

    if safe_teacher_id:
        student_dir = os.path.join(SCREENSHOTS_DIR, safe_teacher_id, roll)
    else:
        student_dir = os.path.join(SCREENSHOTS_DIR, roll)

    # Path traversal guard
    real_dir = os.path.realpath(student_dir)
    real_base = os.path.realpath(SCREENSHOTS_DIR)
    if not real_dir.startswith(real_base + os.sep) and real_dir != real_base:
        raise HTTPException(status_code=400, detail="Invalid roll number")

    try:
        selfie_fname, id_fname = await asyncio.to_thread(
            _save_id_verification_images, student_dir, data.selfie_frame, data.id_frame)
    except Exception as e:
        _exam_log.error("[ID Verify] File save error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save verification images")

    # Dual-write ID verification images to S3 (best-effort background upload)
    _enqueue_s3_upload(safe_teacher_id, roll, selfie_fname,
                       os.path.join(student_dir, selfie_fname), "image/jpeg")
    _enqueue_s3_upload(safe_teacher_id, roll, id_fname,
                       os.path.join(student_dir, id_fname), "image/jpeg")

    # The violations table has a FK to exam_sessions(session_key) (phase80).
    # ID verification is the student's FIRST exam-start step — it runs BEFORE
    # the exam_started event / first heartbeat creates the session row (renderer
    # order: id-verification → startProctor → exam_started). So the violation
    # insert below would hit that FK and 500 ("Failed to record verification") —
    # the bug that surfaced once the CORS fix let this request reach the server.
    # Ensure the session row exists first: insert a minimal IN_PROGRESS row only
    # when absent (never resurrect a terminal one; a concurrent exam_started just
    # makes our insert a no-op race we swallow).
    try:
        existing_sess = await _atable("exam_sessions").select("session_key")\
            .eq("session_key", data.session_id).limit(1).execute()
        if not existing_sess.data:
            # Only these four are REQUIRED to satisfy violations' session FK.
            base_row = {
                "session_key": data.session_id,
                "roll_number": raw_roll,
                "status":      SessionStatus.IN_PROGRESS,
                "started_at":  now_ist().isoformat(),
            }
            # student_id (→ student_accounts), teacher_id (→ teachers) and
            # exam_id are nice-to-have for scoping/results, but each is its own
            # FK — a stale/mismatched sid claim (seen after manual session
            # surgery) fails the WHOLE insert, leaving no session row so the
            # verification below 500s on the FK. Attempt the full row, then fall
            # back to the bare required columns so the row always lands.
            full_row = dict(base_row)
            if claims.get("sid"):
                full_row["student_id"] = claims.get("sid")
            if tid:
                full_row["teacher_id"] = str(tid)
            if claims.get("eid"):
                full_row["exam_id"] = claims.get("eid")
            try:
                await _atable("exam_sessions").insert(full_row).execute()
            except Exception as e_full:
                _exam_log.warning(
                    "[ID Verify] full session insert failed (%s) — retrying with "
                    "required columns only", safe(e_full))
                await _atable("exam_sessions").insert(base_row).execute()
    except Exception as e:
        _exam_log.warning("[ID Verify] ensure-session failed (may be a race): %s", safe(e))

    try:
        detail_obj = {
            "status":       "pending",
            "selfie_file":  selfie_fname,
            "id_file":      id_fname,
            "roll_number":  roll,
            "full_name":    data.full_name,
            "exam_id":      claims.get("eid") or "",
        }
        viol_row = {
            "session_key":    data.session_id,
            "violation_type": "id_verification",
            "severity":       "low",
            "details":        json.dumps(detail_obj),
        }
        if tid:
            viol_row["teacher_id"] = str(tid)
        await _atable("violations").insert(viol_row).execute()
    except Exception as e:
        _exam_log.error("[ID Verify] DB error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to record verification. Please try again.")

    return {"status": "received"}


@router.get("/api/v1/id-verification/status")
@limiter.limit("30/minute")
async def id_verification_status(request: Request, session_id: str = ""):
    """Student polls this to check if teacher has approved/retake/rejected."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    # Practice sandbox: auto-approve
    if is_practice(session_id):
        return {"status": "approved", "practice": True}

    claims = require_auth(request)
    await _assert_student_session_access(claims, session_id)
    result = await _atable("violations")\
        .select("details")\
        .eq("session_key", session_id)\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    if not result.data:
        return {"status": "not_found"}
    raw = result.data[0].get("details", "")
    try:
        obj = json.loads(raw)
        # Surface reason fields so the student's retake / rejected screens
        # can show "Your examiner determined: <reason>" instead of the
        # generic copy. Empty strings on legacy rows (no migration), which
        # the renderer's _idReasonInline() short-circuits cleanly.
        return {
            "status":      obj.get("status", "pending"),
            "reason_code": obj.get("reason_code", "") or "",
            "reason_text": obj.get("reason_text", "") or "",
        }
    except Exception:
        _exam_log.warning("id_verification_status: malformed JSON for session %s", safe(session_id))
        return {"status": "pending", "reason_code": "", "reason_text": ""}


@router.get("/api/v1/events/{session_id}")
@limiter.limit("30/minute")
async def get_events(session_id: str, request: Request):
    claims = require_auth(request)
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")
    q = _atable("violations").select("session_key,violation_type,severity,details,created_at,teacher_id,detection_confidence").eq("session_key", session_id)
    if tid:
        q.eq("teacher_id", str(tid))
    q.order("created_at")
    result = await q.execute()
    events = result.data or []
    return {
        "session_id": session_id,
        "total":      len(events),
        "events": [
            {
                "id":        e.get("id") or ts_to_id(e.get("created_at", "")),
                "type":      e["violation_type"],
                "severity":  e["severity"],
                "timestamp": fmt_ist(e.get("created_at", "")),
                "details":   e.get("details"),
            }
            for e in events
        ],
    }


@router.post("/api/v1/room-cam-token")
@limiter.limit("10/minute")
async def room_cam_token(request: Request, body: dict = Body(...)):
    """Issue a time-bound room-cam JWT for phone pairing.

    Body: {"session_id": "ALICE001_abc-123"}
    Returns a 2-hour token with scope='room-cam'.
    The phone uses this token to authenticate its WebSocket connection.
    """
    student = require_auth(request)
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    await _assert_student_session_access(student, session_id)
    roll = student.get("roll", "")
    import jwt as _jwt
    token = _jwt.encode({
        "scope": "room-cam",
        "sid": session_id,
        "roll": roll,
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
    }, ROOM_CAM_SIGNING_KEY, algorithm="HS256")
    return {"token": token, "session_id": session_id, "expires_in_hours": 2}


@router.get("/api/v1/room-cam-qr")
@limiter.limit("30/minute")
async def room_cam_qr(request: Request):
    """Generate a QR code SVG for phone camera pairing."""
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    student = require_auth(request)
    await _assert_student_session_access(student, session_id)
    roll = student.get("roll", "")
    # Cache QR token for 60s to avoid re-minting on refresh
    if not hasattr(room_cam_qr, "_token_cache"):
        room_cam_qr._token_cache: dict[str, dict] = {}
    cache_key = f"{session_id}:{roll}"
    cached = room_cam_qr._token_cache.get(cache_key)
    if cached and cached["expires"] > time.time():
        token = cached["token"]
    else:
        import jwt as _jwt
        token = _jwt.encode({
            "scope": "room-cam", "sid": session_id, "roll": roll,
            "exp": datetime.now(timezone.utc) + timedelta(hours=2),
        }, ROOM_CAM_SIGNING_KEY, algorithm="HS256")
        room_cam_qr._token_cache[cache_key] = {"token": token, "expires": time.time() + 60}
    from ..invites import _get_invite_base_url
    base = _get_invite_base_url()
    url = f"{base}/phone-cam#token={token}&sid={session_id}"
    import io
    import qrcode
    qr = qrcode.QRCode(border=2, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    # make_image() returns a Pillow image; emit a PNG. (The old code called
    # .to_string() — an SVG-factory-only method — on that PIL image, which
    # raised AttributeError -> 500, and the 500 lacked CORS headers so the
    # browser misreported it as a CORS failure.)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    from fastapi.responses import Response as _Resp
    return _Resp(content=buf.getvalue(), media_type="image/png",
                 headers={"Cache-Control": "no-cache"})


@router.get("/api/v1/room-cam-approval-status")
@limiter.limit("30/minute")
async def room_cam_approval_status(request: Request):
    """Return the room camera approval status for the current session.

    The renderer polls this to know when the teacher has approved.
    """
    session_id = (request.query_params.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    student = require_auth(request)
    await _assert_student_session_access(student, session_id)
    row = await _atable("exam_sessions").select("room_cam_status,room_cam_approved_at")\
        .eq("session_key", session_id).limit(1).execute()
    data = row.data[0] if row.data else {}
    return {
        "status": data.get("room_cam_status", "disabled"),
        "approved_at": data.get("room_cam_approved_at"),
    }
