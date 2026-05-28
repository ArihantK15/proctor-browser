"""RQ job for async exam scoring.

When ASYNC_SCORING_ENABLED=1 the /submit-exam handler returns 202 immediately
after persisting answers + marking status='submitted', and this job:

  1. Recalculates the score on the saved answer set
  2. Computes the risk score
  3. Marks the session COMPLETED with final score, percentage, risk_score
  4. Publishes the submission event to the dashboard SSE bus
  5. Fires LMS grade passback (AGS) if the student came in via LTI

The job is idempotent — re-running it on an already-COMPLETED session is a
no-op. That matters because RQ retries failed jobs.
"""
from __future__ import annotations

from ..log_safe import safe
import asyncio
import logging
from datetime import datetime
from typing import Optional

from .helpers import _run_coro_in_sync

logger = logging.getLogger("scoring_jobs")


async def _score_submission_async(
    session_id: str,
    teacher_id: Optional[str],
    exam_id: Optional[str],
    **kwargs,
) -> dict:
    """The actual scoring work — runs inside the RQ worker.

    Returns a dict with the computed score so the worker logs are useful.
    Errors are raised so RQ's retry policy fires.
    """
    time_taken_secs = kwargs.get("time_taken_secs", 0)
    roll_number = kwargs.get("roll_number", "")
    from ..database import async_table as _atable
    from ..services.scoring import recalculate_score
    from ..services.risk import compute_risk_score
    from ..repositories.questions import load_exam_config
    from ..models import SessionStatus
    from ..utils import now_ist
    from ..event_bus import async_publish as _bus_async_publish

    # Idempotency guard — if another retry already completed it, bail.
    existing = await _atable("exam_sessions")\
        .select("status,score,total,percentage,risk_score,started_at,full_name,email")\
        .eq("session_key", session_id).limit(1).execute()
    if not existing.data:
        logger.warning("[score_job] session %s not found, bailing", safe(session_id))
        return {"status": "not_found"}
    sess = existing.data[0]
    if sess.get("status") in (SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED):
        logger.info("[score_job] session %s already completed/force-submitted, skipping", safe(session_id))
        return {"status": "already_completed", "score": sess.get("score"),
                "total": sess.get("total"), "percentage": sess.get("percentage")}

    # 1. Load saved answers from DB (the submit handler already persisted them)
    saved = await _atable("answers").select("question_id,answer")\
        .eq("session_key", session_id).execute()
    ans_payload = {str(r["question_id"]): str(r["answer"]) for r in (saved.data or [])}

    # 2. Score + config in parallel
    score_fut = recalculate_score(session_id, ans_payload, teacher_id=teacher_id, exam_id=exam_id)
    config_fut = load_exam_config(teacher_id=teacher_id, exam_id=exam_id)
    try:
        (server_score, server_total), config = await asyncio.gather(score_fut, config_fut)
    except Exception as e:
        logger.error("[score_job] scoring failed for %s: %s", safe(session_id), safe(e))
        raise

    pct = round((server_score / max(server_total, 1)) * 100, 1)

    # 3. Risk score (non-fatal — submission is already saved)
    risk_score_val = 0
    risk_label = "Unknown"
    try:
        risk = await compute_risk_score(session_id, teacher_id=teacher_id)
        risk_score_val = risk["risk_score"]
        risk_label = risk["label"]
    except Exception as e:
        logger.warning("[score_job] risk score failed for %s: %s", safe(session_id), safe(e))

    # 4. Final session write — flip to COMPLETED with final numbers
    now = now_ist()
    session_row = {
        "score":           server_score,
        "total":           server_total,
        "percentage":      pct,
        "risk_score":      risk_score_val,
        "status":          SessionStatus.COMPLETED,
    }
    # Only set submitted_at if it isn't already (submit handler may have set it).
    # Pass a datetime OBJECT, not .isoformat() — asyncpg's timestamptz_encode
    # rejects strings here even though postgres_table._SQL.add has a coercion
    # heuristic. The heuristic was bypassed when this code ran from the RQ
    # worker container which may have stale postgres_table.py. Passing a
    # datetime directly avoids the entire round-trip and works against any
    # version of the adapter.
    if not sess.get("submitted_at"):
        session_row["submitted_at"] = now

    upd_q = _atable("exam_sessions").update(session_row).eq("session_key", session_id)
    if teacher_id:
        upd_q = upd_q.eq("teacher_id", str(teacher_id))
    # Guard against double-completion with .neq() — same TOCTOU defence as submit
    upd_q = upd_q.neq("status", SessionStatus.COMPLETED).neq("status", SessionStatus.FORCE_SUBMITTED)
    upd_result = await upd_q.execute()
    if not upd_result.data:
        logger.info("[score_job] session %s already COMPLETED by another path", safe(session_id))
        return {"status": "race_condition", "score": server_score, "total": server_total}

    # 5. Submission violation log (audit row — same as inline path wrote).
    # Skip if one already exists from a previous run (RQ retries this job
    # on failure, so we have to be idempotent — without this check, retries
    # would duplicate the audit row).
    try:
        existing_log = await _atable("violations")\
            .select("id")\
            .eq("session_key", session_id)\
            .eq("violation_type", "exam_submitted")\
            .limit(1).execute()
        if not existing_log.data:
            viol = {
                "session_key":    session_id,
                "violation_type": "exam_submitted",
                "severity":       "low",
                "details":        f"Score:{server_score}/{server_total} ({pct}%)",
            }
            if teacher_id:
                viol["teacher_id"] = teacher_id
            await _atable("violations").insert(viol).execute()
    except Exception as e:
        logger.warning("[score_job] audit insert failed for %s: %s", safe(session_id), safe(e))

    # 6. Time-exceeded violation (if applicable, idempotent like step 5).
    # Use server-computed elapsed time (started_at → submitted_at) — the
    # client-supplied time_taken_secs is just an upper-bound fallback,
    # mirroring the security check in the inline path (H44).
    allowed_secs = config.get("duration_minutes", 60) * 60
    server_elapsed = time_taken_secs
    started_at_str = sess.get("started_at")
    if started_at_str:
        try:
            started = datetime.fromisoformat(str(started_at_str).replace("Z", "+00:00"))
            server_elapsed = (now - started).total_seconds()
        except (ValueError, TypeError):
            pass  # fall back to client-supplied value
    if server_elapsed and server_elapsed > allowed_secs + 120:
        try:
            existing_time_log = await _atable("violations")\
                .select("id")\
                .eq("session_key", session_id)\
                .eq("violation_type", "time_exceeded")\
                .limit(1).execute()
            if not existing_time_log.data:
                time_viol = {
                    "session_key":    session_id,
                    "violation_type": "time_exceeded",
                    "severity":       "high",
                    "details":        f"Submitted {int(server_elapsed - allowed_secs)}s past time limit",
                }
                if teacher_id:
                    time_viol["teacher_id"] = teacher_id
                await _atable("violations").insert(time_viol).execute()
        except Exception as e:
            logger.warning("[score_job] time-exceeded audit failed for %s: %s", safe(session_id), safe(e))

    # 7. Publish to dashboard SSE (best-effort)
    if teacher_id:
        try:
            await _bus_async_publish(f"sessions:{teacher_id}", {
                "kind": "submitted",
                "session_id": session_id,
                "score": server_score,
                "total": server_total,
            })
        except Exception as e:
            logger.warning("[score_job] SSE publish failed for %s: %s", safe(session_id), safe(e))

    # 8. LMS grade passback (best-effort)
    if roll_number and server_total > 0:
        try:
            from ..routers.exam import _try_ags_grade_passback
            await _try_ags_grade_passback(roll_number, server_score, server_total, pct)
        except Exception as e:
            logger.warning("[score_job] AGS passback failed for %s: %s", safe(session_id), safe(e))

    logger.info("[score_job] %s scored: %d/%d (%s%%) risk=%d",
                safe(session_id), server_score, server_total, pct, risk_score_val)

    return {
        "status": "completed",
        "score": server_score,
        "total": server_total,
        "percentage": pct,
        "risk_score": risk_score_val,
        "risk_label": risk_label,
    }


def score_submission_job(
    session_id: str,
    teacher_id: Optional[str] = None,
    exam_id: Optional[str] = None,
    student_id: Optional[str] = None,
    roll_number: str = "",
    time_taken_secs: int = 0,
) -> dict:
    """Sync wrapper called by the RQ worker process."""
    return _run_coro_in_sync(_score_submission_async(
        session_id=session_id,
        teacher_id=teacher_id,
        exam_id=exam_id,
        roll_number=roll_number,
        time_taken_secs=time_taken_secs,
    ))
