"""Data-access layer for questions and exam configuration.

Extracted from app/dependencies.py.
"""

import logging
import os

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

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
        query = _atable("questions").select("question_id,question,options,correct")
        if teacher_id:
            query = query.eq("teacher_id", teacher_id)
        if exam_id:
            query = query.eq("exam_id", exam_id)
        rows = (await query.order("question_id").execute()).data or []
    out = []
    for q in rows:
        qtype = (q.get("question_type") or "mcq_single").strip().lower()
        if qtype not in ("mcq_single", "mcq_multi", "true_false"):
            qtype = "mcq_single"
        out.append({
            "id": str(q["question_id"]),
            "question": q.get("question", "") or "",
            "options": q.get("options") or {},
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
    query = _atable("exam_config").select("*")
    if exam_id:
        query = query.eq("exam_id", exam_id)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    result = await query.execute()
    if result.data:
        if _cache:
            _cache.set(cache_key, result.data[0], ttl=86400)  # 24h — invalidation keeps it fresh
        return result.data[0]
    return {
        "exam_title": "Exam", "duration_minutes": 60, "access_code": "",
        "starts_at": None, "ends_at": None,
        "shuffle_questions": True, "shuffle_options": True,
    }


async def get_access_code(teacher_id: str = None, exam_id: str = None) -> str:
    try:
        config = await load_exam_config(teacher_id, exam_id=exam_id)
        code = config.get("access_code", "")
        if code:
            return str(code).strip().upper()
    except Exception:
        pass
    return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()


async def set_access_code(code: str, teacher_id: str = None, exam_id: str = None):
    if teacher_id and exam_id:
        await _atable("exam_config").update({"access_code": code}).eq("teacher_id", teacher_id).eq("exam_id", exam_id).execute()
    elif teacher_id:
        await _atable("exam_config").upsert({"teacher_id": teacher_id, "access_code": code}).execute()
    else:
        await _atable("exam_config").upsert({"id": 1, "access_code": code}).execute()
