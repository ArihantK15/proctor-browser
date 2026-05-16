"""Redis-first autosave snapshots and durable answer flushing."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

from .. import cache as _cache
from ..database import async_table as _atable
from .scoring import canonicalise_student_answer

log = logging.getLogger(__name__)

AUTOSAVE_TTL_SECONDS = int(os.environ.get("AUTOSAVE_TTL_SECONDS", str(6 * 60 * 60)))
AUTOSAVE_FLUSH_LOCK_SECONDS = int(os.environ.get("AUTOSAVE_FLUSH_LOCK_SECONDS", "10"))
AUTOSAVE_QUEUE_NAME = os.environ.get("RQ_AUTOSAVE_QUEUE", "autosave")


def _snapshot_key(session_id: str) -> str:
    return f"autosave:{session_id}"


def _flush_lock_key(session_id: str) -> str:
    return f"autosave_flush_lock:{session_id}"


def _final_flush_key(session_id: str) -> str:
    return f"autosave_final_flush:{session_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_answers(answers: dict[str, Any] | None) -> dict[str, str]:
    return {
        str(qid): "" if answer is None else str(answer)
        for qid, answer in (answers or {}).items()
    }


def cache_autosave_snapshot(
    session_id: str,
    answers: dict[str, Any],
    *,
    teacher_id: str | None = None,
    exam_id: str | None = None,
    student_id: str | None = None,
    final: bool = False,
) -> tuple[bool, bool]:
    """Store the latest autosave snapshot and return ``(stored, lock_acquired)``.

    ``lock_acquired`` is true only when a background flush should be queued.
    The snapshot itself is always overwritten with the newest answer map.
    """
    try:
        client = _cache._client()
        if client is None:
            return False, False

        payload = {
            "session_id": session_id,
            "teacher_id": teacher_id or "",
            "exam_id": exam_id or "",
            "student_id": student_id or "",
            "answers": _normalise_answers(answers),
            "updated_at": _now_iso(),
            "final": bool(final),
        }
        lock_key = _final_flush_key(session_id) if final else _flush_lock_key(session_id)
        pipe = client.pipeline()
        pipe.setex(_snapshot_key(session_id), AUTOSAVE_TTL_SECONDS, json.dumps(payload))
        pipe.set(lock_key, "1", ex=AUTOSAVE_FLUSH_LOCK_SECONDS, nx=True)
        _, acquired = pipe.execute()
        return True, bool(acquired)
    except (redis.ConnectionError, redis.TimeoutError, ConnectionError, OSError):
        return False, False
    except Exception:
        log.warning("Failed to cache autosave snapshot session=%s", session_id, exc_info=True)
        return False, False


def load_autosave_snapshot(session_id: str) -> dict[str, Any] | None:
    """Return the latest Redis snapshot for a session, if present."""
    try:
        snapshot = _cache.get(_snapshot_key(session_id))
    except Exception:
        return None
    if not isinstance(snapshot, dict):
        return None
    answers = snapshot.get("answers")
    if not isinstance(answers, dict):
        return None
    snapshot["answers"] = _normalise_answers(answers)
    return snapshot


def merge_answer_snapshot(
    submitted_answers: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
) -> dict[str, str]:
    """Merge Redis snapshot answers with submitted answers.

    Submitted answers win. Redis only fills gaps when the final POST misses a
    question that was already captured by autosave.
    """
    merged: dict[str, str] = {}
    if snapshot and isinstance(snapshot.get("answers"), dict):
        merged.update(_normalise_answers(snapshot["answers"]))
    merged.update(_normalise_answers(submitted_answers))
    return merged


async def flush_answers_to_db(
    session_id: str,
    answers: dict[str, Any],
    *,
    teacher_id: str | None = None,
    exam_id: str | None = None,
) -> int:
    """Canonicalise and bulk upsert answers to Supabase."""
    normalised = _normalise_answers(answers)
    if not normalised:
        return 0

    records = []
    for qid, answer in normalised.items():
        canonical = await canonicalise_student_answer(
            session_id, str(teacher_id or ""), str(qid), str(answer), exam_id)
        rec = {
            "session_key": session_id,
            "question_id": str(qid),
            "answer": canonical,
        }
        if teacher_id:
            rec["teacher_id"] = teacher_id
        if exam_id:
            rec["exam_id"] = exam_id
        records.append(rec)

    await _atable("answers").upsert(records).execute()
    return len(records)


async def flush_autosave_snapshot(session_id: str, *, delete_after: bool = False) -> dict[str, Any]:
    """Flush the latest Redis snapshot for a session to the database."""
    snapshot = load_autosave_snapshot(session_id)
    if not snapshot:
        return {"ok": True, "flushed": 0, "missing": True}

    flushed = await flush_answers_to_db(
        session_id,
        snapshot.get("answers") or {},
        teacher_id=snapshot.get("teacher_id") or None,
        exam_id=snapshot.get("exam_id") or None,
    )

    try:
        client = _cache._client()
        if client is not None:
            client.delete(_flush_lock_key(session_id), _final_flush_key(session_id))
            if delete_after:
                client.delete(_snapshot_key(session_id))
    except Exception:
        log.debug("Failed to update autosave flush metadata session=%s", session_id, exc_info=True)

    return {"ok": True, "flushed": flushed}
