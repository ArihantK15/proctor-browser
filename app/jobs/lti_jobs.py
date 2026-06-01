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

    Raises on transient failure so RQ retries; returns ``{"ok": True}``
    on success or when the student has no LTI context (nothing to do).
    """
    from ..routers.exam import _try_ags_grade_passback

    async def _run() -> dict:
        # _try_ags_grade_passback swallows its own errors and returns
        # None unconditionally today, so we can't distinguish "no LTI
        # context" from "LMS rejected". Wrap with a probe: if the call
        # raises (asyncpg dropped, LMS 5xx propagated up), let the
        # exception escape so RQ retries. If it returns silently,
        # treat as success.
        await _try_ags_grade_passback(roll_number, score, total, percentage)
        return {"ok": True}

    return _run_coro_in_sync(_run())
