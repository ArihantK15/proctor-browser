"""Data-access layer for questions and exam configuration.

Extracted from app/dependencies.py.
"""

import asyncio
import logging
import os
import secrets
import string

from ..database import async_table as _atable
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Sub-second retry for the request-path questions fetch — a student is
# waiting live for their exam to load, unlike the RQ background-job retry
# policy (10s/60s/300s, see app/jobs/helpers.py) which can afford to wait.
_QUESTIONS_FETCH_RETRIES = 2  # extra attempts beyond the first
_QUESTIONS_FETCH_BACKOFF = (0.3, 0.8)  # seconds, between attempts


class QuestionsFetchError(Exception):
    """Questions could not be loaded from the DB after retries.

    Distinct from a genuinely empty result set (an exam with zero
    questions) — callers that currently do `if not questions: 404` must
    catch this separately, or a transient DB outage looks identical to
    "this exam has no questions" to the student.
    """


async def _select_questions(select_cols: str, teacher_id, exam_id) -> list[dict[str, Any]]:
    """Run one questions SELECT with a short retry for transient DB errors."""
    last_exc: Exception | None = None
    for attempt in range(_QUESTIONS_FETCH_RETRIES + 1):
        try:
            query = _atable("questions").select(select_cols)
            if teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            last_exc = e
            if attempt < _QUESTIONS_FETCH_RETRIES:
                await asyncio.sleep(_QUESTIONS_FETCH_BACKOFF[min(attempt, len(_QUESTIONS_FETCH_BACKOFF) - 1)])
    assert last_exc is not None
    raise last_exc

_EXAM_CONFIG_COLUMNS = (
    "id,exam_id,teacher_id,exam_title,duration_minutes,access_code,"
    "starts_at,ends_at,shuffle_questions,shuffle_options,"
    "phone_camera_enabled,proctoring_sensitivity,"
    "audio_keywords,audio_keywords_language,archived_at,"
    "created_at,pass_mark,early_join_minutes,coding_max_submit_attempts"
)

from typing import Any, cast
_cache: Any = None
try:
    from .. import cache as _cache_src
    _cache = _cache_src
except Exception:
    pass

from ..services import secrets_crypto


def _qid_sort_key(q: dict[str, Any]):
    """Order questions the way the integer question_id column used to.

    phase146 widened questions.question_id integer → text (coding questions
    need string labels). Numeric MCQ ordinals must still sort by VALUE
    (1, 2, …, 10, 11), not lexically (1, 10, 11, 2). Non-numeric coding
    labels sort after all numerics, then lexically, so they append in a
    stable, deterministic order. Replaces the old DB-level
    `.order("question_id")`, which would now collate lexically.
    """
    qid = str(q.get("id", ""))
    return (0, int(qid)) if qid.isdigit() else (1, qid)


async def load_questions(teacher_id: Optional[str] = None, exam_id: Optional[str] = None) -> list[dict[str, Any]]:
    cache_key = f"questions:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cast("list[dict[str, Any]]", cached)
    # Ordering is applied in Python (_qid_sort_key) after fetch — a DB-level
    # ORDER BY question_id now collates the text column lexically (1, 10,
    # 2…), scrambling exams with ≥10 questions.
    try:
        rows = await _select_questions("*", teacher_id, exam_id)
    except Exception as e:
        logger.warning("[Questions] select(*) failed after retries, falling back: %s", e)
        try:
            rows = await _select_questions("question_id,question,options,correct", teacher_id, exam_id)
        except Exception as e2:
            # Both the primary and fallback selects failed after retries —
            # this is a real DB outage, not "this exam has no questions".
            # The old code returned [] here, which looked identical to a
            # genuinely empty exam to every caller: the student saw a
            # misleading "Questions not found" 404, and nobody was alerted.
            logger.error("[Questions] fallback also failed after retries — raising: %s", e2)
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(e2)
            except Exception:
                pass
            raise QuestionsFetchError(
                f"Could not load questions (teacher_id={teacher_id!r}, exam_id={exam_id!r}) after retries"
            ) from e2
    out = []
    for q in rows:
        # Preserve every valid type. A too-narrow allowlist here silently
        # rewrites short_answer/numeric to mcq_single, which (a) breaks student
        # delivery — a numeric/short-answer question reaches the renderer as an
        # optionless MCQ the student can't answer — and (b) defeats the
        # scoring filter that excludes short_answer from auto-grading.
        qtype = (q.get("question_type") or "mcq_single").strip().lower()
        if qtype not in ("mcq_single", "mcq_multi", "true_false", "short_answer", "numeric", "coding"):
            qtype = "mcq_single"
        # `options` lands as a dict on Supabase REST (PostgREST decodes
        # jsonb → object) but as a JSON-encoded string on the plain
        # Postgres backend (questions.options is a TEXT column on the
        # legacy schema and writers explicitly json.dumps before insert).
        # Parse defensively so downstream code always sees a dict.
        raw_options = q.get("options") or {}
        if isinstance(raw_options, str):
            try:
                import json as _json
                raw_options = _json.loads(raw_options)
            except (ValueError, TypeError):
                raw_options = {}
        # `correct` is the secret MCQ answer key — may be a legacy plaintext
        # value or an enc:v1: token (envelope-encrypted at authoring time).
        # decrypt() transparently handles both, so callers (scoring's
        # answers_match, etc.) always see plaintext. `correct` is stripped
        # before any student-facing response (see exam.py safe_questions) —
        # decrypting it here does not create a new leak path.
        raw_correct = str(q.get("correct") or "")
        try:
            decrypted_correct = secrets_crypto.decrypt(raw_correct)
        except Exception:
            logger.warning("[Questions] failed to decrypt 'correct' for question_id=%s",
                            q.get("question_id"))
            decrypted_correct = raw_correct
        out.append({
            "id": str(q["question_id"]),
            "question": q.get("question", "") or "",
            "options": raw_options,
            "correct": decrypted_correct,
            "question_type": qtype,
            "image_url": q.get("image_url") or "",
            # Authoring/grading-only fields (short_answer needs these to round-trip
            # through the bulk save without data loss). NEVER student-facing — the
            # exam client goes through the _STUDENT_Q_KEYS allowlist (exam.py).
            "reference_answer": q.get("reference_answer") or "",
            "max_score": q.get("max_score"),
            "rubric": q.get("rubric") or "",
        })
    # Numeric-faithful ordering (replaces the removed DB-level ORDER BY).
    out.sort(key=_qid_sort_key)
    if _cache and out:
        _cache.set(cache_key, out, ttl=300)
    return out


async def load_exam_config(teacher_id: Optional[str] = None, exam_id: Optional[str] = None) -> dict[str, Any]:
    cache_key = f"exam_config:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cast(dict[str, Any], cached)
    query = _atable("exam_config").select(_EXAM_CONFIG_COLUMNS)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    # With NEITHER filter the query would .limit(1) an arbitrary row and leak
    # another tenant's config — a caller with no identifying key gets defaults.
    result = (await query.limit(1).execute()) if (exam_id or teacher_id) else None
    if result and result.data:
        if _cache:
            # 5m, not 24h: every writer (set_access_code, admin exam edits) does
            # invalidate this key, but a missed invalidation path anywhere means
            # up to a full day of students hitting a stale access code/schedule
            # instead of a five-minute window.
            _cache.set(cache_key, result.data[0], ttl=300)
        return result.data[0]
    return {
        "exam_title": "Exam", "duration_minutes": 60, "access_code": "",
        "starts_at": None, "ends_at": None,
        "shuffle_questions": True, "shuffle_options": True,
        "proctoring_sensitivity": "balanced",
        "phone_camera_enabled": False,
        "audio_keywords": None,
        "audio_keywords_language": "en",
        "archived_at": None,
        "pass_mark": 40,
        "early_join_minutes": 15,
    }


# Access codes are now compulsory for every exam (no more "no code
# required" mode). Built from ascii_uppercase + digits rather than a
# literal string so it doesn't read as one — excludes visually-confusable
# characters (0/O, 1/I/L) since students type this by hand off a
# projector/whiteboard.
_ACCESS_CODE_ALPHABET = "".join(
    c for c in string.ascii_uppercase + string.digits if c not in "IL01O"
)
_ACCESS_CODE_LENGTH = 6


def generate_access_code() -> str:
    return "".join(secrets.choice(_ACCESS_CODE_ALPHABET) for _ in range(_ACCESS_CODE_LENGTH))


async def get_access_code(teacher_id: Optional[str] = None, exam_id: Optional[str] = None) -> str:
    if not teacher_id and not exam_id:
        code = os.getenv("EXAM_ACCESS_CODE", "").strip().upper()
        # No teacher/exam key to persist a generated code against (e.g. the
        # single-tenant env-var fallback) — best-effort only in that mode.
        return code or generate_access_code()
    try:
        config = await load_exam_config(teacher_id, exam_id=exam_id)
        code = str(config.get("access_code") or "").strip().upper()
        if code:
            return code
    except Exception:
        # Do NOT fabricate a code here. A freshly generate_access_code()'d
        # value on every transient DB error would never match the code
        # actually on file — silently 403ing a legitimate student ("Invalid
        # exam access code") on every retry of a real outage, indistinguishable
        # from them having mistyped it. Fail closed and let the caller's
        # generic error handling surface the real problem instead.
        logger.warning("questions: exam-config read failed while resolving access code", exc_info=True)
        raise
    # No code on file for this exam — every exam requires one now, so
    # generate and persist one instead of leaving students permanently
    # unable to start it. Covers both freshly-created exams and legacy
    # ones saved before access codes were mandatory.
    new_code = generate_access_code()
    try:
        await set_access_code(new_code, teacher_id=teacher_id, exam_id=exam_id)
    except Exception:
        # Same reasoning as the read-failure branch above: an unpersisted
        # code returned here would never match what's actually on file.
        # Since _validate_access_code (exam.py) re-calls this function fresh
        # on every single student attempt, silently returning it would mean
        # EVERY validation regenerates its own throwaway code that matches
        # nothing — permanently locking every student out of this exam
        # (worse than one bad request) until the underlying DB issue clears,
        # with no visible error anywhere. Fail closed and let the caller's
        # generic error handling surface the real problem instead.
        logger.warning("questions: failed to persist auto-generated access code", exc_info=True)
        raise
    return new_code


async def set_access_code(code: str, teacher_id: Optional[str] = None, exam_id: Optional[str] = None):
    if teacher_id and exam_id:
        await _atable("exam_config").upsert({"teacher_id": teacher_id, "exam_id": exam_id, "access_code": code}).execute()
    elif teacher_id:
        await _atable("exam_config").upsert({"teacher_id": teacher_id, "access_code": code}).execute()
    else:
        await _atable("exam_config").upsert({"id": 1, "access_code": code}).execute()
    if _cache:
        _cache.delete(f"exam_config:{teacher_id or '_'}:{exam_id or '_'}")
