"""RQ jobs for LTI grade passback.

When ``RQ_ENABLED=1`` the AGS push goes through this job so transient
LMS failures (Canvas/Moodle 5xx, network blips during a maintenance
window) get the standard 3-attempt retry with 10/60/300s backoff
instead of disappearing into a fire-and-forget asyncio task.

Idempotency: LTI AGS POST is itself idempotent for the same
(lineitem, user_id) pair — repeat submits update the score in place.
So retrying after a partial failure is always safe.
"""
from __future__ import annotations

import logging

from .helpers import _run_coro_in_sync

logger = logging.getLogger("lti_jobs")


def ags_grade_passback_job(
    roll_number: str,
    score: int,
    total: int,
    percentage: float,
) -> dict:
    """Sync wrapper called by the RQ worker.

    Calls _try_ags_grade_passback with raise_on_failure=True so that a
    transient post_score failure (LMS 5xx, access-token miss, asyncpg
    error) raises AgsTransientError. RQ catches the exception and
    re-enqueues with the standard 10/60/300s backoff. A clean return
    (success OR "no LTI context to push to") completes the job.
    """
    from ..routers.exam import _try_ags_grade_passback

    async def _run() -> dict:
        await _try_ags_grade_passback(
            roll_number, score, total, percentage,
            raise_on_failure=True,
        )
        return {"ok": True}

    return _run_coro_in_sync(_run())
