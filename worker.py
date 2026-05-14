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

# ── optional Sentry init (mirrors app/main.py pattern) ──────────────
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        )
        print("[worker] Sentry initialized", flush=True)
    except Exception as e:
        print(f"[worker] Sentry init failed: {e}", flush=True)

# RQ / Redis imports after Sentry (avoid import-order side effects)
import logging
from rq import Worker, Queue, Connection
from rq.job import Job
from redis import Redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("worker")

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
queue_name = os.environ.get("RQ_QUEUE", "default")

# Import job modules so the function references are available to the worker.
from app import jobs  # noqa: F401

# ── job lifecycle callbacks ──────────────────────────────────────────


def _job_success(job: Job, connection, result, *args, **kwargs):
    log.info("[done] %s args=%s result=%s", job.func_name, job.args, result)


def _job_failure(job: Job, connection, typ, value, traceback):
    log.error(
        "[fail] %s args=%s exc=%s",
        job.func_name, job.args, value,
        exc_info=(typ, value, traceback),
    )
    if SENTRY_DSN:
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("job", job.func_name)
                scope.set_extra("job_args", job.args)
                sentry_sdk.capture_exception(value)
        except Exception:
            pass


conn = Redis.from_url(redis_url)

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
    log.info("worker starting — redis=%s queue=%s", redis_url, queue_name)
    with Connection(conn):
        w = Worker(
            [Queue(queue_name)],
            exception_handlers=[_job_failure],
        )
        w.work()
