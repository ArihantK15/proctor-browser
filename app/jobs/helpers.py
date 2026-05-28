"""Job queuing helpers — enqueue_job + RQ connectivity.

Usage::

    send_result = enqueue_job(send_scorecard_email_job, session_key=...)

When ``RQ_ENABLED=1`` the function is enqueued to a Redis RQ worker and
returns ``None`` immediately.  When disabled the function runs synchronously
so tests and local dev work without Redis.
"""
import os
import logging
import asyncio
import threading
from typing import Optional, Callable, Any

log = logging.getLogger(__name__)

# ── retry policy for RQ jobs ──────────────────────────────────────────

def _retry_max() -> int:
    return int(os.environ.get("RQ_RETRY_MAX", "3"))


def _retry_intervals() -> list[int]:
    return [int(x) for x in os.environ.get("RQ_RETRY_INTERVALS", "10,60,300").split(",")]


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _rq_enabled() -> bool:
    return os.environ.get("RQ_ENABLED", "").lower() in ("1", "true", "yes")


def enqueue_job(func: Callable, *args, queue_name: str = "default", **kwargs) -> Optional[dict]:
    """Enqueue *func* to an RQ queue, or call it synchronously.

    Returns ``None`` when the job was enqueued (async), or the function's
    return dict when run synchronously.

    Retry policy is read from environment (``RQ_RETRY_MAX``,
    ``RQ_RETRY_INTERVALS``) so it can be tuned without a deploy.
    """
    if _rq_enabled():
        from rq import Queue
        from rq.job import Retry
        from redis import Redis
        q = Queue(queue_name, connection=Redis.from_url(_redis_url()))
        q.enqueue(
            func, *args, **kwargs,
            retry=Retry(max=_retry_max(), interval=_retry_intervals()),
        )
        return None
    return func(*args, **kwargs)


# ── Persistent event loop for RQ workers ────────────────────────────
# In SimpleWorker mode the worker runs all jobs in the same process,
# so a single shared event loop can host coroutines from every job.
# This is critical because asyncpg connection pools are loop-bound —
# if we created a new loop per job (the old `asyncio.run` pattern),
# the pool's TCP+SCRAM-authed connections would die with the loop
# and the next job would pay ~500ms-1s to rebuild a 20-connection
# pool. With a persistent loop, the pool lives for the worker's
# entire lifetime.

_persistent_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _get_persistent_loop() -> asyncio.AbstractEventLoop:
    """Get or lazily create the per-process persistent event loop."""
    global _persistent_loop, _loop_thread
    if _persistent_loop is not None and _persistent_loop.is_running():
        return _persistent_loop
    with _loop_lock:
        # Double-check after acquiring the lock — another thread may
        # have just created the loop while we were waiting.
        if _persistent_loop is not None and _persistent_loop.is_running():
            return _persistent_loop
        loop = asyncio.new_event_loop()
        t = threading.Thread(
            target=loop.run_forever,
            daemon=True,
            name="rq-async-loop",
        )
        t.start()
        _persistent_loop = loop
        _loop_thread = t
        log.info("[helpers] persistent asyncio event loop started for RQ jobs")
    return _persistent_loop


def _run_coro_in_sync(coro) -> Any:
    """Run an async coroutine from a sync context.

    Two paths:

    1. **RQ SimpleWorker (no running loop in this thread)**: submit
       the coroutine to a process-persistent background loop running
       in its own thread. asyncpg pools created on this loop survive
       across all job invocations — no per-job handshake overhead.
       This is the codepath for proctor-worker and
       proctor-autosave-worker containers.

    2. **uvicorn sync fallback (running loop in this thread)**: only
       hit when a sync helper is invoked from inside an async request
       handler. Spawns a one-shot thread + loop so we don't nest
       loops in the same thread. Unchanged from prior behavior.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop → submit to the persistent background loop.
        loop = _get_persistent_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=60)

    # Running loop exists → spawn a fresh thread + loop for this call.
    result: list = []
    exc: list[Exception] = []

    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result.append(loop.run_until_complete(coro))
        except Exception as e:
            exc.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()
    if exc:
        raise exc[0]
    return result[0]
