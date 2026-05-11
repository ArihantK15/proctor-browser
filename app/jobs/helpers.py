"""Job queuing helpers — enqueue_job + RQ connectivity."""
import os
import logging
from typing import Optional, Callable

log = logging.getLogger(__name__)


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _rq_enabled() -> bool:
    return os.environ.get("RQ_ENABLED", "").lower() in ("1", "true", "yes")


def enqueue_job(func: Callable, *args, **kwargs) -> Optional[dict]:
    if _rq_enabled():
        from rq import Queue
        from redis import Redis
        q = Queue("default", connection=Redis.from_url(_redis_url()))
        q.enqueue(func, *args, **kwargs)
        return None
    return func(*args, **kwargs)
