"""Grading endpoints: pending grades, AI grade suggestions, teacher grade confirmation."""
import json
import logging
import time
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Body, HTTPException
from pydantic import BaseModel, ConfigDict
_grading_log = logging.getLogger("grading")

from ..dependencies import (
    supabase,
    _atable,
    limiter,
    require_admin,
    fmt_ist,
    _load_questions,
    now_ist,
)

router = APIRouter(prefix="")


# ─── PYDANTIC MODELS ──────────────────────────────────

class GradeSuggestIn(BaseModel):
    model_config = ConfigDict(strict=True)
    answer_ids: list[str]


class GradeConfirmIn(BaseModel):
    model_config = ConfigDict(strict=True)
    answer_id: str
    score: float


async def _apply_short_answer_to_session(session_key: str, teacher_id: str) -> dict | None:
    """Recompute exam_sessions.{score,total,percentage} including
    teacher-confirmed short-answer scores.

    Idempotent: reads the canonical state (MCQ correctness from the
    questions/answers tables, plus confirmed teacher_score per short-
    answer response) and rewrites the session row from scratch. Safe to
    call repeatedly — never double-counts.

    Returns the new totals or None if the session wasn't found.
    """
    from ..dependencies import _recalculate_score

    sess = await _atable("exam_sessions")\
        .select("session_key,exam_id,teacher_id")\
        .eq("session_key", session_key)\
        .eq("teacher_id", teacher_id)\
        .limit(1).execute()
    if not sess.data:
        return None
    eid = sess.data[0].get("exam_id")

    try:
        mcq_score, mcq_total = await _recalculate_score(session_key, {}, teacher_id, eid)
    except Exception as e:
        _grading_log.warning("[rollup] mcq recalc failed: %s", e)
        return None

    sa_qs = await _atable("questions")\
        .select("question_id,max_score")\
        .eq("teacher_id", teacher_id)\
        .eq("exam_id", eid)\
        .eq("question_type", "short_answer")\
        .execute()
    sa_max_total = sum(float(q.get("max_score") or 1.0) for q in (sa_qs.data or []))

    sa_ans = await _atable("answers")\
        .select("teacher_score")\
        .eq("session_key", session_key)\
        .eq("teacher_id", teacher_id)\
        .execute()
    sa_score_total = sum(float(a.get("teacher_score") or 0)
                         for a in (sa_ans.data or []) if a.get("teacher_score") is not None)

    new_score = int(round(mcq_score + sa_score_total))
    new_total = int(round(mcq_total + sa_max_total))
    new_pct = round((new_score / max(new_total, 1)) * 100, 1)

    await _atable("exam_sessions").update({
        "score":      new_score,
        "total":      new_total,
        "percentage": new_pct,
    }).eq("session_key", session_key).eq("teacher_id", teacher_id).execute()

    return {"score": new_score, "total": new_total, "percentage": new_pct,
            "mcq_score": mcq_score, "mcq_total": mcq_total,
            "short_answer_score": sa_score_total,
            "short_answer_max": sa_max_total}


@router.get("/api/v1/admin/pending-grades")
async def pending_grades(request: Request):
    """List answers to short-answer questions that haven't been
    teacher-confirmed yet. Optionally filtered by exam_id."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    exam_id = request.query_params.get("exam_id")

    q_query = _atable("questions").select(
        "id,question_id,exam_id,question,reference_answer,rubric,max_score"
    ).eq("teacher_id", tid).eq("question_type", "short_answer")
    if exam_id:
        q_query = q_query.eq("exam_id", exam_id)
    questions = (await q_query.execute()).data or []
    if not questions:
        return {"questions": [], "answers": [], "total_pending": 0}

    qid_to_meta = {str(q["question_id"]): q for q in questions}

    a_query = _atable("answers").select(
        "id,session_key,question_id,answer,ai_score,ai_feedback,ai_confidence,teacher_score,exam_id"
    ).eq("teacher_id", tid).is_("teacher_score", "null")
    if exam_id:
        a_query = a_query.eq("exam_id", exam_id)
    all_answers = (await a_query.execute()).data or []
    pending = [a for a in all_answers if str(a.get("question_id")) in qid_to_meta]

    session_keys = list({a["session_key"] for a in pending if a.get("session_key")})
    roll_map = {}
    if session_keys:
        try:
            sess_rows = (await _atable("exam_sessions")
                .select("session_key,roll_number,full_name")
                .eq("teacher_id", tid)
                .in_("session_key", session_keys).execute()).data or []
            roll_map = {s["session_key"]: s for s in sess_rows}
        except Exception as e:
            _grading_log.warning("[pending-grades] session lookup failed: %s", e)

    enriched = []
    for a in pending:
        meta = qid_to_meta.get(str(a["question_id"]), {})
        sess = roll_map.get(a["session_key"]) or {}
        enriched.append({
            "answer_id":      a["id"],
            "session_key":    a["session_key"],
            "roll_number":    sess.get("roll_number") or "",
            "full_name":      sess.get("full_name") or "",
            "question_id":    a["question_id"],
            "exam_id":        a.get("exam_id") or meta.get("exam_id"),
            "question":       meta.get("question") or "",
            "reference":      meta.get("reference_answer") or "",
            "rubric":         meta.get("rubric") or "",
            "max_score":      float(meta.get("max_score") or 1.0),
            "student_answer": a.get("answer") or "",
            "ai_score":       a.get("ai_score"),
            "ai_feedback":    a.get("ai_feedback"),
            "ai_confidence":  a.get("ai_confidence"),
        })
    return {
        "questions": questions,
        "answers": enriched,
        "total_pending": len(enriched),
    }


@router.post("/api/v1/admin/grade-suggest")
@limiter.limit("20/minute")
async def grade_suggest(request: Request, body: GradeSuggestIn = Body(...)):
    """Run AI grader over a batch of pending short answers. Writes
    suggested scores + feedback to the answers table; teacher_score
    is left NULL (still pending review). Idempotent — re-running on
    the same answers updates the suggestions in place.

    Body: ``{answer_ids: [uuid, uuid, ...]}`` — the dashboard sends
    the IDs returned by /pending-grades. Up to 50 per call to keep
    a single request bounded; dashboard batches if the queue is
    larger.
    """
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    from llm import is_configured, grade_short_answer  # noqa
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI grader unavailable. Set LLM_API_KEY on the server.")

    answer_ids = body.answer_ids
    if not isinstance(answer_ids, list) or not answer_ids:
        raise HTTPException(status_code=400, detail="answer_ids required")
    if len(answer_ids) > 50:
        raise HTTPException(status_code=413, detail="Max 50 per call.")

    answers = (await _atable("answers").select("*")
        .eq("teacher_id", tid).in_("id", answer_ids)
        .execute()).data or []
    if not answers:
        return {"graded": 0, "results": []}
    qids = list({str(a["question_id"]) for a in answers})
    questions = (await _atable("questions").select(
        "question_id,question,reference_answer,rubric,max_score"
    ).eq("teacher_id", tid).in_("question_id", qids).execute()).data or []
    qmap = {str(q["question_id"]): q for q in questions}

    results = []
    for a in answers:
        q = qmap.get(str(a["question_id"]))
        if not q:
            results.append({"answer_id": a["id"], "error": "question not found"})
            continue
        try:
            suggestion = grade_short_answer(
                question=q.get("question") or "",
                reference=q.get("reference_answer") or "",
                rubric=q.get("rubric") or "",
                student_answer=a.get("answer") or "",
                max_score=float(q.get("max_score") or 1.0),
            )
        except Exception as e:
            _grading_log.warning("[grade-suggest] LLM call failed for %s: %s", a['id'], e)
            results.append({"answer_id": a["id"], "error": f"LLM error: {str(e)[:120]}"})
            continue
        try:
            await _atable("answers").update({
                "ai_score":      suggestion.get("score"),
                "ai_feedback":   suggestion.get("feedback"),
                "ai_confidence": suggestion.get("confidence"),
            }).eq("id", a["id"]).eq("teacher_id", tid).execute()
            results.append({"answer_id": a["id"], **suggestion})
        except Exception as e:
            _grading_log.warning("[grade-suggest] DB write failed for %s: %s", a['id'], e)
            results.append({"answer_id": a["id"], "error": str(e)[:120]})
    return {"graded": len(results), "results": results}


@router.post("/api/v1/admin/grade-confirm")
async def grade_confirm(request: Request, body: GradeConfirmIn = Body(...)):
    """Teacher commits a final score for a short-answer response.
    Sets teacher_score (the value used in the gradebook) and
    graded_at (audit timestamp). Score can match the AI suggestion
    or be overridden — both flow through the same endpoint."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    answer_id = body.answer_id
    score = body.score
    if not answer_id or score is None:
        raise HTTPException(status_code=400, detail="answer_id and score required")
    try:
        score = float(score)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="score must be a number")

    own = (await _atable("answers").select("id,question_id,session_key")
        .eq("id", answer_id).eq("teacher_id", tid).limit(1).execute()).data
    if not own:
        raise HTTPException(status_code=404, detail="Answer not found")

    qrow = (await _atable("questions").select("max_score")
        .eq("teacher_id", tid).eq("question_id", own[0]["question_id"])
        .limit(1).execute()).data
    max_score = float((qrow[0] or {}).get("max_score") or 1.0) if qrow else 1.0
    if score < 0 or score > max_score:
        raise HTTPException(status_code=400,
            detail=f"score must be between 0 and {max_score}")

    await _atable("answers").update({
        "teacher_score": score,
        "graded_at": now_ist().isoformat(),
    }).eq("id", answer_id).eq("teacher_id", tid).execute()

    session_key = (own[0] or {}).get("session_key")
    new_totals = None
    if session_key:
        try:
            new_totals = await _apply_short_answer_to_session(session_key, tid)
        except Exception as e:
            _grading_log.warning("[grade-confirm] rollup failed for %s: %s", session_key, e)

    return {"ok": True, "answer_id": answer_id,
            "teacher_score": score,
            "session_totals": new_totals}
