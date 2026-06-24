"""FastAPI `/run` endpoint: validates input, enforces limits, returns the
result envelope. One responsibility: the HTTP contract + queueing.

This endpoint holds NO expected outputs — it only ever sees source + stdin
and returns raw execution results. Grading against secret expected outputs
happens in the trusted app orchestrator, never here.
"""

import asyncio
import hmac
import logging
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .languages import LANGUAGES
from .isolate_cmd import Limits
from .runner import run_in_isolate

_log = logging.getLogger("execsvc")

# Service-side maxima — caller-supplied limits are clamped to these, never
# trusted outright.
MAX_CPU_MS = 10_000
MAX_WALL_MS = 15_000
MAX_MEM_MB = 512
MAX_OUTPUT_KB = 256
# Cap the payload the executor will accept so a malformed/hostile caller can't
# OOM the host with a giant source/stdin before isolate even runs. The trusted
# app bounds submissions well below this; this is defense-in-depth.
MAX_SOURCE_CHARS = 256 * 1024
MAX_STDIN_CHARS = 256 * 1024

# Pool size bounds concurrent sandboxed runs on this host. Sized conservatively
# until real concurrency numbers exist (Phase 8).
POOL_SIZE = int(os.environ.get("EXEC_POOL_SIZE", "4"))

# Shared-secret auth. The app sends `Authorization: Bearer <EXEC_SERVICE_AUTH>`.
# Enforced whenever the secret is configured. When it is NOT set we log LOUDLY
# rather than silently accepting unauthenticated code-execution requests — a
# networked deployment MUST set it (the executor runs untrusted code).
EXEC_SERVICE_AUTH = os.environ.get("EXEC_SERVICE_AUTH", "")
if not EXEC_SERVICE_AUTH:
    _log.critical(
        "EXEC_SERVICE_AUTH is not set — /run is UNAUTHENTICATED. Set it (and a "
        "private-network bind) in any real deployment; the executor runs "
        "untrusted code.")

# Box-id pool: isolate sandboxes are keyed by box-id, and concurrent runs MUST
# use DISTINCT boxes — two runs sharing /var/local/lib/isolate/<id>/box clobber
# each other's source/output, silently corrupting results (for an exam: wrong
# scores). One id per slot; the queue also bounds concurrency. Populated at
# import so it works whether or not FastAPI startup events fire (e.g. a plain
# TestClient(app) without a context manager).
_box_ids: "asyncio.Queue[int]" = asyncio.Queue()
for _i in range(POOL_SIZE):
    _box_ids.put_nowait(_i)

app = FastAPI(title="execsvc")


class RunRequest(BaseModel):
    language: str
    source: str = Field(max_length=MAX_SOURCE_CHARS)
    stdin: str = Field(default="", max_length=MAX_STDIN_CHARS)
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int


class RunResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int
    timed_out: bool
    oom: bool
    compile_error: Optional[str] = None


def _clamp(value: int, maximum: int) -> int:
    return max(1, min(value, maximum))


def _check_auth(authorization: Optional[str]) -> None:
    """Constant-time bearer check. No-op when EXEC_SERVICE_AUTH is unset (dev/
    test; warned loudly at import). Raises 401 on a missing/wrong token."""
    if not EXEC_SERVICE_AUTH:
        return
    expected = f"Bearer {EXEC_SERVICE_AUTH}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health() -> dict:
    """Liveness + pool visibility for the deploy smoke test. No auth — carries
    no data and runs nothing."""
    return {"ok": True, "boxes_free": _box_ids.qsize(), "pool": POOL_SIZE}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    if req.language.lower() not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"unknown language: {req.language}")

    limits = Limits(
        cpu_ms=_clamp(req.cpu_ms, MAX_CPU_MS),
        wall_ms=_clamp(req.wall_ms, MAX_WALL_MS),
        mem_mb=_clamp(req.mem_mb, MAX_MEM_MB),
        output_kb=_clamp(req.output_kb, MAX_OUTPUT_KB),
    )

    # Acquire a unique sandbox box for this run. Release it only when the
    # sandbox thread TRULY finishes — tied to the task's completion, not a
    # try/finally. A finally would return the box on coroutine cancellation
    # (orchestrator timeout / client disconnect) while the un-cancellable
    # to_thread is STILL running isolate on box_id; a later run would then grab
    # the same box and collide (the silent-wrong-score class this pool prevents).
    # shield() lets the in-flight run keep going (and clean up its own box) even
    # when this request is cancelled.
    box_id = await _box_ids.get()
    task = asyncio.ensure_future(
        asyncio.to_thread(run_in_isolate, req.language, req.source, req.stdin, limits, box_id)
    )
    task.add_done_callback(lambda _t: _box_ids.put_nowait(box_id))
    result = await asyncio.shield(task)

    return RunResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        time_ms=result.time_ms,
        timed_out=result.timed_out,
        oom=result.oom,
        compile_error=result.compile_error,
    )
