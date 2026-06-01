"""Background task that reaps stale exam sessions.

A session is considered ABANDONED when its ``last_heartbeat`` timestamp is
more than HEARTBEAT_TIMEOUT_SECS seconds in the past AND the session status
is still ACTIVE or IN_PROGRESS.

On detection the reaper:
1. Marks the exam session status = 'ABANDONED'.
2. Records a 'session_abandoned' violation event (visible in the admin dashboard).
3. Scores and persists the latest Redis autosave snapshot when available, so
   a network drop does not discard already-saved answers.

The loop runs every REAPER_INTERVAL_SECS seconds (default 60).  Only the
leader worker runs the reaper (enforced by the caller in main.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from ..models import SessionStatus

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_SECS = int(os.environ.get("HEARTBEAT_TIMEOUT_SECS", "300"))   # 5 min
REAPER_INTERVAL_SECS   = int(os.environ.get("REAPER_INTERVAL_SECS",   "60"))    # 1 min


async def heartbeat_reaper_loop() -> None:
    """Run forever, sweeping for abandoned sessions every REAPER_INTERVAL_SECS."""
    while True:
        try:
            await _reap_once()
        except Exception as e:
            logger.exception("[reaper] unhandled error: %s", e)
        await asyncio.sleep(REAPER_INTERVAL_SECS)


async def _reap_once() -> None:
    from ..database import async_table as _atable

    # Pass a real datetime object (not .isoformat()) — asyncpg's postgres
    # backend rejects ISO strings for timestamptz parameters with
    # "expected a datetime.date or datetime.datetime instance, got 'str'".
    # The Supabase REST backend coerces datetimes to strings on its own,
    # so this works for both backends.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_TIMEOUT_SECS)

    # Find sessions that are still marked active/in_progress but have a
    # last_heartbeat older than the cutoff (or NULL — pre-heartbeat sessions
    # are excluded by the last_heartbeat IS NOT NULL filter so we don't
    # accidentally reap sessions that predate heartbeat support).
    try:
        result = await _atable("exam_sessions").select(
            "session_key,roll_number,teacher_id,exam_id,student_id,last_heartbeat"
        ).in_(
            "status", [SessionStatus.IN_PROGRESS]
        ).not_.is_(
            "last_heartbeat", "null"
        ).lt(
            "last_heartbeat", cutoff
        ).execute()
    except Exception as e:
        logger.warning("[reaper] DB query failed: %s", e)
        return

    rows = result.data or []
    if not rows:
        return

    logger.info("[reaper] found %d stale session(s) to abandon", len(rows))

    for row in rows:
        sid = row.get("session_key")
        try:
            await _mark_abandoned(row, _atable)
        except Exception as e:
            logger.error("[reaper] failed to abandon session %s: %s", sid, e)


async def _mark_abandoned(row: dict, _atable) -> None:
    """Mark a single session ABANDONED and attempt to score saved answers."""
    sid       = row.get("session_key")
    teacher_id = str(row.get("teacher_id") or "")
    exam_id    = row.get("exam_id")
    student_id = row.get("student_id")
    roll      = row.get("roll_number", "unknown")
    if not sid:
        return

    # Guard: skip if session already progressed to a terminal state since SELECT
    try:
        cur = await _atable("exam_sessions").select("status")\
            .eq("session_key", sid).limit(1).execute()
        if cur.data and cur.data[0].get("status") in (
            SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED,
            SessionStatus.SUBMITTED, SessionStatus.REJECTED,
        ):
            logger.info("[reaper] session %s already terminal (%s), skipping",
                        sid, cur.data[0].get("status"))
            return
    except Exception as e:
        logger.warning("[reaper] status re-check failed for %s: %s", sid, e)

    # 1. Mark ABANDONED — atomic with teacher_id filter so a stale UPDATE
    # cannot bleed across tenants if the session row got reassigned
    # between our initial SELECT and now. Matches the TOCTOU defence
    # applied to admin_sessions / admin_org destructive UPDATEs.
    upd_q = _atable("exam_sessions").update({
        "status": SessionStatus.ABANDONED,
    }).eq("session_key", sid)
    if teacher_id:
        upd_q = upd_q.eq("teacher_id", teacher_id)
    await upd_q.execute()

    logger.info("[reaper] session %s (%s) marked ABANDONED", sid, roll)

    # 2. Record a violation event so it appears in the admin timeline
    try:
        viol = {
            "session_key": sid,
            "violation_type": "session_abandoned",
            "severity": "high",
            "details": "Session auto-abandoned: heartbeat timeout exceeded",
        }
        if teacher_id:
            viol["teacher_id"] = teacher_id
        await _atable("violations").insert(viol).execute()
    except Exception as e:
        logger.warning("[reaper] violation insert failed for %s: %s", sid, e)

    # 3. Auto-submit whatever answers were saved in Redis autosave so the
    #    student isn't penalised for a network drop.
    try:
        from ..services.autosave import load_autosave_snapshot, flush_answers_to_db
        snapshot = await asyncio.to_thread(load_autosave_snapshot, sid) or {}
        # The earlier code did `snapshot.get("answers") if isinstance(...) else snapshot`,
        # which silently fell back to the WHOLE snapshot dict (session_id,
        # teacher_id, etc. as keys) when "answers" wasn't a dict —
        # flush_answers_to_db would then iterate those metadata keys as
        # if they were question_id/answer pairs and write garbage rows
        # to the answers table. Take "answers" or nothing.
        raw_answers = snapshot.get("answers")
        answers = raw_answers if isinstance(raw_answers, dict) else {}
    except Exception as e:
        logger.warning("[reaper] autosave snapshot load failed for %s: %s", sid, e)
        answers = {}
    if not answers:
        return  # nothing to score

    try:
        from ..services.scoring import recalculate_score
        await flush_answers_to_db(
            sid,
            answers,
            teacher_id=teacher_id or None,
            exam_id=exam_id,
            student_id=student_id,
            delete_after=True,
        )
        score, total = await recalculate_score(
            sid, answers, teacher_id=teacher_id or None, exam_id=exam_id
        )
        pct = round((score / max(total, 1)) * 100, 1)
        # Atomic update with teacher_id filter — defense-in-depth as
        # above on the ABANDONED transition.
        final_upd_q = _atable("exam_sessions").update({
            "score":      score,
            "total":      total,
            "percentage": pct,
            "status":     SessionStatus.FORCE_SUBMITTED,
            # ISO string is fine for UPDATE values — asyncpg's column-level
            # input coercion handles it. Only WHERE-filter parameters
            # (see cutoff above) need a real datetime to satisfy the
            # prepared-statement parameter type check.
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("session_key", sid)
        if teacher_id:
            final_upd_q = final_upd_q.eq("teacher_id", teacher_id)
        await final_upd_q.execute()
        logger.info("[reaper] auto-scored session %s: %d/%d (%.1f%%)", sid, score, total, pct)
    except Exception as e:
        logger.warning("[reaper] auto-score failed for %s: %s", sid, e)
