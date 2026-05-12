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


def enqueue_job(func: Callable, *args, **kwargs) -> Optional[dict]:
    """Enqueue *func* to the default RQ queue, or call it synchronously.

    Returns ``None`` when the job was enqueued (async), or the function's
    return dict when run synchronously.

    Retry policy is read from environment (``RQ_RETRY_MAX``,
    ``RQ_RETRY_INTERVALS``) so it can be tuned without a deploy.
    """
    if _rq_enabled():
        from rq import Queue
        from rq.job import Retry
        from redis import Redis
        q = Queue("default", connection=Redis.from_url(_redis_url()))
        q.enqueue(
            func, *args, **kwargs,
            retry=Retry(max=_retry_max(), interval=_retry_intervals()),
        )
        return None
    return func(*args, **kwargs)


def _run_coro_in_sync(coro) -> Any:
    """Run an async coroutine from a sync context.

    Handles both environments transparently:

    * **RQ worker process** – no running event loop, uses ``asyncio.run()``.
    * **uvicorn sync fallback** – a running event loop exists.  Spawns a
      daemon thread with its own loop so we never nest loops.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

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
