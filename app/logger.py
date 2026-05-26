"""Structured JSON logging with trace context propagation.

Every log line across the entire codebase automatically emits JSON
with timestamp, level, module, and the current request_id.  No changes
needed in any existing logger.info/warning/error calls.

Usage:
    from app.logger import get_logger
    logger = get_logger("my_module")
    logger.info("hello %s", name)   # → {"msg": "hello Arihant", ...}

Trace context (request_id, method, path) is propagated via contextvars
so async tasks, background jobs, and websocket handlers all carry the
same trace ID without any manual threading.

JSON format matches Datadog / ELK / Loki ingestion conventions.
"""

import json
import logging
import os
import sys
import time
import contextvars
from collections import OrderedDict
from threading import Lock

from pythonjsonlogger.jsonlogger import JsonFormatter

# ─── Trace context (propagates across async boundaries) ──────────
trace_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_request_id", default="")
trace_method: contextvars.ContextVar[str] = contextvars.ContextVar("trace_method", default="")
trace_path: contextvars.ContextVar[str] = contextvars.ContextVar("trace_path", default="")


def set_trace_context(request_id: str, method: str = "", path: str = "") -> None:
    """Set the current trace context (called once per request from middleware)."""
    trace_request_id.set(request_id)
    trace_method.set(method)
    trace_path.set(path)


# ─── JSON formatter ──────────────────────────────────────────────
_LOG_RECORD_ATTRS = frozenset(
    "args asctime created exc_info exc_text filename funcName "
    "levelname levelno lineno module msecs msg name pathname "
    "process processName relativeCreated stack_info thread threadName taskName"
    .split()
)


class TraceJsonFormatter(JsonFormatter):
    """JSON formatter that injects trace context into every record."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)

        # Inject trace context (defaults to "" to keep JSON valid)
        log_record.setdefault("request_id", trace_request_id.get() or "")
        log_record.setdefault("method", trace_method.get() or "")
        log_record.setdefault("path", trace_path.get() or "")

        # Move extra kwargs from 'message_dict' into the root of the record
        # so they appear as top-level JSON keys, not nested under 'message_dict'.
        for key, val in message_dict.items():
            if key not in log_record:
                log_record[key] = val

    def process_log_record(self, log_record):
        """Remove Python-internal keys that don't add value in JSON."""
        for key in list(log_record.keys()):
            if key.startswith("_") or key in ("args", "exc_text", "stack_info",
                                                "processName", "threadName", "taskName"):
                del log_record[key]
        return log_record


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(method)s %(path)s"
_formatter = TraceJsonFormatter(_LOG_FORMAT)

# ─── Logger cache ────────────────────────────────────────────────
_MAX_LOGGERS = 200
_logger_cache: OrderedDict[str, logging.Logger] = OrderedDict()
_cache_lock = Lock()


def _evict_lru():
    _, old_logger = _logger_cache.popitem(last=False)
    for h in old_logger.handlers[:]:
        try:
            h.close()
        except Exception:
            logger.debug("logger: handler close failed during LRU evict", exc_info=True)
        old_logger.removeHandler(h)
    logging.Logger.manager.loggerDict.pop(old_logger.name, None)


def get_logger(session_id: str) -> logging.Logger:
    """Get a per-session logger (for exam/student-specific logging)."""
    with _cache_lock:
        if session_id in _logger_cache:
            _logger_cache.move_to_end(session_id)
            return _logger_cache[session_id]

        if len(_logger_cache) >= _MAX_LOGGERS:
            _evict_lru()

        logger = logging.getLogger(session_id)
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            stdout_handler = logging.StreamHandler(sys.stdout)
            stdout_handler.setFormatter(_formatter)
            logger.addHandler(stdout_handler)

        _logger_cache[session_id] = logger
        return logger


# ─── Default root logger ─────────────────────────────────────────
_log = logging.getLogger()
_log.setLevel(logging.INFO)
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_formatter)
    _log.addHandler(_handler)

# ─── Quiet down chatty third-party loggers ──────────────────────
# `httpx` (used by supabase-py to call REST) logs every single HTTP
# request at INFO. At 5 RPS per active student × 500 students that's
# 2,500 log lines per second of pure "made a Supabase call" noise.
# Bump it to WARNING — we still see errors, we lose the firehose.
# Same for `urllib3` and `httpcore` which httpx wraps.
for _noisy in ("httpx", "httpcore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ─── Async timing helper ─────────────────────────────────────────
import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def trace_span(name: str, **extra: str):
    """Context manager that logs the duration of an async operation.

    Usage::

        async with trace_span("db.exam_config", exam_id=eid):
            config = await load_exam_config(...)

    Output (JSON)::

        {"level": "INFO", "msg": "[span] db.exam_config", "duration_ms": 42.1, "exam_id": "abc", ...}
    """
    start = time.monotonic()
    try:
        yield
    except Exception as e:
        duration = round((time.monotonic() - start) * 1000, 1)
        _log.warning("[span] %s failed after %.1fms: %s", name, duration, e,
                     extra={"duration_ms": duration, "span": name, "error": str(e), **extra})
        raise
    else:
        duration = round((time.monotonic() - start) * 1000, 1)
        _log.debug("[span] %s %.1fms", name, duration,
                   extra={"duration_ms": duration, "span": name, **extra})
