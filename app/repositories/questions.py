"""Data-access layer for questions and exam configuration.

Extracted from app/dependencies.py.
"""

import logging
import os

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

_EXAM_CONFIG_COLUMNS = (
    "id,exam_id,teacher_id,exam_title,duration_minutes,access_code,"
    "starts_at,ends_at,shuffle_questions,shuffle_options,"
    "phone_camera_enabled,proctoring_sensitivity,"
    "audio_keywords,audio_keywords_language,archived_at,"
    "created_at,pass_mark"
)

try:
    from .. import cache as _cache
except Exception:
    _cache = None


async def load_questions(teacher_id: str = None, exam_id: str = None) -> list[dict]:
    cache_key = f"questions:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
    try:
        query = _atable("questions").select("*")
        if teacher_id:
            query = query.eq("teacher_id", teacher_id)
        if exam_id:
            query = query.eq("exam_id", exam_id)
        result = await query.order("question_id").execute()
        rows = result.data or []
    except Exception as e:
        logger.warning("[Questions] select(*) failed, falling back: %s", e)
        try:
            query = _atable("questions").select("question_id,question,options,correct")
            if teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
            rows = (await query.order("question_id").execute()).data or []
        except Exception as e2:
            logger.warning("[Questions] fallback also failed: %s", e2)
            rows = []
    out = []
    for q in rows:
        # Preserve every valid type. A too-narrow allowlist here silently
        # rewrites short_answer/numeric to mcq_single, which (a) breaks student
        # delivery — a numeric/short-answer question reaches the renderer as an
        # optionless MCQ the student can't answer — and (b) defeats the
        # scoring filter that excludes short_answer from auto-grading.
        qtype = (q.get("question_type") or "mcq_single").strip().lower()
        if qtype not in ("mcq_single", "mcq_multi", "true_false", "short_answer", "numeric"):
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
        out.append({
            "id": str(q["question_id"]),
            "question": q.get("question", "") or "",
            "options": raw_options,
            "correct": str(q.get("correct") or ""),
            "question_type": qtype,
            "image_url": q.get("image_url") or "",
        })
    if _cache and out:
        _cache.set(cache_key, out, ttl=300)
    return out


async def load_exam_config(teacher_id: str = None, exam_id: str = None) -> dict:
    cache_key = f"exam_config:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
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
            _cache.set(cache_key, result.data[0], ttl=86400)  # 24h — invalidation keeps it fresh
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
    }


async def get_access_code(teacher_id: str = None, exam_id: str = None) -> str:
    if not teacher_id and not exam_id:
        return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()
    try:
        config = await load_exam_config(teacher_id, exam_id=exam_id)
        code = config.get("access_code", "")
        if code:
            return str(code).strip().upper()
    except Exception:
        logger.debug("questions: exam-config access-code fallback failed", exc_info=True)
    return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()


async def set_access_code(code: str, teacher_id: str = None, exam_id: str = None):
    if teacher_id and exam_id:
        await _atable("exam_config").upsert({"teacher_id": teacher_id, "exam_id": exam_id, "access_code": code}).execute()
    elif teacher_id:
        await _atable("exam_config").upsert({"teacher_id": teacher_id, "access_code": code}).execute()
    else:
        await _atable("exam_config").upsert({"id": 1, "access_code": code}).execute()
    if _cache:
        _cache.delete(f"exam_config:{teacher_id or '_'}:{exam_id or '_'}")
