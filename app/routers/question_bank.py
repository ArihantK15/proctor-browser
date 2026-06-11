"""Question bank CRUD and AI features router."""

import asyncio
import hashlib
import json
import logging
import re
import time
_qbank_log = logging.getLogger("question_bank")
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Request, HTTPException, Body, UploadFile, File
from fastapi.routing import APIRouter
from pydantic import BaseModel, ConfigDict

from ..database import supabase, async_table as _atable
from ..limiter import limiter
from ..auth import require_admin
from .. import cache as _cache
from ..models import SessionStatus
from ..constants import QUESTION_IMG_DIR
from ..utils import _safe_path_component
from ..repositories.questions import load_questions as _load_questions, load_exam_config as _load_exam_config
from ..repositories.sessions import assert_session_owned as _assert_session_owned
from ..parsers.document import extract_document, ScannedPdfError, UnreadableDocError
from ..parsers.question_parser import parse_questions, BLOCKING_FLAGS
from ..parsers.region_render import render_region_png

router = APIRouter(prefix="")


# ─── PYDANTIC MODELS ──────────────────────────────────

class BankQuestionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    questions: list[dict] | None = None


class UpdateQuestionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question: str | None = None
    question_type: str | None = None
    options: dict | None = None
    correct: str | None = None
    image_url: str | None = None
    tags: list[str] | None = None


class ImportQuestionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    questions: list[dict]


class GenerateQuestionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    topic: str
    count: int = 10
    difficulty: str = "mixed"
    question_type: str = "mcq_single"
    grade_level: str = ""
    source_text: str | None = None


class SuggestTagsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question: str
    options: dict = {}
    correct: str = ""


class LintQuestionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    questions: list[dict]


class BankToExamIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question_ids: list[str]
    exam_id: str


class UpdateQuestionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    questions: list[dict]
    exam_id: str | None = None
    exam_title: str | None = None
    duration_minutes: int | None = None


# ─── QUESTION BANK ─────────────────────────────────────────────────

@router.get("/api/v1/admin/question-bank")
@limiter.limit("30/minute")
async def list_bank_questions(request: Request):
    """List all question bank entries for the teacher, optionally filtered by tag."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    tag = request.query_params.get("tag")
    q = (_atable("question_bank").select("*")
         .eq("teacher_id", tid)
         .order("created_at", desc=True)
         .limit(5000))
    rows = (await q.execute()).data or []
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    return rows


@router.post("/api/v1/admin/question-bank")
@limiter.limit("30/minute")
async def add_bank_questions(request: Request, body: BankQuestionIn = Body(...)):
    """Add one or more questions to the bank."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    questions = body.questions or ([body.model_dump()] if "question" in body.model_dump() else [])
    if not questions:
        raise HTTPException(status_code=400, detail="No questions provided")
    rows = []
    for q in questions:
        raw_tags = q.get("tags", []) or []
        tags = ([str(t).strip() for t in raw_tags if t is not None and str(t).strip()]
                if isinstance(raw_tags, list) else [])
        rows.append({
            "teacher_id": tid,
            "question": str(q.get("question", "") or ""),
            "question_type": str(q.get("question_type", "mcq_single") or "mcq_single"),
            # JSONB column — explicit json.dumps for asyncpg/Postgres
            # compatibility. See 9ce75f4 for the same pattern.
            "options": json.dumps(q.get("options", {}) or {}),
            "correct": str(q.get("correct", "")),
            "image_url": str(q.get("image_url", "") or ""),
            "tags": tags,
        })
    try:
        result = await _atable("question_bank").insert(rows).execute()
    except Exception as e:
        _qbank_log.error("[bank.add] insert failed for tid=%s rows=%d: %s",
                         tid, len(rows), e, exc_info=True)
        raise HTTPException(status_code=500,
            detail=f"Failed to save question(s): {type(e).__name__}")
    return result.data or []


@router.put("/api/v1/admin/question-bank/{qid}")
@limiter.limit("30/minute")
async def update_bank_question(qid: str, request: Request, body: UpdateQuestionIn = Body(...)):
    """Update a question in the bank."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    fields = {}
    for k in ("question", "question_type", "options", "correct", "image_url", "tags"):
        v = getattr(body, k, None)
        if v is not None:
            fields[k] = v
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    # question_bank.options is JSONB and no asyncpg codec is registered, so
    # a raw dict fails parameter binding ("expected str, got dict") — the
    # same constraint every insert path in this file handles via json.dumps.
    if isinstance(fields.get("options"), dict):
        fields["options"] = json.dumps(fields["options"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await (_atable("question_bank")
                    .update(fields).eq("id", qid).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Question not found")
    return result.data[0]


@router.delete("/api/v1/admin/question-bank/{qid}")
@limiter.limit("30/minute")
async def delete_bank_question(qid: str, request: Request):
    """Delete a question from the bank."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _atable("question_bank").delete().eq("id", qid).eq("teacher_id", tid).execute()
    return {"ok": True}


@router.post("/api/v1/admin/question-bank/import")
@limiter.limit("30/minute")
async def import_bank_questions(request: Request, body: ImportQuestionsIn = Body(...)):
    """Bulk import questions from CSV-style JSON array.

    Expected format: list of objects with keys:
    question, type, option_A, option_B, option_C, option_D, correct, image_url, tags
    """
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    items = body.questions
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
        # Normalise tags to a clean list[str] — asyncpg's text[] binder
        # rejects Nones and non-strings.
        raw_tags = item.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if t is not None and str(t).strip()]
        else:
            tags = [t.strip() for t in str(raw_tags).split(",") if t.strip()]
        rows.append({
            "teacher_id": tid,
            "question": str(item.get("question", "") or ""),
            "question_type": str(item.get("type", item.get("question_type", "mcq_single")) or "mcq_single"),
            # JSONB column — asyncpg won't auto-encode a Python dict for
            # JSONB binding (same root cause as 9ce75f4 fix for the
            # regular questions table). Explicit json.dumps works on
            # both the asyncpg/Postgres backend and Supabase REST
            # (which re-decodes the string on write).
            "options": json.dumps(options),
            "correct": str(item.get("correct", "")),
            "image_url": str(item.get("image_url", "") or ""),
            "tags": tags,
        })
    try:
        result = await _atable("question_bank").insert(rows).execute()
    except Exception as e:
        _qbank_log.error("[bank.import] insert failed for tid=%s rows=%d: %s",
                         tid, len(rows), e, exc_info=True)
        raise HTTPException(status_code=500,
            detail=f"Failed to import questions: {type(e).__name__}. "
                   f"Check server logs (request_id in response headers).")
    inserted = result.data or []
    return {
        "imported": len(inserted),
        "inserted_ids": [r.get("id") for r in inserted if r.get("id")],
    }


# ─── PDF/DOCX IMPORT (on-device extraction → review → bank) ────────────

_MAX_UPLOAD = 20 * 1024 * 1024   # 20 MB


async def _read_upload_capped(file: UploadFile) -> bytes:
    """Read an upload with a hard memory cap. Reads at most _MAX_UPLOAD+1 bytes
    so an oversized file is rejected with 413 WITHOUT buffering the whole thing
    into a bytes object — a multi-GB POST can't balloon process memory before we
    reject it."""
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 20MB).")
    return data


def _extract_parse_render(data: bytes, name: str, tid: str) -> dict:
    """CPU/IO-bound import pipeline (PDF/DOCX parse + region rasterization).

    Pure of the request loop so it can run under asyncio.to_thread — pdfplumber
    / pypdfium2 are synchronous and CPU-heavy, and a large paper would otherwise
    block every other API request (incl. live exams) for the parse duration.
    Raises ScannedPdfError / UnreadableDocError for the caller to translate."""
    doc = extract_document(data, name)
    questions = parse_questions(doc.text, lines=doc.lines)
    for q in questions:
        if q._region and doc.pdf_bytes is not None:
            png = render_region_png(doc.pdf_bytes, q._region["page"], q._region["bbox"])
            if png:
                q.image_url = _store_region_png(tid, png)
            else:
                if "has_image" in q.flags:
                    q.flags.remove("has_image")
                if "math_review" not in q.flags:
                    q.flags.append("math_review")
    public = [q.to_public() for q in questions]
    ready = sum(1 for q in public if not (set(q["flags"]) & BLOCKING_FLAGS))
    return {"found": len(public), "ready": ready, "questions": public}


def _store_region_png(tid: str, png: bytes) -> str:
    """Persist a rasterized question region under the teacher's own image dir,
    reusing the existing /api/v1/question-image/{tid}/{file} serve route."""
    digest = hashlib.sha256(png, usedforsecurity=False).hexdigest()[:24]
    safe_tid = _safe_path_component(str(tid))
    tdir = Path(QUESTION_IMG_DIR) / safe_tid
    tdir.mkdir(parents=True, exist_ok=True)
    fpath = tdir / f"{digest}.png"
    if not fpath.exists():
        with open(fpath, "wb") as f:
            f.write(png)
    return f"/api/v1/question-image/{tid}/{digest}.png"


def _recompute_blocking(item: dict) -> list:
    """Server-side truth for blocking flags — never trusts client-sent flags."""
    flags: list = []
    qtype = str(item.get("type") or item.get("question_type") or "mcq_single").lower()
    options = item.get("options") or {}
    correct = str(item.get("correct") or "").strip()
    has_image = bool(item.get("image_url"))
    if not correct:
        flags.append("no_answer")
    if qtype in ("mcq_single", "mcq_multi") and len(options) < 2:
        flags.append("few_options")
    if not str(item.get("question") or "").strip() and not has_image:
        flags.append("low_confidence")
    return flags


class ExtractConfirmIn(BaseModel):
    model_config = ConfigDict(strict=False)
    questions: list[dict]


@router.post("/api/v1/admin/question-bank/extract")
@limiter.limit("20/minute")
async def extract_questions(request: Request, file: UploadFile = File(...)):
    """Upload a text PDF or DOCX → on-device extraction → review PREVIEW.

    Nothing is persisted here; the teacher reviews and confirms via
    /extract/confirm. Question content never leaves the server."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    name = (file.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".docx")):
        raise HTTPException(status_code=415,
            detail="Only PDF and Word (.docx) files are supported.")
    data = await _read_upload_capped(file)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    # Heavy parse/raster runs off the event loop so it can't stall live exams.
    try:
        return await asyncio.to_thread(_extract_parse_render, data, name, tid)
    except ScannedPdfError:
        raise HTTPException(status_code=422,
            detail="This looks like a scanned PDF — text extraction isn't supported yet.")
    except UnreadableDocError as e:
        raise HTTPException(status_code=422, detail=str(e) or "Couldn't open this file.")


@router.post("/api/v1/admin/question-bank/extract/confirm")
@limiter.limit("20/minute")
async def confirm_extracted(request: Request, body: ExtractConfirmIn = Body(...)):
    """Persist reviewed questions into the bank. Re-validates blocking flags
    server-side (defense in depth — never trusts the client's 'this is clean')."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    items = body.questions or []
    if not items:
        raise HTTPException(status_code=400, detail="No questions to import.")
    if len(items) > 2000:
        raise HTTPException(status_code=413,
            detail=f"Too many questions ({len(items)}). Max 2000 — split the file.")
    # Egress guard: only the CALLER's own locally-served image paths may be
    # persisted. Our extraction only ever emits this exact prefix; anything
    # else is client tampering — either an external beacon that would fire in
    # the student's browser, or a cross-teacher path that the serve route would
    # 401 (leaving a permanently-broken image baked into the saved exam).
    img_prefix = f"/api/v1/question-image/{tid}/"
    for idx, item in enumerate(items):
        img = str(item.get("image_url") or "")
        if img and not img.startswith(img_prefix):
            raise HTTPException(status_code=400,
                detail=f"Question {idx + 1} has an invalid image reference.")
        blocking = set(_recompute_blocking(item)) & BLOCKING_FLAGS
        if blocking:
            raise HTTPException(status_code=400,
                detail=f"Question {idx + 1} still needs attention "
                       f"({', '.join(sorted(blocking))}). Resolve all flagged "
                       f"questions before importing.")
    rows = []
    for item in items:
        options = item.get("options") or {}
        raw_tags = item.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if t is not None and str(t).strip()]
        else:
            tags = [t.strip() for t in str(raw_tags).split(",") if t.strip()]
        rows.append({
            "teacher_id": tid,
            "question": str(item.get("question", "") or ""),
            # Accept either key: the extract preview emits `type` (to_public),
            # but other bank APIs use `question_type` — honour whichever the
            # client sends so a numeric/short_answer item isn't silently stored
            # as an MCQ.
            "question_type": str(item.get("type") or item.get("question_type") or "mcq_single"),
            "options": json.dumps(options),
            "correct": str(item.get("correct", "")),
            "image_url": str(item.get("image_url", "") or ""),
            "tags": tags,
        })
    try:
        result = await _atable("question_bank").insert(rows).execute()
    except Exception as e:
        _qbank_log.error("[bank.confirm] insert failed tid=%s rows=%d: %s",
                         tid, len(rows), e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to import questions.")
    inserted = result.data or []
    return {"imported": len(inserted),
            "inserted_ids": [r.get("id") for r in inserted if r.get("id")]}


def _clean_source_text(text: str) -> str:
    """Light, safe normalisation of extracted notes before sending to the LLM.

    Removes standalone ligature/bullet artifacts that some PDFs emit (e.g. an
    arrow/bullet glyph mis-decoded to a lone "fi"/"fl"/"ff" line) and collapses
    runaway whitespace for a tighter prompt. Deliberately conservative: it never
    touches inline tokens (a lone "n" could be a real code variable), so it can't
    corrupt code snippets in the notes."""
    text = re.sub(r"(?im)^[ \t]*(fi|fl|ff)[ \t]*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@router.post("/api/v1/admin/question-bank/generate-from-file")
@limiter.limit("10/minute")
async def generate_questions_from_file(
    request: Request,
    file: UploadFile = File(...),
    count: int = 10,
    difficulty: str = "mixed",
    question_type: str = "mcq_single",
    topic: str = "",
):
    """Upload notes (PDF/DOCX/PPTX) → extract text → LLM-generate a PREVIEW.

    The extracted text is sent to the configured AI provider (same path as
    /generate). Nothing is persisted; the teacher reviews and adds to the bank
    through the existing generate-preview flow."""
    teacher = await require_admin(request)
    _ = str(teacher["id"])
    from ..llm import is_configured, generate_questions
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set the AI provider key on the server.")
    name = (file.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".docx") or name.endswith(".pptx")):
        raise HTTPException(status_code=415,
            detail="Only PDF, Word (.docx) and PowerPoint (.pptx) files are supported.")
    data = await _read_upload_capped(file)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        doc = await asyncio.to_thread(extract_document, data, name)
    except ScannedPdfError:
        raise HTTPException(status_code=422,
            detail="This looks like a scanned PDF — text extraction isn't supported yet.")
    except UnreadableDocError as e:
        raise HTTPException(status_code=422, detail=str(e) or "Couldn't open this file.")

    source_text = _clean_source_text(doc.text or "")
    if len(source_text) < 40:
        raise HTTPException(status_code=422,
            detail="Couldn't find enough text in this file to generate questions.")
    truncated = len(source_text) > 20000
    source_text = source_text[:20000]

    try:
        questions = await generate_questions(
            topic=topic.strip(),
            count=count,
            difficulty=difficulty.strip().lower(),
            question_type=question_type.strip(),
            source_text=source_text,
        )
    except Exception as e:
        _qbank_log.error("[gen-from-file] generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="AI provider error. Try again.")
    return {"questions": questions or [], "count": len(questions or []),
            "truncated": truncated}


@router.get("/api/v1/admin/question-bank/export")
@limiter.limit("30/minute")
async def export_bank_questions(request: Request):
    """Export all bank questions as JSON."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rows = (await _atable("question_bank").select("*")
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
async def generate_bank_questions(request: Request, body: GenerateQuestionsIn = Body(...)):
    """Generate question-bank rows from a topic / source text via LLM.

    Returns a *preview* — the teacher reviews and explicitly clicks
    'Add to Bank' to actually persist.
    """
    teacher = await require_admin(request)
    _ = str(teacher["id"])

    from ..llm import is_configured, generate_questions
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set GROQ_API_KEY on the server.")

    topic = body.topic.strip()
    count = body.count
    difficulty = body.difficulty.strip().lower()
    qtype = body.question_type.strip()
    grade_level = body.grade_level.strip() or None
    source_text = body.source_text
    if source_text and len(source_text) > 20000:
        raise HTTPException(status_code=400,
            detail="source_text too long (max 20000 chars)")

    try:
        questions = await generate_questions(
            topic=topic,
            count=count,
            difficulty=difficulty,
            question_type=qtype,
            source_text=source_text,
            grade_level=grade_level,
        )
    except httpx.HTTPStatusError as e:
        _qbank_log.error("[llm] groq error: %s", e)
        raise HTTPException(status_code=502, detail="AI provider error. Try again.")
    except Exception as e:
        _qbank_log.error("[llm] generate failed: %s", e)
        raise HTTPException(status_code=500, detail="Generation failed. Try again.")

    if not questions:
        raise HTTPException(status_code=502,
            detail="AI returned no usable questions. Try a more specific topic.")
    return {"questions": questions, "count": len(questions)}


@router.post("/api/v1/admin/question-bank/suggest-tags")
@limiter.limit("60/minute")
async def suggest_question_tags(request: Request, body: SuggestTagsIn = Body(...)):
    """Suggest 3-5 tags for a single question."""
    await require_admin(request)
    from ..llm import is_configured, suggest_tags
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set GROQ_API_KEY on the server.")
    question = body.question.strip()
    options = body.options
    correct = body.correct
    try:
        tags = await suggest_tags(question[:2000], options, str(correct)[:50])
    except Exception as e:
        _qbank_log.warning("[llm] suggest_tags failed: %s", e)
        raise HTTPException(status_code=502, detail="AI provider error.")
    return {"tags": tags}


@router.post("/api/v1/admin/lint-questions")
@limiter.limit("10/minute")
async def lint_questions_endpoint(request: Request, body: LintQuestionsIn = Body(...)):
    """Pre-publish AI review of an exam's questions."""
    await require_admin(request)
    from ..llm import is_configured, lint_questions
    if not is_configured():
        raise HTTPException(status_code=503,
            detail="AI features unavailable. Set LLM_API_KEY on the server.")

    questions = body.questions
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
            chunk_results = await lint_questions(chunk)
            if not chunk_results:
                for q in chunk:
                    all_results.append({"idx": q["idx"], "issues": [],
                                        "lint_failed": True})
            else:
                all_results.extend(chunk_results)
    except Exception as e:
        _qbank_log.error("[llm] lint_questions failed: %s", e)
        raise HTTPException(status_code=502, detail="AI provider error. Try again.")

    total_issues = sum(len(r.get("issues", [])) for r in all_results)
    return {"results": all_results, "total_issues": total_issues}


class GenerateRubricIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question: str
    reference_answer: str = ""
    max_score: int = 5


@router.post("/api/v1/admin/generate-rubric")
@limiter.limit("10/minute")
async def generate_rubric_endpoint(request: Request, body: GenerateRubricIn = Body(...)):
    """Generate a grading rubric for a short-answer question."""
    from ..llm import is_configured, generate_rubric
    if not is_configured():
        raise HTTPException(status_code=503, detail="AI features unavailable. Set LLM_API_KEY on the server.")
    teacher = await require_admin(request)
    _ = teacher
    try:
        result = await generate_rubric(body.question, body.reference_answer, body.max_score)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail="AI provider error. Try again.")


@router.post("/api/v1/admin/question-bank/to-exam")
@limiter.limit("30/minute")
async def bank_to_exam(request: Request, body: BankToExamIn = Body(...)):
    """Copy bank questions into an exam's question list."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    question_ids = body.question_ids
    exam_id = body.exam_id
    if not question_ids or not exam_id:
        raise HTTPException(status_code=400, detail="question_ids and exam_id required")
    if len(question_ids) > 500:
        raise HTTPException(status_code=413,
            detail="Too many questions. Max 500 per copy.")

    try:
        own_via_config = (
            await _atable("exam_config")
            .select("exam_id").eq("teacher_id", tid)
            .eq("exam_id", exam_id).limit(1).execute()
        ).data
        if not own_via_config:
            own_via_questions = (
                await _atable("questions")
                .select("exam_id").eq("teacher_id", tid)
                .eq("exam_id", exam_id).limit(1).execute()
            ).data
            if not own_via_questions:
                raise HTTPException(status_code=404, detail="Exam not found")

        res = await (
            _atable("question_bank").select("*")
            .eq("teacher_id", tid).in_("id", question_ids).execute()
        )
        bank_rows = res.data or []
        if not bank_rows:
            raise HTTPException(status_code=404, detail="No matching bank questions found")

        existing = await _load_questions(teacher_id=tid, exam_id=exam_id)
        max_id = max((int(q.get("question_id", q.get("id", 0))) for q in existing), default=0)

        new_rows = []
        bad = []
        for i, bq in enumerate(bank_rows, start=max_id + 1):
            q_text = (bq.get("question") or "").strip()
            correct = (bq.get("correct") or "").strip()
            opts = bq.get("options") or {}
            # Numeric questions legitimately carry no options (their tolerance
            # band lives in `correct` as "range:MIN:MAX") — don't reject them.
            bq_type = str(bq.get("question_type") or "mcq_single").lower()
            needs_opts = bq_type != "numeric"
            if not q_text or not correct or (needs_opts and not opts):
                bad.append({"id": bq.get("id"), "reason":
                    f"missing fields: question={'OK' if q_text else 'MISSING'}, "
                    f"correct={'OK' if correct else 'MISSING'}, "
                    f"options={'OK' if (opts or not needs_opts) else 'MISSING'}"})
                continue
            # `questions.options` is TEXT on the legacy schema (per 9ce75f4)
            # so asyncpg needs an explicit json.dumps. opts here comes
            # back as a dict from question_bank (JSONB) — re-encode for
            # the destination column. Defensive: if it's already a string
            # leave it as-is.
            opts_for_insert = opts if isinstance(opts, str) else json.dumps(opts)
            new_rows.append({
                "teacher_id": tid,
                "exam_id": exam_id,
                "question_id": i,
                "question": q_text,
                "question_type": bq.get("question_type", "mcq_single"),
                "options": opts_for_insert,
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
                    await _atable("questions").insert(new_rows).execute()
                    if attempted_drops:
                        _qbank_log.info("[bank-to-exam] succeeded after dropping %s due to schema mismatch.", attempted_drops)
                    break
                except Exception as ie:
                    msg = str(ie)
                    m = re.search(r"Could not find the '([^']+)' column", msg, re.IGNORECASE)
                    if not m:
                        raise
                    missing_col = m.group(1)
                    if missing_col not in optional_cols:
                        raise
                    _qbank_log.warning("[bank-to-exam] column '%s' missing — dropping + retrying", missing_col)
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
        _qbank_log.error("[bank-to-exam][ERROR] tid=%s exam=%s qcount=%d err=%s: %s", tid, exam_id, len(question_ids), type(e).__name__, e, exc_info=True)
        raise HTTPException(status_code=502,
            detail="Couldn't copy questions from bank. Try again.")


@router.get("/api/v1/admin/questions")
@limiter.limit("30/minute")
async def get_admin_questions(request: Request):
    """Return all questions including correct answers (admin only)."""
    teacher = await require_admin(request)
    tid = teacher["id"]
    exam_id = request.query_params.get("exam_id")
    try:
        config = await _load_exam_config(str(tid) if tid else None, exam_id=exam_id)
        questions = await _load_questions(str(tid) if tid else None, exam_id=exam_id)
        return {
            "exam_title": config.get("exam_title", "Exam"),
            "duration_minutes": config.get("duration_minutes", 60),
            "questions": questions,
        }
    except Exception as e:
        _qbank_log.error("[Questions] ERROR: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/answers/{session_id:path}")
@limiter.limit("30/minute")
async def get_admin_answers(session_id: str, request: Request):
    """Return student answers merged with correct answers for the detail modal."""
    teacher = await require_admin(request)
    # Org-admin roll-up: resolve the session through the scope spine (404s
    # cross-tenant) and key reads on the session OWNER's tid, not the caller's,
    # so an admin viewing a co-teacher's session sees the answers instead of an
    # empty list. Matches the screenshot/scorecard/results roll-up.
    from ..auth.scope import resolve_scope, assert_session_accessible
    scope = await resolve_scope(teacher, request)
    sess = await assert_session_accessible(session_id, scope)
    tid = str(sess.get("teacher_id") or "")
    # Ownerless/orphan session → no derivable owner. NEVER call _load_questions("")
    # or query answers with an empty teacher_id: load_questions treats a falsy
    # teacher_id as "no filter" and would return EVERY teacher's questions across
    # all orgs (cross-tenant leak). Return an empty review instead.
    if not tid:
        return {"answers": [], "total": 0, "correct_count": 0}

    # Scope questions to THIS session's exam. question_id is unique only within
    # (teacher_id, exam_id), so loading every exam's questions and merging by
    # qid would collide across exams — pulling in wrong question text / correct
    # answers and duplicate rows. The session row carries its exam_id.
    exam_id = sess.get("exam_id")
    questions = await _load_questions(tid, exam_id=exam_id)
    ans_result = await _atable("answers").select("question_id,answer")\
        .eq("session_key", session_id)\
        .eq("teacher_id", tid)\
        .execute()
    ans_map = {str(r["question_id"]): str(r["answer"]) for r in (ans_result.data or [])}

    # Delegate to the authoritative grader so the admin "is_correct" display
    # matches the actual score — including numeric-range ("range:MIN:MAX")
    # questions, which a local set-equality copy would always mark wrong.
    from ..services.scoring import answers_match as _answers_match

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
@limiter.limit("30/minute")
async def update_questions(request: Request, body: UpdateQuestionsIn = Body(...)):
    """Update questions in Supabase."""
    teacher = await require_admin(request)
    tid = teacher["id"]
    questions = body.questions
    if not isinstance(questions, list) or len(questions) == 0:
        raise HTTPException(status_code=400, detail="'questions' must be a non-empty list")

    ALLOWED_TYPES = {"mcq_single", "mcq_multi", "true_false", "short_answer", "numeric"}
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
                # `questions.options` is a TEXT column on the legacy
                # schema; asyncpg won't bind a Python dict there. Serialize
                # to JSON so both Supabase REST (tolerates either) and
                # plain Postgres (strict) accept it.
                "options":          "{}",
                "correct":          "",
                "question_type":    qtype,
                "image_url":        str(q.get("image_url") or "") or None,
                "reference_answer": ref,
                "rubric":           str(q.get("rubric") or ""),
                "max_score":        max_score,
            })
            continue

        if qtype == "numeric":
            # Numeric/integer questions carry no options; the tolerance band is
            # encoded in `correct` as "range:MIN:MAX". Students never see it.
            correct_raw = str(q.get("correct") or "").strip()
            parts = correct_raw.split(":")
            if len(parts) != 3 or parts[0].lower() != "range":
                raise HTTPException(status_code=400,
                    detail=f"Question {i+1}: numeric question needs a min and max value")
            try:
                lo = float(parts[1])
                hi = float(parts[2])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400,
                    detail=f"Question {i+1}: numeric min/max must be numbers")
            lo_s, hi_s = parts[1].strip(), parts[2].strip()
            if lo > hi:                    # tolerate bounds entered in reverse
                lo_s, hi_s = hi_s, lo_s
            normalised.append({
                "question_id":   q["id"],
                "question":      q["question"],
                "options":       "{}",
                "correct":       f"range:{lo_s}:{hi_s}",
                "question_type": "numeric",
                "image_url":     str(q.get("image_url") or "") or None,
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
            # `questions.options` is TEXT on the legacy schema (jsonb only
            # for question_bank). asyncpg rejects a dict for a text param
            # with `expected str, got dict` — serialize so both backends
            # work. The reader side (load_questions) already json.loads()
            # the string back into a dict.
            "options":       json.dumps(options),
            "correct":       ",".join(sorted(correct_parts)),
            "question_type": qtype,
            "image_url":     str(q.get("image_url") or "") or None,
        })

    exam_id = body.exam_id
    if tid and exam_id:
        update_fields = {}
        if body.exam_title is not None:
            update_fields["exam_title"] = body.exam_title
        if body.duration_minutes is not None:
            update_fields["duration_minutes"] = body.duration_minutes
        if update_fields:
            await _atable("exam_config").update(update_fields)\
                .eq("teacher_id", tid).eq("exam_id", exam_id).execute()

    q_query = _atable("questions").select("*")
    if tid:
        q_query = q_query.eq("teacher_id", tid)
    if exam_id:
        q_query = q_query.eq("exam_id", exam_id)
    backup = await q_query.execute()
    backup_rows = backup.data or []
    try:
        extra = {}
        if tid:
            extra["teacher_id"] = tid
        if exam_id:
            extra["exam_id"] = exam_id
        records = [{**r, **extra} for r in normalised]
        # UPSERT, not INSERT: the questions table has
        # UNIQUE (teacher_id, exam_id, question_id), so a plain INSERT
        # collides with the existing rows on every re-save of an exam
        # that already has questions — the whole save would 500 and roll
        # back. Upsert updates the surviving rows in place, which also
        # preserves the C16 guarantee (no window where the exam has no
        # questions).
        try:
            await _atable("questions").upsert(records).execute()
        except Exception as e:
            msg = str(e).lower()
            if "question_type" in msg or "image_url" in msg or "column" in msg \
                    or "reference_answer" in msg or "rubric" in msg or "max_score" in msg:
                _qbank_log.warning("[Questions] new columns missing on DB, retrying without")
                legacy = [
                    {k: v for k, v in r.items()
                     if k not in ("question_type", "image_url",
                                  "reference_answer", "rubric", "max_score")}
                    for r in records
                ]
                await _atable("questions").upsert(legacy).execute()
            else:
                raise
        # Delete only the STALE old rows — the ones whose question_id left
        # the new set — addressed by primary key from the backup snapshot.
        # The previous filter (teacher_id + exam_id alone) matched the rows
        # just written too and would have wiped the exam.
        if exam_id:
            new_qids = {str(r["question_id"]) for r in normalised}
            stale_ids = [r["id"] for r in backup_rows
                         if r.get("id") is not None
                         and str(r.get("question_id")) not in new_qids]
        else:
            # Legacy single-exam mode: rows have NULL exam_id, which never
            # matches the (teacher_id, exam_id, question_id) conflict
            # target (NULLs are distinct), so the upsert inserted fresh
            # rows rather than updating in place — every backup row is
            # stale and must go, or the exam doubles its questions.
            stale_ids = [r["id"] for r in backup_rows if r.get("id") is not None]
        if stale_ids:
            del_q = _atable("questions").delete().in_("id", stale_ids)
            if tid:
                del_q = del_q.eq("teacher_id", tid)
            await del_q.execute()
    except Exception as e:
        _qbank_log.error("[Questions] Insert failed, rolling back: %s", e)
        if backup_rows:
            try:
                await _atable("questions").upsert(backup_rows).execute()
            except Exception as e2:
                _qbank_log.critical("[Questions] Rollback also failed: %s", e2)
        raise HTTPException(status_code=500, detail="Failed to update questions. Try again.")
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
        _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"status": "updated", "count": len(questions)}
