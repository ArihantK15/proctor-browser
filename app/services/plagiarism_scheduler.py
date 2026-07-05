"""Leader-only periodic loop that auto-triggers plagiarism checks for
recently-ended exams. Same shape as heartbeat_reaper_loop/ttl_sweeper_loop
in app/main.py — a plain asyncio loop with a sleep interval, registered
only on the leader worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..database import async_table as _atable

logger = logging.getLogger("plagiarism_scheduler")

CHECK_INTERVAL_SECS = int(os.environ.get("PLAGIARISM_SCHEDULER_INTERVAL_SECS", "300"))


async def _find_exams_to_check() -> list[dict[str, Any]]:
    """Exams whose ends_at has passed and that have no row yet in
    coding_plagiarism_checks (never checked) or were previously marked
    'failed' (worth retrying)."""
    ended = (await _atable("exam_config")
             .select("exam_id,teacher_id")
             .lt("ends_at", datetime.now(timezone.utc).isoformat())
             .execute())
    if not ended.data:
        return []

    checked = (await _atable("coding_plagiarism_checks")
               .select("exam_id,status").execute())
    checked_map = {row["exam_id"]: row["status"] for row in (checked.data or [])}

    return [
        row for row in ended.data
        if checked_map.get(row["exam_id"]) != "ok"
    ]


async def plagiarism_scheduler_loop() -> None:
    from ..jobs import enqueue_job, check_plagiarism_job

    while True:
        try:
            exams = await _find_exams_to_check()
            for exam in exams:
                logger.info("[plagiarism_scheduler] enqueueing check for exam=%s", exam["exam_id"])
                enqueue_job(
                    check_plagiarism_job,
                    exam_id=exam["exam_id"],
                    teacher_id=exam.get("teacher_id"),
                    queue_name="default",
                )
        except Exception as e:
            logger.exception("[plagiarism_scheduler] unhandled error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECS)
