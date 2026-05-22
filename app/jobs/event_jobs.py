"""RQ jobs for off-request-path event/violation persistence.

The /api/v1/event endpoint fires from the browser on EVERY proctoring
detection (face_lost, multiple_faces, tab_switch, copy_paste, etc).
At 3000 concurrent students that's ~25 inserts/sec into the
`violations` table just from heartbeat-cadence alone, plus burst
from event-driven detections.

Doing those INSERTs synchronously on the request path means:

  1. The browser waits ~5-20ms for the DB ack before the next
     detection can be sent
  2. asyncpg pool slots are held for the duration of the INSERT
  3. Postgres back-end count climbs proportionally to concurrent
     students

Moving the INSERT to the autosave queue (which already has 2
dedicated workers) keeps the dashboard SSE fast (still inline) while
the durable DB write happens in the background. The audit row lands
within ~50-200ms instead of being on the critical path.

What stays SYNCHRONOUS in routers/exam.py /api/v1/event:
  - exam_started: writes to exam_sessions which subsequent
    bulk_save calls depend on. Race-unsafe to defer.
  - submit_failed: low-volume critical alert path; the row should
    be durable before the response returns so the operator's
    follow-up admin-submit call sees it.
  - Redis cache invalidation: in-memory, microsecond-scale.
  - Redis pub/sub publish: SSE subscribers need the event NOW.
"""
from __future__ import annotations

import logging
from typing import Any

from .helpers import _run_coro_in_sync

logger = logging.getLogger("event_jobs")


async def _record_violation_async(viol_row: dict[str, Any]) -> dict:
    """Insert a single violation row asynchronously."""
    from ..database import async_table as _atable

    try:
        await _atable("violations").insert(viol_row).execute()
        return {"status": "recorded"}
    except Exception as e:
        # Don't raise — we don't want RQ to retry indefinitely on a
        # bad row. Log + return so the worker stays clean. The audit
        # row is best-effort; if a single insert fails the SSE event
        # has already gone out and the operator sees real-time
        # signal. Retries would only ever matter for transient DB
        # blips, which RQ's outer retry policy handles separately.
        logger.warning(
            "[event_job] insert failed for session=%s type=%s: %s",
            viol_row.get("session_key"),
            viol_row.get("violation_type"),
            e,
        )
        return {"status": "failed", "error": str(e)}


def record_violation_job(viol_row: dict[str, Any]) -> dict:
    """Sync wrapper called by the RQ worker process.

    viol_row is a dict of primitives (str/int/None). All fields are
    picklable, no datetime objects — keep it that way so the RQ
    serializer doesn't choke.
    """
    return _run_coro_in_sync(_record_violation_async(viol_row))
