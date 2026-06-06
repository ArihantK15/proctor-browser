"""Periodic session-state reconciler — the redundancy layer.

Detects and heals exam_sessions rows that have drifted into an inconsistent
state, so a transient failure (a scoring worker that died, a crash between two
writes) self-corrects instead of silently stranding a student's attempt:

  1. SUBMITTED for too long  → scoring never finished; re-enqueue it. With async
     scoring OFF (prod default) submit scores inline, so this should be empty —
     it's the safety net for when async is on or an inline submit half-failed.
  2. COMPLETED/FORCE_SUBMITTED with NULL submitted_at → backfill submitted_at so
     the row satisfies the consistency invariant (phase95 CHECK).
  3. COMPLETED with NULL score → re-enqueue scoring (finished but unscored).

Every anomaly is logged AND reported to Sentry (if configured) so drift is
*seen*, not discovered weeks later. Leader-worker only (wired in main.py like
the heartbeat reaper / ttl sweeper). All operations are idempotent.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from ..database import async_table as _atable
from ..models.exam import SessionStatus, RESULT_STATUSES

logger = logging.getLogger(__name__)

RECONCILER_INTERVAL_SECS      = int(os.environ.get("RECONCILER_INTERVAL_SECS",      "300"))   # 5 min
RECONCILER_STARTUP_DELAY_SECS = int(os.environ.get("RECONCILER_STARTUP_DELAY_SECS", "120"))   # 2 min
# How long a session may sit in SUBMITTED before we treat scoring as stuck.
RECONCILER_STUCK_SUBMITTED_SECS = int(os.environ.get("RECONCILER_STUCK_SUBMITTED_SECS", "600"))  # 10 min


def _report(msg: str) -> None:
    """Log + best-effort Sentry capture so drift is observable."""
    logger.warning("[reconciler] %s", msg)
    try:
        import sentry_sdk
        sentry_sdk.capture_message(f"[reconciler] {msg}", level="warning")
    except Exception:
        pass


def _enqueue_rescore(row: dict) -> bool:
    """Re-enqueue the (idempotent) scoring job for a drifted row."""
    try:
        from ..jobs import enqueue_job, score_submission_job
        enqueue_job(
            score_submission_job,
            session_id=row["session_key"],
            teacher_id=row.get("teacher_id"),
            exam_id=row.get("exam_id"),
            student_id=row.get("student_id"),
            roll_number=row.get("roll_number") or (
                row["session_key"].rsplit("_", 1)[0] if "_" in row["session_key"] else ""),
            time_taken_secs=row.get("time_taken_secs") or 0,
            queue_name="scoring",
        )
        return True
    except Exception as e:
        logger.exception("[reconciler] re-enqueue scoring failed for %s: %s",
                         row.get("session_key"), e)
        return False


async def _reconcile_once() -> dict:
    healed = {"stuck_submitted": 0, "missing_submitted_at": 0, "completed_no_score": 0}
    now = datetime.now(timezone.utc)
    fields = "session_key,status,teacher_id,exam_id,student_id,roll_number,submitted_at,score,time_taken_secs"

    # 1. SUBMITTED stuck past the cutoff → scoring never completed → re-enqueue.
    cutoff = (now - timedelta(seconds=RECONCILER_STUCK_SUBMITTED_SECS)).isoformat()
    stuck = (await _atable("exam_sessions").select(fields)
             .eq("status", SessionStatus.SUBMITTED)
             .lt("submitted_at", cutoff)
             .limit(200).execute()).data or []
    for row in stuck:
        if _enqueue_rescore(row):
            healed["stuck_submitted"] += 1

    # 3. COMPLETED with NULL score → finished but unscored → re-enqueue.
    no_score = (await _atable("exam_sessions").select(fields)
                .eq("status", SessionStatus.COMPLETED)
                .is_("score", "null")
                .limit(200).execute()).data or []
    for row in no_score:
        if _enqueue_rescore(row):
            healed["completed_no_score"] += 1

    # 2. RESULT-state rows missing submitted_at → backfill (consistency invariant).
    for st in RESULT_STATUSES:
        rows = (await _atable("exam_sessions").select("session_key,teacher_id")
                .eq("status", st).is_("submitted_at", "null")
                .limit(200).execute()).data or []
        for row in rows:
            try:
                await (_atable("exam_sessions")
                       .update({"submitted_at": now.isoformat()})
                       .eq("session_key", row["session_key"])
                       .is_("submitted_at", "null").execute())
                healed["missing_submitted_at"] += 1
            except Exception:
                logger.debug("[reconciler] submitted_at backfill failed for %s",
                             row.get("session_key"), exc_info=True)

    total = sum(healed.values())
    if total:
        _report(f"healed {total} drifted session(s): {healed}")
    return healed


async def session_reconciler_loop() -> None:
    """Run forever, reconciling drifted sessions every interval."""
    await asyncio.sleep(RECONCILER_STARTUP_DELAY_SECS)
    while True:
        try:
            await _reconcile_once()
        except Exception as e:
            logger.exception("[reconciler] unhandled error: %s", e)
        await asyncio.sleep(RECONCILER_INTERVAL_SECS)
