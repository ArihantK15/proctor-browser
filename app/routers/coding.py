"""Edge Compiler — server-side execution + judge endpoints.

Code now EXECUTES SERVER-SIDE (see
docs/superpowers/specs/2026-06-23-server-side-coding-execution-design.md).
Three trust zones:
  - kiosk: only edits + POSTs source (renderer/coding-ui.js).
  - this orchestrator (trusted): holds expected outputs, calls the execution
    service per test case, compares, stores, returns counts/cases.
  - execution service (execsvc): network-isolated, credential-less; it is
    NEVER sent an expected_output — only {language, source, stdin, limits}.

Security invariants (reviewed line-by-line — see the spec's Anti-cheat/Compliance):
  1. Hidden `expected_output` is read ONLY under system_context() for the
     comparison and is NEVER serialized to the client, and NEVER sent to the
     execution service — the delivery endpoint omits it from the SELECT
     column list for hidden cases, and run_one() is only ever called with
     {language, source, stdin, limits}.
  2. `teacher_id` on coding_submissions is stamped from the JWT claim, never the
     request body.
  3. A per-question SUBMIT-ATTEMPT CAP defends against the output-oracle attack
     (flip one output, read the count delta to infer expected) — distinct from the
     per-minute rate limit.
  4. The insert is idempotent (reserve_idempotency) so a retry-queued client can't
     double-write a submission.
  5. Failure policy: if the execution service is unavailable, /coding/judge
     returns HTTP 503 {"retryable": true} and writes NO submission row — never
     a silent 0/total, never an auto-fail (LeetCode-style "please wait").
"""
import hashlib
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..auth import require_auth
from ..database import async_table as _atable
from ..db_context import system_context
from ..limiter import limiter
from ..repositories.questions import load_exam_config as _load_exam_config
from ..services.coding_judge import normalize_output, _float_match
from ..services.exec_client import run_one, ExecLimits, ExecUnavailable
from ..services.idempotency import (
    reserve_idempotency, release_idempotency, mark_idempotent,
)
from ..services import secrets_crypto
from .exam import _assert_student_session_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="")

DEFAULT_MAX_SUBMIT_ATTEMPTS = 10

# Service-call defaults. time_limit_ms (per-question, teacher-set, 500..15000 —
# see admin_coding._clean_options) drives wall_ms; cpu/mem/output stay fixed
# service-wide until real concurrency numbers exist (plan Phase 8). execsvc
# clamps everything server-side regardless, so these are just sane requests.
DEFAULT_TIME_LIMIT_MS = 5000
EXEC_CPU_MS = 4000
EXEC_MEM_MB = 256
EXEC_OUTPUT_KB = 64


async def _question_time_limit_ms(question_id: str) -> int:
    """Best-effort read of the question's configured time_limit_ms. Falls back
    to the default on any missing/malformed data — never raises."""
    try:
        with system_context():
            rows = (await _atable("questions").select("options")
                     .eq("question_id", question_id).limit(1).execute()).data or []
        if not rows:
            return DEFAULT_TIME_LIMIT_MS
        opts = rows[0].get("options") or {}
        if isinstance(opts, str):
            opts = json.loads(opts)
        return int(opts.get("time_limit_ms") or DEFAULT_TIME_LIMIT_MS)
    except Exception:
        return DEFAULT_TIME_LIMIT_MS


def _limits_for(time_limit_ms: int) -> ExecLimits:
    return ExecLimits(cpu_ms=EXEC_CPU_MS, wall_ms=max(time_limit_ms, EXEC_CPU_MS),
                      mem_mb=EXEC_MEM_MB, output_kb=EXEC_OUTPUT_KB)


@router.get("/api/v1/coding/testcases")
@limiter.limit("60/minute")
async def coding_testcases(request: Request):
    """Deliver a question's test cases to the kiosk: sample cases in full (worked
    examples — expected is public by design) and hidden cases as INPUT ONLY.

    Invariant #1: the hidden SELECT lists only (idx, input). `expected_output` is
    absent from the column list, so it cannot enter a client-bound payload — this
    is enforced at the query, not by filtering in Python."""
    claims = require_auth(request)
    session_id = (request.query_params.get("session_id") or "").strip()
    question_id = (request.query_params.get("question_id") or "").strip()
    if not session_id or not question_id:
        raise HTTPException(status_code=400, detail="session_id and question_id required")
    await _assert_student_session_access(claims, session_id)

    # Students have NO RLS grant on coding_test_cases; this is a system-context read.
    with system_context():
        sample_rows = (await _atable("coding_test_cases")
                       .select("idx,input,expected_output")
                       .eq("question_id", question_id).eq("visibility", "sample")
                       .order("idx").execute()).data or []
        hidden_rows = (await _atable("coding_test_cases")
                       .select("idx,input")   # expected_output deliberately omitted
                       .eq("question_id", question_id).eq("visibility", "hidden")
                       .order("idx").execute()).data or []
    return {
        # Sample expected_output IS shown to the student (worked example, not
        # secret) — decrypt() handles both legacy plaintext and enc:v1: rows.
        "sample": [{"idx": r["idx"], "input": r["input"],
                    "expected_output": secrets_crypto.decrypt(r["expected_output"])}
                   for r in sample_rows],
        "hidden_inputs": [{"idx": r["idx"], "input": r["input"]} for r in hidden_rows],
    }


@router.post("/api/v1/coding/run")
@limiter.limit("30/minute")
async def coding_run(body: dict, request: Request):
    """Run the student's source against the (public) SAMPLE cases, server-side.

    Sample expected outputs ARE returned — they're the worked examples shown to
    the student. The execution service is called once per sample case with only
    {language, source, stdin, limits}; it never sees expected_output."""
    claims = require_auth(request)
    session_id = (body.get("session_id") or "").strip()
    question_id = (body.get("question_id") or "").strip()
    language = (body.get("language") or "").strip()
    source = body.get("source") or ""
    if not session_id or not question_id:
        raise HTTPException(status_code=400, detail="session_id and question_id required")
    await _assert_student_session_access(claims, session_id)

    with system_context():
        sample = (await _atable("coding_test_cases")
                  .select("idx,input,expected_output")
                  .eq("question_id", question_id).eq("visibility", "sample")
                  .order("idx").execute()).data or []

    time_limit_ms = await _question_time_limit_ms(question_id)
    limits = _limits_for(time_limit_ms)

    cases = []
    passed = 0
    for row in sample:
        expected = secrets_crypto.decrypt(row.get("expected_output") or "")
        try:
            result = run_one(language, source, row.get("input") or "", limits)
        except ExecUnavailable:
            raise HTTPException(status_code=503, detail={"retryable": True,
                                "error": "execution service unavailable"})
        if result.compile_error:
            status = "error"
            ok = False
            err = result.compile_error
        elif result.timed_out:
            status = "timeout"
            ok = False
            err = None
        else:
            ok = normalize_output(result.stdout) == normalize_output(expected)
            status = "passed" if ok else "failed"
            err = result.stderr or None
        if ok:
            passed += 1
        cases.append({
            "input": row.get("input"),
            "expected_output": expected,
            "output": result.stdout,
            "status": status,
            "time_ms": result.time_ms,
            "error": err,
        })

    return {"cases": cases, "passed": passed, "total": len(sample)}


@router.post("/api/v1/coding/judge")
@limiter.limit("30/minute")
async def coding_judge(body: dict, request: Request):
    """Run the student's source against the SECRET hidden cases, server-side,
    and grade it. Returns `{passed, total, average_execution_ms}` only — never
    per-case detail or expected values."""
    claims = require_auth(request)
    session_id = (body.get("session_id") or "").strip()
    question_id = (body.get("question_id") or "").strip()
    language = (body.get("language") or "").strip()
    source = body.get("source") or ""
    if not session_id or not question_id:
        raise HTTPException(status_code=400, detail="session_id and question_id required")
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")

    # Invariant #3 — per-question submit-attempt cap (output-oracle defense). Count
    # prior submissions for this (session, question); distinct from the rate limit.
    config = await _load_exam_config(str(tid or ""), exam_id=eid)
    cap = int((config or {}).get("coding_max_submit_attempts") or DEFAULT_MAX_SUBMIT_ATTEMPTS)
    prior = (await _atable("coding_submissions").select("id")
             .eq("session_id", session_id).eq("question_id", question_id).execute()).data or []
    if len(prior) >= cap:
        raise HTTPException(status_code=429, detail="Submission limit reached for this problem")

    # Invariant #4 — idempotency. Key on (session, question, source) so a genuine
    # new attempt isn't suppressed, but a retry double-fire writes once.
    attempt_hash = hashlib.sha256(
        json.dumps([session_id, question_id, source], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    idem_key = f"coding_judge:{session_id}:{question_id}:{attempt_hash}"
    acquired, cached = await reserve_idempotency(idem_key, ttl=300)
    if not acquired:
        if cached is not None:
            return cached
        raise HTTPException(status_code=409, detail="Submission already in progress")

    try:
        # Invariant #1 — hidden expected read under system_context, used only for the
        # comparison, never returned, and never sent to the executor.
        with system_context():
            hidden = (await _atable("coding_test_cases")
                      .select("idx,input,expected_output,float_tolerance")
                      .eq("question_id", question_id).eq("visibility", "hidden")
                      .order("idx").execute()).data or []

        time_limit_ms = await _question_time_limit_ms(question_id)
        limits = _limits_for(time_limit_ms)

        passed = 0
        total = len(hidden)
        exec_times = []
        compile_output = None
        try:
            for row in hidden:
                # ONLY {language, source, stdin, limits} cross to the executor —
                # row["expected_output"] is read here and used only below, never
                # passed to run_one().
                result = run_one(language, source, row.get("input") or "", limits)
                if result.compile_error:
                    compile_output = result.compile_error
                    continue  # a compile error fails every remaining case too
                exec_times.append(result.time_ms)
                tol = row.get("float_tolerance")
                expected = secrets_crypto.decrypt(row.get("expected_output") or "")
                if result.timed_out:
                    ok = False
                elif tol is not None:
                    ok = _float_match(result.stdout, expected, float(tol))
                else:
                    ok = normalize_output(result.stdout) == normalize_output(expected)
                if ok:
                    passed += 1
        except ExecUnavailable:
            # Invariant #5 — never write a submission row on a transient
            # executor outage; the kiosk auto-retries. The outer except
            # HTTPException handler below releases the idempotency reservation.
            raise HTTPException(status_code=503, detail={"retryable": True,
                                "error": "execution service unavailable"})

        avg_ms = int(sum(exec_times) / len(exec_times)) if exec_times else None
        telemetry = body.get("telemetry") or {}
        row_to_insert = {
            "exam_id":     eid,
            "teacher_id":  str(tid) if tid else None,   # Invariant #2 — JWT, not body
            "session_id":  session_id,
            "student_id":  claims.get("sid"),
            "question_id": question_id,
            "language":    language[:40],
            "test_cases_total":  total,
            "test_cases_passed": passed,
            "average_execution_ms": avg_ms,
            "memory_consumed_kb":   None,
            "source_code": source,
            "compile_output": compile_output,
            "keystroke_rhythm_variance": telemetry.get("keystroke_rhythm_variance"),
            "paste_attempts":   int(telemetry.get("paste_attempts") or 0),
            "focus_loss_count": int(telemetry.get("focus_loss_count") or 0),
        }
        # is_fully_solved is a GENERATED column — never inserted.
        await _atable("coding_submissions").insert(row_to_insert).execute()
        resp = {"passed": passed, "total": total, "average_execution_ms": avg_ms}
        await mark_idempotent(idem_key, resp)
        return resp
    except HTTPException:
        await release_idempotency(idem_key)
        raise
    except Exception as e:
        await release_idempotency(idem_key)
        logger.error("[coding_judge] error for %s/%s: %s", session_id, question_id, e)
        raise HTTPException(status_code=500, detail="Failed to judge submission")
