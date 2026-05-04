"""Question bank CRUD and AI features router."""

import json
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import Request, HTTPException, Body
from fastapi.routing import APIRouter

from ..dependencies import supabase, limiter, require_admin, _cache, SessionStatus
from ..dependencies import _load_questions, _load_exam_config

router = APIRouter(prefix="")

# ─── QUESTION BANK ─────────────────────────────────────────────────

@router.get("/api/v1/admin/question-bank")
def list_bank_questions(request: Request):
    """List all question bank entries for the teacher, optionally filtered by tag."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    tag = request.query_params.get("tag")
    q = (supabase.table("question_bank").select("*")
         .eq("teacher_id", tid)
         .order("created_at", desc=True)
         .limit(5000))
    rows = (q.execute()).data or []
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    return rows


@router.post("/api/v1/admin/question-bank")
def add_bank_questions(request: Request, body: dict = Body(...)):
    """Add one or more questions to the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    questions = body.get("questions", [body] if "question" in body else [])
    if not questions:
        raise HTTPException(status_code=400, detail="No questions provided")
    rows = []
    for q in questions:
        rows.append({
            "teacher_id": tid,
            "question": q.get("question", ""),
            "question_type": q.get("question_type", "mcq_single"),
            "options": q.get("options", {}),
            "correct": str(q.get("correct", "")),
            "image_url": q.get("image_url", ""),
            "tags": q.get("tags", []),
        })
    result = supabase.table("question_bank").insert(rows).execute()
    return result.data or []


@router.put("/api/v1/admin/question-bank/{qid}")
def update_bank_question(qid: str, request: Request, body: dict = Body(...)):
    """Update a question in the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    fields = {}
    for k in ("question", "question_type", "options", "correct", "image_url", "tags"):
        if k in body:
            fields[k] = body[k]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (supabase.table("question_bank")
              .update(fields).eq("id", qid).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Question not found")
    return result.data[0]


@router.delete("/api/v1/admin/question-bank/{qid}")
def delete_bank_question(qid: str, request: Request):
    """Delete a question from the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    supabase.table("question_bank").delete().eq("id", qid).eq("teacher_id", tid).execute()
    return {"ok": True}


@router.post("/api/v1/admin/question-bank/import")
def import_bank_questions(request: Request, body: dict = Body(...)):
    """Bulk import questions from CSV-style JSON array.

    Expected format: list of objects with keys:
    question, type, option_A, option_B, option_C, option_D, correct, image_url, tags
    """
    teacher = require_admin(request)
    tid = str(teacher["id"])
    items = body.get("questions", [])
    if not items:
        raise HTTPException(status_code=400, detail="No questions to import")
    if len(items) > 2000:
        raise HTTPException(status_code=413,
            detail=f"Too many questions ({len(items)}). Max 2000 per import — split into smaller files.")
    rows = []
    for item in items:
        options = {}
        for letter in ("A", "B", "C", "D", "E", "F"):
            val = item.get(f"option_{letter}")
            if val is not None:
                options[letter] = val
        rows.append({
            "teacher_id": tid,
            "question": item.get("question", ""),
            "question_type": item.get("type", item.get("question_type", "mcq_single")),
            "options": options,
            "correct": str(item.get("correct", "")),
            "image_url": item.get("image_url", ""),
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list)
                    else [t.strip() for t in str(item.get("tags", "")).split(",") if t.strip()],
        })
    result = supabase.table("question_bank").insert(rows).execute()
    inserted = result.data or []
    return {
        "imported": len(inserted),
        "inserted_ids": [r.get("id") for r in inserted if r.get("id")],
    }


@router.get("/api/v1/admin/question-bank/export")
def export_bank_questions(request: Request):
    """Export all bank questions as JSON."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    rows = (supabase.table("question_bank").select("*")
            .eq("teacher_id", tid)
            .order("created_at", desc=True)
            .limit(5000).execute()).data or []
    export = []
    for r in rows:
        entry = {
            "question": r["question"],
            "type": r["question_type"],
            "correct": r["correct"],
            "image_url": r.get("image_url", ""),
            "tags": ",".join(r.get("tags") or []),
        }
        opts = r.get("options") or {}
        for letter in ("A", "B", "C", "D", "E", "F"):
            if letter in opts:
                entry[f"option_{letter}"] = opts[letter]
        export.append(entry)
    return export


@router.post("/api/v1/admin/question-bank/generate")
@limiter.limit("20/minute")
def generate_bank_questions(request: Request, body: dict = Body(...)):
    """Generate question-bank rows from a topic / source text via LLM.

    Returns a *preview* — the teacher reviews and explicitly clicks
    'Add to Bank' to actually persist.
    """
    teacher = require_admin(request)
    _ = str(teacher["id"])

    from llm import is_configured, generate_questions
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set GROQ_API_KEY on the server.")

    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    if len(topic) > 500:
        raise HTTPException(status_code=400, detail="topic too long (max 500 chars)")

    count = body.get("count", 10)
    difficulty = (body.get("difficulty") or "mixed").strip().lower()
    qtype = (body.get("question_type") or "mcq_single").strip()
    grade_level = (body.get("grade_level") or "").strip() or None
    source_text = body.get("source_text") or None
    if source_text and len(source_text) > 20000:
        raise HTTPException(status_code=400,
            detail="source_text too long (max 20000 chars)")

    try:
        questions = generate_questions(
            topic=topic,
            count=count,
            difficulty=difficulty,
            question_type=qtype,
            source_text=source_text,
            grade_level=grade_level,
        )
    except httpx.HTTPStatusError as e:
        print(f"[llm] groq error: {e}", flush=True)
        raise HTTPException(status_code=502, detail="AI provider error. Try again.")
    except Exception as e:
        print(f"[llm] generate failed: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    if not questions:
        raise HTTPException(status_code=502,
            detail="AI returned no usable questions. Try a more specific topic.")
    return {"questions": questions, "count": len(questions)}


@router.post("/api/v1/admin/question-bank/suggest-tags")
@limiter.limit("60/minute")
def suggest_question_tags(request: Request, body: dict = Body(...)):
    """Suggest 3-5 tags for a single question."""
    require_admin(request)
    from llm import is_configured, suggest_tags
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set GROQ_API_KEY on the server.")
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    options = body.get("options") or {}
    correct = body.get("correct") or ""
    try:
        tags = suggest_tags(question[:2000], options, str(correct)[:50])
    except Exception as e:
        print(f"[llm] suggest_tags failed: {e}", flush=True)
        raise HTTPException(status_code=502, detail="AI provider error.")
    return {"tags": tags}


@router.post("/api/v1/admin/lint-questions")
@limiter.limit("10/minute")
def lint_questions_endpoint(request: Request, body: dict = Body(...)):
    """Pre-publish AI review of an exam's questions."""
    require_admin(request)
    from llm import is_configured, lint_questions
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set LLM_API_KEY on the server.")

    questions = body.get("questions") or []
    if not isinstance(questions, list) or not questions:
        raise HTTPException(status_code=400, detail="questions array required")
    if len(questions) > 200:
        raise HTTPException(status_code=413,
            detail="Too many questions for one lint pass. Max 200.")

    cleaned = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        cleaned.append({
            "idx": q.get("idx", i),
            "question": str(q.get("question") or "")[:1500],
            "options": q.get("options") or {},
            "correct": str(q.get("correct") or "")[:50],
        })

    all_results = []
    BATCH = 25
    try:
        for i in range(0, len(cleaned), BATCH):
            chunk = cleaned[i:i + BATCH]
            chunk_results = lint_questions(chunk)
            if not chunk_results:
                for q in chunk:
                    all_results.append({"idx": q["idx"], "issues": [],
                                        "lint_failed": True})
            else:
                all_results.extend(chunk_results)
    except Exception as e:
        print(f"[llm] lint_questions failed: {e}", flush=True)
        raise HTTPException(status_code=502, detail=f"AI provider error: {e}")

    total_issues = sum(len(r.get("issues", [])) for r in all_results)
    return {"results": all_results, "total_issues": total_issues}


@router.post("/api/v1/admin/question-bank/to-exam")
def bank_to_exam(request: Request, body: dict = Body(...)):
    """Copy bank questions into an exam's question list."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    question_ids = body.get("question_ids", [])
    exam_id = body.get("exam_id")
    if not question_ids or not exam_id:
        raise HTTPException(status_code=400, detail="question_ids and exam_id required")
    if len(question_ids) > 500:
        raise HTTPException(status_code=413,
            detail="Too many questions. Max 500 per copy.")

    try:
        own_via_config = (supabase.table("exam_config")
                          .select("exam_id").eq("teacher_id", tid)
                          .eq("exam_id", exam_id).limit(1).execute()).data
        if not own_via_config:
            own_via_questions = (supabase.table("questions")
                                 .select("exam_id").eq("teacher_id", tid)
                                 .eq("exam_id", exam_id).limit(1).execute()).data
            if not own_via_questions:
                pass

        bank_rows = (supabase.table("question_bank").select("*")
                     .eq("teacher_id", tid).in_("id", question_ids).execute()).data or []
        if not bank_rows:
            raise HTTPException(status_code=404, detail="No matching bank questions found")

        existing = _load_questions(teacher_id=tid, exam_id=exam_id)
        max_id = max((int(q.get("question_id", q.get("id", 0))) for q in existing), default=0)

        new_rows = []
        bad = []
        for i, bq in enumerate(bank_rows, start=max_id + 1):
            q_text = (bq.get("question") or "").strip()
            correct = (bq.get("correct") or "").strip()
            opts = bq.get("options") or {}
            if not q_text or not correct or not opts:
                bad.append({"id": bq.get("id"), "reason":
                    f"missing fields: question={'OK' if q_text else 'MISSING'}, "
                    f"correct={'OK' if correct else 'MISSING'}, "
                    f"options={'OK' if opts else 'MISSING'}"})
                continue
            new_rows.append({
                "teacher_id": tid,
                "exam_id": exam_id,
                "question_id": i,
                "question": q_text,
                "question_type": bq.get("question_type", "mcq_single"),
                "options": opts,
                "correct": correct,
                "image_url": bq.get("image_url") or "",
            })
        if bad and not new_rows:
            raise HTTPException(status_code=422, detail={
                "message": "All selected bank rows are missing required fields. "
                           "Edit them in the bank list (pencil icon) before adding.",
                "rows": bad,
            })
        if new_rows:
            optional_cols = {"image_url", "question_type", "tags"}
            attempted_drops = []
            for _attempt in range(len(optional_cols) + 1):
                try:
                    supabase.table("questions").insert(new_rows).execute()
                    if attempted_drops:
                        print(f"[bank-to-exam] succeeded after dropping "
                              f"{attempted_drops} due to schema mismatch.",
                              flush=True)
                    break
                except Exception as ie:
                    msg = str(ie)
                    m = re.search(r"Could not find the '([^']+)' column", msg, re.IGNORECASE)
                    if not m:
                        raise
                    missing_col = m.group(1)
                    if missing_col not in optional_cols:
                        raise
                    print(f"[bank-to-exam] column '{missing_col}' "
                          f"missing — dropping + retrying", flush=True)
                    for row in new_rows:
                        row.pop(missing_col, None)
                    attempted_drops.append(missing_col)
            else:
                raise RuntimeError(
                    f"questions table missing all of: {attempted_drops}. "
                    f"Run migrations/phase11_questions_full_schema.sql.")
            if _cache:
                _cache.delete(f"questions:{tid}:{exam_id or '_'}")
        return {
            "added": len(new_rows),
            "starting_id": max_id + 1,
            "skipped": len(bad),
            "skipped_rows": bad[:10],
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[bank-to-exam][ERROR] tid={tid} exam={exam_id} "
              f"qcount={len(question_ids)} err={type(e).__name__}: {e}",
              flush=True)
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=502,
            detail=f"Couldn't copy questions: {type(e).__name__}: {e}")


@router.get("/api/v1/admin/questions")
def get_admin_questions(request: Request):
    """Return all questions including correct answers (admin only)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    exam_id = request.query_params.get("exam_id")
    try:
        config = _load_exam_config(str(tid) if tid else None, exam_id=exam_id)
        questions = _load_questions(str(tid) if tid else None, exam_id=exam_id)
        return {
            "exam_title": config.get("exam_title", "Exam"),
            "duration_minutes": config.get("duration_minutes", 60),
            "questions": questions,
        }
    except Exception as e:
        print(f"[Questions] ERROR: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/answers/{session_id:path}")
def get_admin_answers(session_id: str, request: Request):
    """Return student answers merged with correct answers for the detail modal."""
    teacher = require_admin(request)
    tid = teacher["id"]

    def _assert_session_owned(session_id: str, teacher_id: str) -> dict:
        if not teacher_id:
            raise HTTPException(status_code=403, detail="Teacher context missing")
        tid_str = str(teacher_id)
        result = supabase.table("exam_sessions").select("*").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()
        if result.data:
            return result.data[0]
        bare = supabase.table("exam_sessions").select("*").eq("session_key", session_id).limit(1).execute()
        if bare.data:
            row = bare.data[0]
            row_tid = row.get("teacher_id")
            if row_tid in (None, ""):
                v_other = supabase.table("violations").select("teacher_id").eq("session_key", session_id).neq("teacher_id", tid_str).limit(1).execute()
                if not (v_other.data or []):
                    return row
            raise HTTPException(status_code=404, detail="Session not found")
        v_mine = supabase.table("violations").select("session_key,teacher_id").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()
        if v_mine.data:
            return {"session_key": session_id, "teacher_id": tid_str,
                    "roll_number": (session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]),
                    "full_name": "", "status": SessionStatus.IN_PROGRESS, "started_at": "", "submitted_at": "",
                    "score": None, "total": None, "risk_score": None}
        raise HTTPException(status_code=404, detail="Session not found")

    _assert_session_owned(session_id, tid)

    questions = _load_questions(tid)
    ans_result = supabase.table("answers").select("question_id,answer")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .execute()
    ans_map = {str(r["question_id"]): str(r["answer"]) for r in (ans_result.data or [])}

    def _answers_match(student_ans: str, correct_ans: str) -> bool:
        def _normalise_answer_set(ans: str) -> set:
            if ans is None:
                return set()
            return {s.strip().upper() for s in str(ans).split(",") if s.strip()}
        return _normalise_answer_set(student_ans) == _normalise_answer_set(correct_ans)

    answer_review = []
    for q in questions:
        qid = q["id"]
        student_ans = ans_map.get(qid, "")
        correct_ans = q["correct"]
        answer_review.append({
            "question_id":   qid,
            "question":      q.get("question", ""),
            "options":       q.get("options", {}),
            "question_type": q.get("question_type", "mcq_single"),
            "image_url":     q.get("image_url", ""),
            "student_answer": student_ans,
            "correct_answer": correct_ans,
            "is_correct":     _answers_match(student_ans, correct_ans),
        })

    return {"answers": answer_review, "total": len(questions),
            "correct_count": sum(1 for a in answer_review if a["is_correct"])}


@router.post("/api/v1/admin/questions")
def update_questions(request: Request, body: dict = Body(...)):
    """Update questions in Supabase."""
    teacher = require_admin(request)
    tid = teacher["id"]
    if "questions" not in body:
        raise HTTPException(status_code=400, detail="Missing 'questions' key")
    questions = body["questions"]
    if not isinstance(questions, list) or len(questions) == 0:
        raise HTTPException(status_code=400, detail="'questions' must be a non-empty list")

    ALLOWED_TYPES = {"mcq_single", "mcq_multi", "true_false", "short_answer"}
    required_fields = {"id", "question", "options", "correct"}
    normalised: list[dict] = []
    for i, q in enumerate(questions):
        missing = required_fields - set(q.keys())
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1} missing fields: {', '.join(sorted(missing))}"
            )
        qtype = str(q.get("question_type", "mcq_single")).strip().lower()
        if qtype not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: invalid question_type '{qtype}'. "
                       f"Must be one of {sorted(ALLOWED_TYPES)}"
            )

        if qtype == "short_answer":
            ref = str(q.get("reference_answer") or "").strip()
            if not ref:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: short-answer needs a reference_answer"
                )
            try:
                max_score = float(q.get("max_score") or 1.0)
            except (TypeError, ValueError):
                max_score = 1.0
            if max_score <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: max_score must be greater than 0"
                )
            normalised.append({
                "question_id":      q["id"],
                "question":         q["question"],
                "options":          {},
                "correct":          "",
                "question_type":    qtype,
                "image_url":        str(q.get("image_url") or "") or None,
                "reference_answer": ref,
                "rubric":           str(q.get("rubric") or ""),
                "max_score":        max_score,
            })
            continue

        if qtype == "true_false":
            options = {"True": "True", "False": "False"}
        else:
            if not isinstance(q["options"], dict) or len(q["options"]) < 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: 'options' must be a dict with at least 2 entries"
                )
            options = q["options"]

        opt_keys = {str(k) for k in options.keys()}
        correct_raw = str(q["correct"] or "")
        correct_parts = [p.strip() for p in correct_raw.split(",") if p.strip()]
        if not correct_parts:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: 'correct' cannot be empty"
            )
        for cp in correct_parts:
            if cp not in opt_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: 'correct' value '{cp}' not in options"
                )
        if qtype == "mcq_single" and len(correct_parts) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: single-choice questions need exactly 1 correct answer"
            )
        if qtype == "mcq_multi" and len(correct_parts) < 2:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: multi-choice questions need at least 2 correct answers"
            )
        if qtype == "true_false" and (len(correct_parts) != 1 or
                                       correct_parts[0] not in ("True", "False")):
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: true/false correct must be 'True' or 'False'"
            )

        normalised.append({
            "question_id":   q["id"],
            "question":      q["question"],
            "options":       options,
            "correct":       ",".join(sorted(correct_parts)),
            "question_type": qtype,
            "image_url":     str(q.get("image_url") or "") or None,
        })

    exam_id = body.get("exam_id")
    if tid and exam_id:
        update_fields = {}
        if "exam_title" in body:
            update_fields["exam_title"] = body["exam_title"]
        if "duration_minutes" in body:
            update_fields["duration_minutes"] = body["duration_minutes"]
        if update_fields:
            supabase.table("exam_config").update(update_fields)\
                .eq("teacher_id", tid).eq("exam_id", exam_id).execute()

    q_query = supabase.table("questions").select("*")
    if tid:
        q_query = q_query.eq("teacher_id", tid)
    if exam_id:
        q_query = q_query.eq("exam_id", exam_id)
    backup = q_query.execute()
    backup_rows = backup.data or []
    try:
        del_q = supabase.table("questions").delete()
        if tid:
            del_q = del_q.eq("teacher_id", tid)
        if exam_id:
            del_q = del_q.eq("exam_id", exam_id)
        del_q.execute() if tid or exam_id else del_q.neq("question_id", -1).execute()
        extra = {}
        if tid:
            extra["teacher_id"] = tid
        if exam_id:
            extra["exam_id"] = exam_id
        records = [{**r, **extra} for r in normalised]
        try:
            supabase.table("questions").insert(records).execute()
        except Exception as e:
            msg = str(e).lower()
            if "question_type" in msg or "image_url" in msg or "column" in msg \
                    or "reference_answer" in msg or "rubric" in msg or "max_score" in msg:
                print("[Questions] new columns missing on DB, retrying without")
                legacy = [
                    {k: v for k, v in r.items()
                     if k not in ("question_type", "image_url",
                                  "reference_answer", "rubric", "max_score")}
                    for r in records
                ]
                supabase.table("questions").insert(legacy).execute()
            else:
                raise
    except Exception as e:
        print(f"[Questions] Insert failed, rolling back: {e}")
        if backup_rows:
            try:
                supabase.table("questions").upsert(backup_rows).execute()
            except Exception as e2:
                print(f"[Questions] Rollback also failed: {e2}")
        raise HTTPException(status_code=500, detail=f"Failed to update questions: {e}")
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
        _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"status": "updated", "count": len(questions)}
