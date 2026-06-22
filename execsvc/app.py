"""FastAPI `/run` endpoint: validates input, enforces limits, returns the
result envelope. One responsibility: the HTTP contract + queueing.

This endpoint holds NO expected outputs — it only ever sees source + stdin
and returns raw execution results. Grading against secret expected outputs
happens in the trusted app orchestrator, never here.
"""

import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .languages import LANGUAGES
from .isolate_cmd import Limits
from .runner import run_in_isolate

# Service-side maxima — caller-supplied limits are clamped to these, never
# trusted outright.
MAX_CPU_MS = 10_000
MAX_WALL_MS = 15_000
MAX_MEM_MB = 512
MAX_OUTPUT_KB = 256

# One semaphore = pool size; bounds concurrent sandboxed runs on this host.
# Sized conservatively until real concurrency numbers exist (Phase 8).
POOL_SIZE = 4
_pool_semaphore = asyncio.Semaphore(POOL_SIZE)

app = FastAPI(title="execsvc")


class RunRequest(BaseModel):
    language: str
    source: str
    stdin: str = ""
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


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    if req.language.lower() not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"unknown language: {req.language}")

    limits = Limits(
        cpu_ms=_clamp(req.cpu_ms, MAX_CPU_MS),
        wall_ms=_clamp(req.wall_ms, MAX_WALL_MS),
        mem_mb=_clamp(req.mem_mb, MAX_MEM_MB),
        output_kb=_clamp(req.output_kb, MAX_OUTPUT_KB),
    )

    async with _pool_semaphore:
        result = await asyncio.to_thread(
            run_in_isolate, req.language, req.source, req.stdin, limits
        )

    return RunResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        time_ms=result.time_ms,
        timed_out=result.timed_out,
        oom=result.oom,
        compile_error=result.compile_error,
    )
