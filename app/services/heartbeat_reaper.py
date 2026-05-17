"""Background task that reaps stale exam sessions.

A session is considered ABANDONED when its ``last_heartbeat`` timestamp is
more than HEARTBEAT_TIMEOUT_SECS seconds in the past AND the session status
is still ACTIVE or IN_PROGRESS.

On detection the reaper:
1. Marks the session status = 'ABANDONED'.
2. POSTs a 'session_abandoned' violation event (visible in the admin dashboard).
3. Calls the existing submit-answers logic so whatever answers the student
   saved up to the point of disconnection are scored and stored, rather than
   being lost.

The loop runs every REAPER_INTERVAL_SECS seconds (default 60).  Only the
leader worker runs the reaper (enforced by the caller in main.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

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

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_TIMEOUT_SECS)).isoformat()

    # Find sessions that are still marked active/in_progress but have a
    # last_heartbeat older than the cutoff (or NULL — pre-heartbeat sessions
    # are excluded by the last_heartbeat IS NOT NULL filter so we don't
    # accidentally reap sessions that predate heartbeat support).
    try:
        result = await _atable("sessions").select(
            "id,session_id,roll_number,teacher_id,answers"
        ).in_(
            "status", ["ACTIVE", "IN_PROGRESS"]
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
        sid = row.get("session_id") or row.get("id")
        try:
            await _mark_abandoned(row, _atable)
        except Exception as e:
            logger.error("[reaper] failed to abandon session %s: %s", sid, e)


async def _mark_abandoned(row: dict, _atable) -> None:
    """Mark a single session ABANDONED and attempt to score saved answers."""
    sid       = row.get("session_id") or row.get("id")
    row_id    = row.get("id")
    teacher_id = str(row.get("teacher_id") or "")
    roll      = row.get("roll_number", "unknown")

    # 1. Mark ABANDONED
    await _atable("sessions").update({
        "status": "ABANDONED",
    }).eq("id", row_id).execute()

    logger.info("[reaper] session %s (%s) marked ABANDONED", sid, roll)

    # 2. Record a violation event so it appears in the admin timeline
    try:
        await _atable("violation_events").insert({
            "session_id": sid,
            "event_type": "session_abandoned",
            "severity": "high",
            "details": "Session auto-abandoned: heartbeat timeout exceeded",
        }).execute()
    except Exception as e:
        logger.warning("[reaper] violation insert failed for %s: %s", sid, e)

    # 3. Auto-submit whatever answers were saved so the student isn't penalised
    #    for a network drop.  Call recalculate_score directly (no HTTP round-trip)
    #    and persist the result so teachers see a scored session, not a void.
    answers = row.get("answers")
    if not answers:
        return  # nothing to score

    try:
        from ..services.scoring import recalculate_score
        score, total = await recalculate_score(
            sid, answers, teacher_id=teacher_id
        )
        pct = round((score / max(total, 1)) * 100, 1)
        await _atable("exam_sessions").update({
            "score":      score,
            "total":      total,
            "percentage": pct,
            "status":     "COMPLETED",
        }).eq("session_key", sid).execute()
        logger.info("[reaper] auto-scored session %s: %d/%d (%.1f%%)", sid, score, total, pct)
    except Exception as e:
        logger.warning("[reaper] auto-score failed for %s: %s", sid, e)
