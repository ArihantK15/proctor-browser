#!/usr/bin/env python3
"""RQ worker entrypoint — processes background jobs from the ``default`` queue.

Usage:

    # Start the worker (runs forever, polling Redis every second)
    python worker.py

Environment variables:

    REDIS_URL   redis://…  (default: redis://localhost:6379/0)
    RQ_QUEUE    queue name (default: ``default``)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rq import Worker, Queue, Connection
from redis import Redis

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
queue_name = os.environ.get("RQ_QUEUE", "default")

# Import job modules so the function references are available to the worker.
from app import jobs  # noqa: F401

conn = Redis.from_url(redis_url)

if __name__ == "__main__":
    with Connection(conn):
        w = Worker([Queue(queue_name)])
        w.work()
