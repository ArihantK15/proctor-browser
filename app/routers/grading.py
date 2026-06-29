"""Grading endpoints: pending grades, AI grade suggestions, teacher grade confirmation."""
from ..log_safe import safe
import json
import logging
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Body, HTTPException
from pydantic import BaseModel, ConfigDict
_grading_log = logging.getLogger("grading")

from ..database import supabase, async_table as _atable
from ..limiter import limiter
from ..auth import require_admin
from ..utils import fmt_ist, now_ist
from ..repositories.questions import load_questions as _load_questions

router = APIRouter(prefix="")


# ─── PYDANTIC MODELS ──────────────────────────────────

class GradeSuggestIn(BaseModel):
    model_config = ConfigDict(strict=True)
    answer_ids: list[str]


class GradeConfirmIn(BaseModel):
    model_config = ConfigDict(strict=True)
    answer_id: str
    score: float
    idempotency_key: str | None = None


async def _apply_short_answer_to_session(session_key: str, teacher_id: str) -> dict | None:
    """Recompute exam_sessions.{score,total,percentage} including
    teacher-confirmed short-answer scores.

    Idempotent: reads the canonical state (MCQ correctness from the
    questions/answers tables, plus confirmed teacher_score per short-
    answer response) and rewrites the session row from scratch. Safe to
    call repeatedly — never double-counts.

    Returns the new totals or None if the session wasn't found.
    """
    from ..services.scoring import recalculate_score as _recalculate_score

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
@limiter.limit("30/minute")
async def pending_grades(request: Request):
    """List answers to short-answer questions that haven't been
    teacher-confirmed yet. Optionally filtered by exam_id."""
    from ..auth.scope import resolve_scope, scope_to_teacher_ids, apply_teacher_scope
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    exam_id = request.query_params.get("exam_id")

    q_query = apply_teacher_scope(_atable("questions").select(
        "id,question_id,exam_id,question,reference_answer,rubric,max_score"
    ), tids).eq("question_type", "short_answer")
    if exam_id:
        q_query = q_query.eq("exam_id", exam_id)
    questions = (await q_query.execute()).data or []
    if not questions:
        return {"questions": [], "answers": [], "total_pending": 0}

    qid_to_meta = {str(q["question_id"]): q for q in questions}

    a_query = apply_teacher_scope(_atable("answers").select(
        "id,session_key,question_id,answer,ai_score,ai_feedback,ai_confidence,teacher_score,exam_id"
    ), tids).is_("teacher_score", "null")
    if exam_id:
        a_query = a_query.eq("exam_id", exam_id)
    all_answers = (await a_query.execute()).data or []
    pending = [a for a in all_answers if str(a.get("question_id")) in qid_to_meta]

    session_keys = list({a["session_key"] for a in pending if a.get("session_key")})
    roll_map = {}
    if session_keys:
        try:
            sess_rows = (await apply_teacher_scope(_atable("exam_sessions")
                .select("session_key,roll_number,full_name"), tids)
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
    from ..llm import is_configured, grade_short_answer  # noqa
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

    # Parallelise the per-answer LLM calls. Serially this was 50 *
    # ~500 ms = ~25 s wall-clock for a full batch; bounded-concurrency
    # asyncio.gather brings that to ~2-4 s. Concurrency cap of 8 keeps
    # us inside Groq's rate budget (free tier = 30 req/min; at 8
    # in-flight a 50-answer batch stays under the quota for a single
    # teacher action).
    import asyncio
    _llm_sem = asyncio.Semaphore(8)

    async def _grade_one(a):
        q = qmap.get(str(a["question_id"]))
        if not q:
            return {"answer_id": a["id"], "error": "question not found"}
        async with _llm_sem:
            try:
                suggestion = await grade_short_answer(
                    question=q.get("question") or "",
                    reference=q.get("reference_answer") or "",
                    rubric=q.get("rubric") or "",
                    student_answer=a.get("answer") or "",
                    max_score=float(q.get("max_score") or 1.0),
                )
            except Exception as e:
                _grading_log.warning("[grade-suggest] LLM call failed for %s: %s", a['id'], e)
                return {"answer_id": a["id"], "error": f"LLM error: {str(e)[:120]}"}
        return {"answer_id": a["id"], **suggestion}

    # gather preserves input order, so the upsert below + the UI's
    # answer-id -> row mapping continue to work unchanged.
    results = await asyncio.gather(*(_grade_one(a) for a in answers))
    results = list(results)

    # Per-row updates: a single failing row no longer rolls back the rest
    # (previous bulk upsert leaked partial state on error).
    if results:
        updates = []
        for r in results:
            if "error" in r:
                continue
            updates.append({
                "id": r["answer_id"],
                "ai_score": r.get("score"),
                "ai_feedback": r.get("feedback", ""),
                "ai_confidence": r.get("confidence", "medium"),
            })
        if updates:
            for r in updates:
                try:
                    await _atable("answers").update({
                        "ai_score": r.get("ai_score"),
                        "ai_feedback": r.get("ai_feedback", ""),
                        "ai_confidence": r.get("ai_confidence", "medium"),
                    }).eq("id", r["id"]).eq("teacher_id", tid).execute()
                except Exception as e:
                    _grading_log.warning("[grade-suggest] update failed for %s: %s", r["id"], e)

    return {"graded": len(results), "results": results}


@router.post("/api/v1/admin/grade-confirm")
@limiter.limit("30/minute")
async def grade_confirm(request: Request, body: GradeConfirmIn = Body(...)):
    """Teacher commits a final score for a short-answer response.
    Sets teacher_score (the value used in the gradebook) and
    graded_at (audit timestamp). Score can match the AI suggestion
    or be overridden — both flow through the same endpoint."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Idempotency: atomically RESERVE the key so concurrent double-submits can't
    # both process (the old check-then-mark was a TOCTOU race — both saw "unseen"
    # and both ran). On success we mark the response; on ANY failure the finally
    # releases the reservation so a legitimate retry isn't blocked for the TTL.
    _idem_k = None
    if body.idempotency_key:
        from ..services.idempotency import (reserve_idempotency, mark_idempotent,
                                            release_idempotency, idempotency_key as _idk)
        _idem_k = _idk("grade-confirm", tid, body.idempotency_key)
        _acquired, _cached = await reserve_idempotency(_idem_k)
        if _cached is not None:
            return _cached
        if not _acquired:
            raise HTTPException(status_code=409,
                                detail="A duplicate request is already being processed.")

    _committed = False
    try:
        answer_id = body.answer_id
        score = body.score
        if not answer_id or score is None:
            raise HTTPException(status_code=400, detail="answer_id and score required")
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="score must be a number")

        own = (await _atable("answers").select("id,question_id,session_key,ai_score,ai_confidence")
            .eq("id", answer_id).eq("teacher_id", tid).limit(1).execute()).data
        if not own:
            raise HTTPException(status_code=404, detail="Answer not found")

        qrow = (await _atable("questions").select("max_score,exam_id,question_id")
            .eq("teacher_id", tid).eq("question_id", own[0]["question_id"])
            .limit(1).execute()).data
        max_score = float((qrow[0] or {}).get("max_score") or 1.0) if qrow else 1.0
        exam_id = (qrow[0] or {}).get("exam_id") if qrow else None
        if score < 0 or score > max_score:
            raise HTTPException(status_code=400,
                detail=f"score must be between 0 and {max_score}")

        await _atable("answers").update({
            "teacher_score": score,
            "graded_at": now_ist().isoformat(),
        }).eq("id", answer_id).eq("teacher_id", tid).execute()

        # Record audit trail
        a = own[0]
        ai_s = a.get("ai_score")
        action = "overridden" if (ai_s is not None and float(ai_s) != score) else "confirmed"
        try:
            tname = teacher.get("full_name") or teacher.get("email") or tid
            await _atable("grading_audit").insert({
                "teacher_id": tid, "teacher_name": tname,
                "exam_id": exam_id, "session_key": a.get("session_key"),
                "answer_id": answer_id, "question_id": a.get("question_id"),
                "ai_score": ai_s, "ai_confidence": a.get("ai_confidence"),
                "teacher_score": score, "max_score": max_score,
                "action": action,
            }).execute()
        except Exception as e:
            _grading_log.warning("[grade-confirm] audit insert failed: %s", e)

        session_key = (own[0] or {}).get("session_key")
        new_totals = None
        if session_key:
            try:
                new_totals = await _apply_short_answer_to_session(session_key, tid)
            except Exception as e:
                _grading_log.warning("[grade-confirm] rollup failed for %s: %s", session_key, e)

        resp = {"ok": True, "answer_id": answer_id,
                "teacher_score": score,
                "session_totals": new_totals}

        if _idem_k:
            await mark_idempotent(_idem_k, resp)
        _committed = True
        return resp
    finally:
        if _idem_k and not _committed:
            await release_idempotency(_idem_k)


@router.post("/api/v1/admin/grade-confirm-bulk")
@limiter.limit("10/minute")
async def grade_confirm_bulk(body: dict, request: Request):
    """Bulk confirm (accept) all pending grades for an exam, or reject all.

    Body::
        {"exam_id": "uuid", "action": "accept", "confidence_filter": "high"}
        {"exam_id": "uuid", "action": "reject"}

    - ``accept``: set teacher_score = ai_score for all pending answers
    - ``reject``: set teacher_score = 0 for all pending answers
    - ``confidence_filter``: optional, limits to answers with matching ai_confidence
    """
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Idempotency: atomically RESERVE (TOCTOU-safe) instead of check-then-mark,
    # so concurrent double-submits can't both run. A short reservation TTL covers
    # the processing window; mark_idempotent then stores the response with the
    # full TTL on success. The body's heavy ops (batch upsert, audit, rollup)
    # each swallow their own failures (warn + continue) rather than raising, so
    # an explicit release isn't needed — the short lock simply expires.
    idem_key_raw = (body.get("idempotency_key") or "").strip()
    _bulk_k = None
    if idem_key_raw:
        from ..services.idempotency import (reserve_idempotency, mark_idempotent,
                                            idempotency_key as _idk)
        _bulk_k = _idk("grade-confirm-bulk", tid, idem_key_raw)
        _acquired, _cached = await reserve_idempotency(_bulk_k, ttl=120)
        if _cached is not None:
            return _cached
        if not _acquired:
            raise HTTPException(status_code=409,
                                detail="A duplicate bulk request is already being processed.")

    exam_id = (body.get("exam_id") or "").strip()
    action = (body.get("action") or "").strip().lower()
    confidence_filter = (body.get("confidence_filter") or "").strip().lower()

    if not exam_id:
        raise HTTPException(status_code=400, detail="exam_id required")
    if action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'accept' or 'reject'")

    score_val = 0 if action == "reject" else None  # None means use ai_score

    # Fetch pending answers with ai_confidence
    a_query = _atable("answers").select(
        "id,session_key,question_id,ai_score,ai_confidence"
    ).eq("teacher_id", tid).eq("exam_id", exam_id).is_("teacher_score", "null")
    if confidence_filter:
        a_query = a_query.eq("ai_confidence", confidence_filter)
    pending = (await a_query.execute()).data or []

    if not pending:
        return {"action": action, "confirmed": 0, "skipped": 0}

    # Fetch question metadata for audit
    qids = list({a.get("question_id") for a in pending if a.get("question_id")})
    qmap = {}
    if qids:
        qrows = (await _atable("questions").select("question_id,max_score,exam_id")
                 .eq("teacher_id", tid).in_("question_id", qids).execute()).data or []
        qmap = {str(q["question_id"]): q for q in qrows}

    audit_rows = []
    session_keys = set()
    confirmed = 0
    skipped = 0
    answer_updates = []
    tname = teacher.get("full_name") or teacher.get("email") or tid
    bulk_action = "bulk_accept" if action == "accept" else "bulk_reject"
    graded_at = now_ist().isoformat()
    for a in pending:
        a_id = a.get("id")
        s_key = a.get("session_key")
        ai_score = a.get("ai_score")
        final_score = score_val if score_val is not None else ai_score
        if final_score is None:
            skipped += 1
            continue
        answer_updates.append({
            "id": a_id,
            "teacher_score": final_score,
            "graded_at": graded_at,
            "teacher_id": tid,
            "exam_id": exam_id,
        })
        confirmed += 1
        if s_key:
            session_keys.add(s_key)
        qm = qmap.get(str(a.get("question_id")), {})
        audit_rows.append({
            "teacher_id": tid, "teacher_name": tname,
            "exam_id": exam_id, "session_key": s_key,
            "answer_id": a_id, "question_id": a.get("question_id"),
            "ai_score": ai_score, "ai_confidence": a.get("ai_confidence"),
            "teacher_score": final_score,
            "max_score": float(qm.get("max_score") or 1.0),
            "action": bulk_action,
        })

    # Batch update answers (one call instead of N)
    if answer_updates:
        try:
            await _atable("answers").upsert(
                answer_updates, on_conflict="id"
            ).execute()
        except Exception as e:
            _grading_log.warning("[grade-confirm-bulk] batch answer update failed: %s", e)
            # Fall back to individual updates — unlikely to ever be reached
            for a in answer_updates:
                try:
                    await _atable("answers").update({
                        "teacher_score": a["teacher_score"],
                        "graded_at": a["graded_at"],
                    }).eq("id", a["id"]).eq("teacher_id", tid).execute()
                except Exception as e2:
                    _grading_log.warning("[grade-confirm-bulk] fallback update failed for %s: %s", safe(a["id"]), safe(e2))

    # Batch insert audit rows (one call instead of N)
    if audit_rows:
        try:
            await _atable("grading_audit").upsert(audit_rows).execute()
        except Exception as e:
            _grading_log.warning("[grade-confirm-bulk] audit batch insert failed: %s", e)
            for r in audit_rows:
                try:
                    await _atable("grading_audit").insert(r).execute()
                except Exception as e2:
                    _grading_log.warning("[grade-confirm-bulk] audit insert fallback failed: %s", e2)

    # Recompute session scores for affected sessions
    recompiled = 0
    for sk in session_keys:
        try:
            await _apply_short_answer_to_session(sk, tid)
            recompiled += 1
        except Exception as e:
            _grading_log.warning("[grade-confirm-bulk] rollup failed for %s: %s", sk, e)

    _grading_log.info("[audit] teacher=%s bulk %s exam=%s confirmed=%d skipped=%d sessions=%d",
                      safe(tid), safe(action), safe(exam_id), confirmed, skipped, recompiled)

    resp = {
        "action": action,
        "confirmed": confirmed,
        "skipped": skipped,
        "sessions_recompiled": recompiled,
        "total_pending": len(pending),
    }

    if idem_key_raw and tid:
        try:
            await mark_idempotent(_bulk_k or "", resp)
        except Exception:
            _grading_log.debug("grading: bulk idempotency mark failed", exc_info=True)

    return resp


@router.get("/api/v1/admin/grading-audit")
@limiter.limit("30/minute")
async def grading_audit(request: Request):
    """Return the grading audit trail for the caller's scope, most recent first
    (own for a teacher, org-wide for an admin)."""
    from ..auth.scope import resolve_scope, scope_to_teacher_ids, apply_teacher_scope
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    exam_id = request.query_params.get("exam_id")
    limit = min(int(request.query_params.get("limit", "100")), 500)

    q = apply_teacher_scope(_atable("grading_audit").select("*"), tids)
    if exam_id:
        q = q.eq("exam_id", exam_id)
    rows = await q.order("created_at", desc=True).limit(limit).execute()

    # Summary stats
    stats_q = apply_teacher_scope(_atable("grading_audit").select("*"), tids)
    if exam_id:
        stats_q = stats_q.eq("exam_id", exam_id)
    all_rows = (await stats_q.execute()).data or []
    total = len(all_rows)
    accepted = sum(1 for r in all_rows if r.get("action") in ("confirmed", "bulk_accept"))
    overridden = sum(1 for r in all_rows if r.get("action") == "overridden")
    rejected = sum(1 for r in all_rows if r.get("action") == "bulk_reject")

    return {
        "events": rows.data or [],
        "stats": {
            "total": total,
            "accepted": accepted,
            "overridden": overridden,
            "rejected": rejected,
            "ai_accept_rate": round((accepted / max(total, 1)) * 100, 1),
        },
    }
