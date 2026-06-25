"""Teacher authoring for coding questions (Edge Compiler, Phase 5).

Until now coding questions could only be created by scripts/seed_coding_question.py
or raw SQL. These endpoints let a teacher (or the AI generator, which posts the same
shape after the client runs the reference solution to fill expected outputs) create
and read coding questions through the product.

Keying invariant (see app/repositories/questions.py:load_questions): the whole
coding chain keys on the questions.question_id LABEL — load_questions remaps each
row to id = str(question_id), and the renderer/judge/scoring all use that. So
coding_test_cases.question_id is the LABEL, and we mint a unique label per question.
"""
import json
import logging
import uuid as _uuid

from fastapi import APIRouter, HTTPException, Request, Body

from ..auth import require_admin
from ..auth.scope import assert_can_author
from ..database import async_table as _atable
from .. import cache as _cache
from ..services import secrets_crypto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="")

# v1 language set — the full six the execution sandbox (execsvc/languages.py)
# can compile + run. Authoring rejects anything else so a teacher can't create a
# question students can't run. Host must have the toolchains installed
# (node/npm/typescript, gcc, g++, default-jdk).
SUPPORTED_LANGUAGES = {"javascript", "typescript", "python", "c", "cpp", "java"}
# Aliases the dashboard / LLM may send → the canonical key the runner and the
# student client both key on (mirrors execsvc/languages.py's alias table).
_LANG_ALIASES = {"js": "javascript", "ts": "typescript", "c++": "cpp"}
_VISIBILITY = {"sample", "hidden"}
MAX_TEST_CASES = 50
# Per-field cap on a test case's input / expected_output. 50 cases * megabytes
# each is a storage/DoS vector; 64 KB is far above any realistic I/O case.
MAX_FIELD_LEN = 64 * 1024


def _clean_options(raw: dict) -> dict:
    """Validate + normalize the per-question options stored in questions.options."""
    opts = raw or {}
    langs = opts.get("allowed_languages") or ["javascript"]
    if isinstance(langs, str):
        langs = [langs]
    langs = [str(l).strip().lower() for l in langs if str(l).strip()]
    langs = [_LANG_ALIASES.get(l, l) for l in langs]
    bad = [l for l in langs if l not in SUPPORTED_LANGUAGES]
    if not langs or bad:
        raise HTTPException(status_code=400,
                            detail=f"allowed_languages must be a non-empty subset of "
                                   f"{sorted(SUPPORTED_LANGUAGES)} (got bad: {bad})")
    try:
        marks = int(opts.get("marks", 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="marks must be an integer")
    if marks < 1 or marks > 100:
        raise HTTPException(status_code=400, detail="marks must be 1..100")
    policy = str(opts.get("marks_policy") or "partial").lower()
    if policy not in ("partial", "all_or_nothing"):
        raise HTTPException(status_code=400, detail="marks_policy must be 'partial' or 'all_or_nothing'")
    try:
        tlimit = int(opts.get("time_limit_ms") or 5000)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_limit_ms must be an integer")
    tlimit = max(500, min(tlimit, 15000))
    # starter_code is a per-language {lang: code} map (the dashboard sends one
    # template per allowed language). A legacy single string is still accepted.
    # Keep only allowed languages and cap each template.
    raw_starter = opts.get("starter_code")
    if raw_starter is None:
        raw_starter = opts.get("starter") or ""
    if isinstance(raw_starter, dict):
        # Keep a language's template when it's PRESENT, even if it's "" — a teacher
        # may intentionally clear a starter. `.get(l) is not None` keeps "" but drops
        # missing/None entries (truthiness would wrongly drop the empty string).
        starter = {l: str(raw_starter[l])[:20000] for l in langs
                   if raw_starter.get(l) is not None}
    elif isinstance(raw_starter, str):
        starter = raw_starter[:20000]
    else:
        starter = ""
    return {
        "allowed_languages": langs,
        "marks": marks,
        "marks_policy": policy,
        "time_limit_ms": tlimit,
        "starter_code": starter,
    }


def _clean_cases(raw_cases) -> list[dict]:
    """Validate the test cases. Requires >=1 hidden case (else nothing is graded);
    expected_output is provided by the caller — the dashboard/LLM fills it by running
    the reference solution through the same runtime, so the server only persists it."""
    if not isinstance(raw_cases, list) or not raw_cases:
        raise HTTPException(status_code=400, detail="test_cases must be a non-empty list")
    if len(raw_cases) > MAX_TEST_CASES:
        raise HTTPException(status_code=400, detail=f"at most {MAX_TEST_CASES} test cases")
    out, hidden = [], 0
    for i, c in enumerate(raw_cases):
        if not isinstance(c, dict):
            raise HTTPException(status_code=400, detail=f"test_cases[{i}] must be an object")
        vis = str(c.get("visibility") or "hidden").lower()
        if vis not in _VISIBILITY:
            raise HTTPException(status_code=400, detail=f"test_cases[{i}].visibility must be sample|hidden")
        if "expected_output" not in c:
            raise HTTPException(status_code=400, detail=f"test_cases[{i}] missing expected_output")
        if vis == "hidden":
            hidden += 1
        inp = str(c.get("input") or "")
        exp = str(c.get("expected_output"))
        if len(inp) > MAX_FIELD_LEN or len(exp) > MAX_FIELD_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"test_cases[{i}] input/expected_output exceeds {MAX_FIELD_LEN} bytes")
        ft = c.get("float_tolerance")
        if ft in (None, ""):
            ftol = None
        else:
            try:
                ftol = float(ft)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"test_cases[{i}].float_tolerance must be a number")
        out.append({
            "idx": i,
            "input": inp,
            "expected_output": exp,
            "visibility": vis,
            "float_tolerance": ftol,
        })
    if hidden == 0:
        raise HTTPException(status_code=400,
                            detail="at least one hidden test case is required (it's what gets graded)")
    return out


@router.post("/api/v1/admin/coding-question")
@router.put("/api/v1/admin/coding-question")
async def upsert_coding_question(body: dict, request: Request):
    """Create (or replace) a coding question + its test cases for an exam.

    Body: {exam_id, question, options{allowed_languages,marks,marks_policy,
    time_limit_ms,starter_code}, test_cases:[{input,expected_output,visibility,
    float_tolerance?}], question_id? (replace existing)}.
    """
    teacher = await require_admin(request)
    assert_can_author(teacher)
    tid = str(teacher["id"])

    exam_id = (body.get("exam_id") or "").strip()
    statement = (body.get("question") or "").strip()
    if not exam_id:
        raise HTTPException(status_code=400, detail="exam_id is required")
    if not statement:
        raise HTTPException(status_code=400, detail="question (problem statement) is required")

    options = _clean_options(body.get("options") or {})
    cases = _clean_cases(body.get("test_cases"))

    # Replace path: an existing question_id (must belong to this teacher+exam).
    qid = (body.get("question_id") or "").strip()
    replacing = bool(qid)
    if replacing:
        owned = (await _atable("questions").select("question_id")
                 .eq("teacher_id", tid).eq("exam_id", exam_id)
                 .eq("question_id", qid).limit(1).execute()).data
        if not owned:
            raise HTTPException(status_code=404, detail="question not found for this teacher/exam")
        # Drop old test cases; rewrite below.
        await _atable("coding_test_cases").delete().eq("teacher_id", tid).eq("question_id", qid).execute()
    else:
        qid = f"coding-{_uuid.uuid4().hex[:12]}"   # unique label (the coding chain's key)

    q_row = {
        "teacher_id": tid, "exam_id": exam_id, "question_id": qid,
        "question": statement, "question_type": "coding",
        "options": json.dumps(options), "correct": "",
    }
    try:
        if replacing:
            await _atable("questions").update(q_row).eq("teacher_id", tid)\
                .eq("exam_id", exam_id).eq("question_id", qid).execute()
        else:
            await _atable("questions").insert(q_row).execute()
        for c in cases:
            await _atable("coding_test_cases").insert({
                "question_id": qid, "teacher_id": tid, "idx": c["idx"],
                "input": c["input"],
                # Envelope-encrypt the secret answer key before it hits Postgres
                # (app/services/secrets_crypto.py) — a no-op if CODING_SECRETS_KEY
                # isn't configured (dev/CI), idempotent if already encrypted.
                "expected_output": secrets_crypto.encrypt(c["expected_output"]),
                "visibility": c["visibility"], "float_tolerance": c["float_tolerance"],
            }).execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[coding-question] write failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save coding question")

    if _cache:
        _cache.delete(f"questions:{tid}:{exam_id}")

    return {
        "question_id": qid, "exam_id": exam_id, "replaced": replacing,
        "test_cases": len(cases),
        "sample": sum(1 for c in cases if c["visibility"] == "sample"),
        "hidden": sum(1 for c in cases if c["visibility"] == "hidden"),
    }


@router.get("/api/v1/admin/coding-question")
async def get_coding_question(request: Request):
    """Fetch a coding question + its test cases (incl. hidden expected — TEACHER view)
    for the authoring editor. Teacher-scoped."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    qid = (request.query_params.get("question_id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="question_id is required")
    qrow = (await _atable("questions").select("question_id,exam_id,question,options")
            .eq("teacher_id", tid).eq("question_id", qid).limit(1).execute()).data
    if not qrow:
        raise HTTPException(status_code=404, detail="question not found")
    cases = (await _atable("coding_test_cases")
             .select("idx,input,expected_output,visibility,float_tolerance")
             .eq("teacher_id", tid).eq("question_id", qid).order("idx").execute()).data or []
    # Teacher-authoring view: decrypt so the editor shows the real answer key,
    # not the enc:v1: ciphertext (handles both encrypted and legacy rows).
    cases = [{**c, "expected_output": secrets_crypto.decrypt(c.get("expected_output"))}
             for c in cases]
    opts = qrow[0].get("options")
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except (ValueError, TypeError):
            opts = {}
    return {
        "question_id": qid, "exam_id": qrow[0].get("exam_id"),
        "question": qrow[0].get("question") or "", "options": opts or {},
        "test_cases": cases,
    }


@router.post("/api/v1/admin/coding-question/generate")
async def generate_coding_question_draft(body: dict, request: Request):
    """AI-draft a coding question for the authoring form. Returns a DRAFT (statement +
    reference solution + test cases with AI-drafted expected) for the teacher to
    review/verify, NOT auto-saved — the teacher edits then POSTs to
    /api/v1/admin/coding-question. The expected outputs are AI-drafted and flagged
    needs_verification (an LLM can mis-compute output)."""
    teacher = await require_admin(request)
    assert_can_author(teacher)
    from ..llm import is_configured, generate_coding_question
    if not is_configured():
        raise HTTPException(status_code=503, detail="AI generation is not configured")
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    difficulty = str(body.get("difficulty") or "medium")
    language = str(body.get("language") or "javascript")
    try:
        draft = await generate_coding_question(
            topic, difficulty=difficulty, language=language,
            grade_level=body.get("grade_level"))
    except Exception as e:
        logger.error("[coding-gen] generation failed: %s", e)
        raise HTTPException(status_code=502, detail="AI generation failed — please try again")
    return draft
