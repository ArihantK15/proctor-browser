"""Thin HTTP client: the app orchestrator's boundary to the execution
service (`execsvc`). One responsibility — POST {language,source,stdin,limits}
to EXEC_SERVICE_URL/run and parse the result envelope.

SECURITY INVARIANT: this client NEVER sends an expected_output / secret to
the executor. The executor only ever sees source + stdin (see execsvc/app.py
docstring) and the orchestrator (app/routers/coding.py) is the only place
that holds expected outputs — comparison happens there, never here.

The execution service is a separate trust zone reachable only over a
private link; EXEC_SERVICE_AUTH is the single shared secret authenticating
the app to it (not a per-user credential — the executor has no concept of
users).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

EXEC_SERVICE_URL = os.environ.get("EXEC_SERVICE_URL", "http://127.0.0.1:8800").rstrip("/")
EXEC_SERVICE_AUTH = os.environ.get("EXEC_SERVICE_AUTH", "")
# Hard client-side timeout. Must comfortably exceed the service's own
# wall-clock cap (execsvc MAX_WALL_MS = 15s) plus queueing slack, or a slow
# (not dead) executor would look "unavailable" to us needlessly.
EXEC_CLIENT_TIMEOUT = float(os.environ.get("EXEC_CLIENT_TIMEOUT", "20"))


class ExecUnavailable(Exception):
    """Raised when the execution service cannot be reached or returns a
    5xx. Callers (the coding router) translate this into a 503 retryable
    response — never a silent fail and never a written submission row."""


@dataclass(frozen=True)
class ExecLimits:
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int
    timed_out: bool
    oom: bool
    compile_error: Optional[str]


def _post(url: str, json: dict, headers: dict, timeout: float):
    """Isolated so tests can patch a single call site instead of mocking
    httpx internals."""
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, json=json, headers=headers)


def run_one(language: str, source: str, stdin: str, limits: ExecLimits) -> ExecResult:
    """POST one run to the execution service and return its result.

    Raises ExecUnavailable on connection failure, timeout, or a 5xx — the
    caller decides the retry/HTTP-status policy (Task 5.5: 503 retryable).
    """
    payload = {
        "language": language,
        "source": source,
        "stdin": stdin,
        "cpu_ms": limits.cpu_ms,
        "wall_ms": limits.wall_ms,
        "mem_mb": limits.mem_mb,
        "output_kb": limits.output_kb,
    }
    headers = {"Authorization": f"Bearer {EXEC_SERVICE_AUTH}"}

    try:
        resp = _post(f"{EXEC_SERVICE_URL}/run", json=payload, headers=headers,
                     timeout=EXEC_CLIENT_TIMEOUT)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
        raise ExecUnavailable(f"execution service unreachable: {e}") from e

    if resp.status_code >= 500:
        raise ExecUnavailable(f"execution service error {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        # A 4xx (e.g. unknown language) is a client-side bug, not a
        # transient-availability problem — surface it distinctly.
        raise ValueError(f"execution service rejected request ({resp.status_code}): {resp.text[:300]}")

    body = resp.json()
    return ExecResult(
        stdout=body.get("stdout", ""),
        stderr=body.get("stderr", ""),
        exit_code=body.get("exit_code", -1),
        time_ms=body.get("time_ms", 0),
        timed_out=bool(body.get("timed_out", False)),
        oom=bool(body.get("oom", False)),
        compile_error=body.get("compile_error"),
    )
