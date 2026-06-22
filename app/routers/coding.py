"""Edge Compiler — judge + test-case delivery endpoints (spec Approach A).

The server NEVER executes student code. The kiosk sandbox (WASM/JS) runs the code
and POSTs its outputs; this endpoint compares them against the SECRET expected
outputs the server holds and returns pass/fail COUNTS ONLY.

Security invariants (reviewed line-by-line — see the spec's Anti-cheat/Compliance):
  1. Hidden `expected_output` is read ONLY under system_context() for the
     comparison and is NEVER serialized to the client — the delivery endpoint
     omits it from the SELECT column list for hidden cases.
  2. `teacher_id` on coding_submissions is stamped from the JWT claim, never the
     request body.
  3. A per-question SUBMIT-ATTEMPT CAP defends against the output-oracle attack
     (flip one output, read the count delta to infer expected) — distinct from the
     per-minute rate limit.
  4. The insert is idempotent (reserve_idempotency) so a retry-queued client can't
     double-write a submission.
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
from ..services.coding_judge import judge_outputs
from ..services.idempotency import (
    reserve_idempotency, release_idempotency, mark_idempotent,
)
from .exam import _assert_student_session_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="")

DEFAULT_MAX_SUBMIT_ATTEMPTS = 10


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
        "sample": [{"idx": r["idx"], "input": r["input"],
                    "expected_output": r["expected_output"]} for r in sample_rows],
        "hidden_inputs": [{"idx": r["idx"], "input": r["input"]} for r in hidden_rows],
    }


@router.post("/api/v1/coding/judge")
@limiter.limit("30/minute")
async def coding_judge(body: dict, request: Request):
    """Judge a submission's outputs against the secret expected outputs. Returns
    `{passed, total}` only."""
    claims = require_auth(request)
    session_id = (body.get("session_id") or "").strip()
    question_id = (body.get("question_id") or "").strip()
    if not session_id or not question_id:
        raise HTTPException(status_code=400, detail="session_id and question_id required")
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")

    outputs = body.get("outputs")
    if not isinstance(outputs, list):
        raise HTTPException(status_code=400, detail="outputs must be a list")

    # Invariant #3 — per-question submit-attempt cap (output-oracle defense). Count
    # prior submissions for this (session, question); distinct from the rate limit.
    config = await _load_exam_config(str(tid or ""), exam_id=eid)
    cap = int((config or {}).get("coding_max_submit_attempts") or DEFAULT_MAX_SUBMIT_ATTEMPTS)
    prior = (await _atable("coding_submissions").select("id")
             .eq("session_id", session_id).eq("question_id", question_id).execute()).data or []
    if len(prior) >= cap:
        raise HTTPException(status_code=429, detail="Submission limit reached for this problem")

    # Invariant #4 — idempotency. Key on (session, question, outputs) so a genuine
    # new attempt isn't suppressed, but a retry double-fire writes once.
    attempt_hash = hashlib.sha256(
        json.dumps([session_id, question_id, outputs], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    idem_key = f"coding_judge:{session_id}:{question_id}:{attempt_hash}"
    acquired, cached = await reserve_idempotency(idem_key, ttl=300)
    if not acquired:
        if cached is not None:
            return cached
        raise HTTPException(status_code=409, detail="Submission already in progress")

    try:
        # Invariant #1 — hidden expected read under system_context, used only for the
        # comparison, never returned.
        with system_context():
            hidden = (await _atable("coding_test_cases")
                      .select("idx,expected_output,float_tolerance")
                      .eq("question_id", question_id).eq("visibility", "hidden")
                      .order("idx").execute()).data or []
        expected = [r["expected_output"] for r in hidden]
        tols = [r.get("float_tolerance") for r in hidden]
        result = judge_outputs(outputs, expected, tols)

        metrics = body.get("metrics") or {}
        telemetry = body.get("telemetry") or {}
        row = {
            "exam_id":     eid,
            "teacher_id":  str(tid) if tid else None,   # Invariant #2 — JWT, not body
            "session_id":  session_id,
            "student_id":  claims.get("sid"),
            "question_id": question_id,
            "language":    str(body.get("language") or "")[:40],
            "test_cases_total":  result["total"],
            "test_cases_passed": result["passed"],
            "average_execution_ms": metrics.get("average_execution_ms"),
            "memory_consumed_kb":   metrics.get("memory_consumed_kb"),
            "source_code": body.get("source") or "",
            "keystroke_rhythm_variance": telemetry.get("keystroke_rhythm_variance"),
            "paste_attempts":   int(telemetry.get("paste_attempts") or 0),
            "focus_loss_count": int(telemetry.get("focus_loss_count") or 0),
        }
        # is_fully_solved is a GENERATED column — never inserted.
        await _atable("coding_submissions").insert(row).execute()
        resp = {"passed": result["passed"], "total": result["total"]}
        await mark_idempotent(idem_key, resp)
        return resp
    except HTTPException:
        await release_idempotency(idem_key)
        raise
    except Exception as e:
        await release_idempotency(idem_key)
        logger.error("[coding_judge] error for %s/%s: %s", session_id, question_id, e)
        raise HTTPException(status_code=500, detail="Failed to judge submission")
