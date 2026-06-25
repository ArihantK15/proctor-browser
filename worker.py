#!/usr/bin/env python3
"""RQ worker entrypoint — processes background jobs from the ``default`` queue.

Usage::

    # Start the worker (runs forever, polling Redis every second)
    python worker.py

Environment variables:

    REDIS_URL       redis://…  (default: redis://localhost:6379/0)
    RQ_QUEUE        queue name (default: ``default``)
    SENTRY_DSN      optional — enables Sentry error reporting
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── optional Sentry init — SAME PII scrubber as the API ─────────────
# The worker processes scoring/autosave/email jobs whose ARGUMENTS carry
# student answers, roll numbers and recipient emails/names. A bare init (no
# scrubber, frame locals ON) would ship all of that to Sentry on any job
# failure — so we reuse app.observability, exactly as app/main.py does.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from app.observability import scrub_sentry_event, SAFE_SENTRY_KWARGS
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            **SAFE_SENTRY_KWARGS,   # PII off, frame locals off, small body window
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            release=(os.environ.get("GIT_SHA") or os.environ.get("APP_VERSION") or None),
            before_send=scrub_sentry_event,
        )
        print("[worker] Sentry initialized (PII scrubber active)", flush=True)
    except Exception as e:
        print(f"[worker] Sentry init failed: {e}", flush=True)

# RQ / Redis imports after Sentry (avoid import-order side effects)
import logging
# Use SimpleWorker (no fork-per-job) so module-level state — most
# importantly the asyncio event loop maintained by app.jobs.helpers
# and the asyncpg connection pool — survives across job invocations.
# Default `Worker` forks a child process per job on POSIX, which
# means any in-process loop/pool dies after every job and the next
# job pays TCP+SCRAM handshake costs to rebuild the asyncpg pool.
#
# Trade-off: a job that hits OOM or segfaults will crash the worker
# process. Scoring/autosave jobs are well-behaved Python code, and
# docker compose's restart=unless-stopped recovers within seconds
# if a worker does die. Net win is ~5-10× scoring throughput.
from rq import SimpleWorker as Worker
from rq import Queue
from rq.job import Job
from redis import Redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("worker")

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# Multiple queues supported via comma-separated list. The 'scoring'
# queue is dedicated to /submit-exam background scoring (Fix #2) so
# heavy submit-wave bursts don't starve other jobs (email, autosave).
queue_names = [q.strip() for q in os.environ.get("RQ_QUEUE", "default,scoring").split(",") if q.strip()]

# Import job modules so the function references are available to the worker.
from app import jobs  # noqa: F401

# ── job lifecycle callbacks ──────────────────────────────────────────


def _job_success(job: Job, connection, result, *args, **kwargs):
    log.info("[done] %s args=%s result=%s", job.func_name, job.args, result)


def _job_failure(job: Job, *exc_info):
    """Log RQ job failures across RQ 1.x/2.x callback signatures.

    RQ 1.x called handlers as ``(job, connection, exc_type, exc, tb)`` while
    RQ 2.x calls them as ``(job, exc_type, exc, tb)``. Accept both so registry
    cleanup cannot crash the worker while processing older failed jobs.
    """
    if len(exc_info) == 4:
        _connection, typ, value, tb = exc_info
    elif len(exc_info) == 3:
        typ, value, tb = exc_info
    else:
        typ, value, tb = Exception, Exception(f"unexpected RQ failure callback args: {exc_info!r}"), None
    log.error(
        "[fail] %s args=%s exc=%s",
        job.func_name, job.args, value,
        exc_info=(typ, value, tb),
    )
    if SENTRY_DSN:
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("job", job.func_name)
                # NEVER ship raw job.args — they are positional values that for
                # email/scorecard/guardian jobs are recipient emails + names and
                # for scoring/autosave are student answers. The func name (tag)
                # + job id are enough to triage which job failed; the redacting
                # before_send can't help positional bare strings.
                scope.set_extra("job_id", getattr(job, "id", None))
                scope.set_extra("job_arg_count", len(job.args or ()))
                sentry_sdk.capture_exception(value)
        except Exception:
            pass
    return True


conn = Redis.from_url(
    redis_url,
    socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "10")),
    socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "600")),
    health_check_interval=int(os.environ.get("REDIS_HEALTH_CHECK_INTERVAL", "30")),
)

# ── health heartbeat ────────────────────────────────────────────────
import threading

def _heartbeat_loop():
    """Write worker:last_heartbeat to Redis every 30 seconds."""
    while True:
        try:
            conn.set("worker:last_heartbeat", str(time.time()), ex=90)
        except Exception:
            pass
        threading.Event().wait(30)

_t = threading.Thread(target=_heartbeat_loop, daemon=True)
_t.start()
log.info("worker heartbeat loop started")

if __name__ == "__main__":
    log.info("worker starting — redis=%s queues=%s", redis_url, queue_names)
    w = Worker(
        [Queue(q, connection=conn) for q in queue_names],
        connection=conn,
        exception_handlers=[_job_failure],
    )
    w.work()
