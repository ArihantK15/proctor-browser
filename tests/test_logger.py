"""Tests for structured JSON logging (app/logger.py).

Covers trace context propagation, the JSON formatter, per-session
logger cache, and the trace_span duration helper.
"""
import os
import sys
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import time
import pytest

# The conftest replaces sys.modules["app.logger"] with a MagicMock.
_saved_logger = sys.modules.pop("app.logger", None)

import importlib
_app_logger_spec = importlib.util.find_spec("app.logger")
_logger = importlib.util.module_from_spec(_app_logger_spec)
_app_logger_spec.loader.exec_module(_logger)

sys.modules["app.logger"] = _saved_logger


# ── TraceJsonFormatter ──────────────────────────────────────────────


class _Record:
    """Minimal LogRecord stand-in for testing the formatter."""
    def __init__(self, msg="hello", level=logging.INFO, extra=None):
        self.name = "test"
        self.msg = msg
        self.args = ()
        self.levelname = "INFO"
        self.levelno = level
        self.pathname = "/fake/path.py"
        self.filename = "path.py"
        self.module = "test"
        self.funcName = "test_func"
        self.lineno = 42
        self.created = time.time()
        self.msecs = 0
        self.relativeCreated = 0
        self.process = 123
        self.thread = 456
        self.processName = "MainProcess"
        self.threadName = "MainThread"
        self.taskName = ""
        self.exc_info = None
        self.exc_text = None
        self.stack_info = None


class TestTraceJsonFormatter:
    def test_adds_trace_context_from_contextvars(self):
        _logger.set_trace_context("req-abc", "POST", "/api/test")
        fmt = _logger.TraceJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        rec = _Record(msg="test msg")
        d = {}
        fmt.add_fields(d, rec, {})
        assert d.get("request_id") == "req-abc"
        assert d.get("method") == "POST"
        assert d.get("path") == "/api/test"

    def test_trace_context_defaults_to_empty(self):
        _logger.set_trace_context("", "", "")
        fmt = _logger.TraceJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        rec = _Record()
        d = {}
        fmt.add_fields(d, rec, {})
        assert d.get("request_id") == ""
        assert d.get("method") == ""

    def test_extra_kwargs_moved_to_root(self):
        fmt = _logger.TraceJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        rec = _Record()
        d = {}
        fmt.add_fields(d, rec, {"exam_id": "e1", "score": 85})
        assert d.get("exam_id") == "e1"
        assert d.get("score") == 85

    def test_process_log_record_removes_internal_keys(self):
        fmt = _logger.TraceJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        record = {
            "msg": "keep",
            "_private": "remove",
            "args": "remove",
            "exc_text": "remove",
            "stack_info": "remove",
            "processName": "remove",
            "threadName": "remove",
            "taskName": "remove",
        }
        result = fmt.process_log_record(record)
        assert "msg" in result
        for key in ("_private", "args", "exc_text", "stack_info",
                    "processName", "threadName", "taskName"):
            assert key not in result


# ── set_trace_context ───────────────────────────────────────────────


class TestSetTraceContext:
    def test_sets_all_vars(self):
        _logger.set_trace_context("r1", "GET", "/health")
        assert _logger.trace_request_id.get() == "r1"
        assert _logger.trace_method.get() == "GET"
        assert _logger.trace_path.get() == "/health"

    def test_clears_vars_with_empty(self):
        _logger.set_trace_context("r1", "GET", "/health")
        _logger.set_trace_context("", "", "")
        assert _logger.trace_request_id.get() == ""
        assert _logger.trace_method.get() == ""
        assert _logger.trace_path.get() == ""


# ── get_logger ──────────────────────────────────────────────────────


class TestGetLogger:
    def test_returns_logger_instance(self):
        logger = _logger.get_logger("sess-foo")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "sess-foo"

    def test_returns_same_logger_on_repeat(self):
        a = _logger.get_logger("same-session")
        b = _logger.get_logger("same-session")
        assert a is b

    def test_lru_eviction(self, monkeypatch):
        from collections import OrderedDict
        from threading import Lock
        monkeypatch.setattr(_logger, "_MAX_LOGGERS", 2)
        monkeypatch.setattr(_logger, "_logger_cache", OrderedDict())
        monkeypatch.setattr(_logger, "_cache_lock", Lock())

        _logger.get_logger("s1")
        _logger.get_logger("s2")
        _logger.get_logger("s3")  # evicts s1

        assert _logger.logging.Logger.manager.loggerDict.get("s1") is None
        assert _logger.logging.Logger.manager.loggerDict.get("s2") is not None
        assert _logger.logging.Logger.manager.loggerDict.get("s3") is not None

    def test_handler_added_only_once(self):
        logger = _logger.get_logger("unique-test")
        handler_count = len(logger.handlers)
        logger2 = _logger.get_logger("unique-test")
        assert len(logger2.handlers) == handler_count


# ── trace_span ──────────────────────────────────────


class TestTraceSpan:
    @pytest.mark.asyncio
    async def test_logs_duration_on_success(self):
        out = StringIO()
        handler = logging.StreamHandler(out)
        logger = _logger._log
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        old_handlers = list(logger.handlers)
        logger.handlers.clear()
        logger.addHandler(handler)
        try:
            async with _logger.trace_span("db.query", exam_id="e1"):
                pass
        finally:
            logger.setLevel(old_level)
            logger.handlers.clear()
            for h in old_handlers:
                logger.addHandler(h)

        out.seek(0)
        line = out.read()
        assert "db.query" in line

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        out = StringIO()
        handler = logging.StreamHandler(out)
        logger = _logger._log
        old_handlers = list(logger.handlers)
        logger.handlers.clear()
        logger.addHandler(handler)
        try:
            with pytest.raises(ValueError):
                async with _logger.trace_span("failing.op"):
                    raise ValueError("oops")
        finally:
            logger.handlers.clear()
            for h in old_handlers:
                logger.addHandler(h)

        out.seek(0)
        line = out.read()
        assert "failing.op" in line
        assert "oops" in line
