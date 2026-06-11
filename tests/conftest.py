"""
Shared fixtures and mocks for Procta unit tests.

Strategy: mock external dependencies (Supabase, Redis, filesystem) so tests
run fast, offline, and without credentials.  The goal is to verify the
business logic and edge cases found during the code audit.
"""
import asyncio
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta

# Suppress httpx's 'app' shortcut deprecation (unavoidable with current
# FastAPI test client — the 'transport' kwarg requires httpx>=0.28)
warnings.filterwarnings("ignore", message="The 'app' shortcut is now deprecated", category=DeprecationWarning)
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# ── Set required env vars BEFORE importing app modules ──────────────
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SCREENSHOTS_DIR", "/tmp/procta_test_screenshots")
os.environ.setdefault("QUESTION_IMG_DIR", "/tmp/procta_test_qimages")
os.environ.setdefault("LOG_DIR", "/tmp/procta_test_logs")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "dGVzdC1mZXJuZXQta2V5LTIzLWNoYXJzLWxvbmchISE=")

# ── Mock heavy dependencies before they're imported by app code ─────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Supabase client — shared mock referenced by ALL modules
_mock_supabase = MagicMock()
_mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.select.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

# Pre-populate sys.modules so `from .database import supabase` resolves
class _AsyncTableMock:
    """Fluent mock for _atable that returns self on every chain method
    and has an awaitable .execute()."""
    def __init__(self, data=None, count=None):
        self._data = data if data is not None else []
        self._count = count
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def neq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def gt(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def like(self, *a, **kw): return self
    def contains(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def single(self, *a, **kw): return self
    def insert(self, *a, **kw): return self
    def upsert(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def delete(self, *a, **kw): return self
    async def execute(self):
        r = MagicMock()
        r.data = self._data
        r.count = self._count
        return r

class _AsyncChainWrapper:
    """Wraps a MagicMock chain so .execute() is awaitable but all
    other attribute access passes through to the underlying mock.

    The wrapper itself is also awaitable (``await chain``) for callers
    that split the builder from execution::

        q = await _atable("t").select("a").eq("x", 1)
        rows = (await q.execute()).data
    """
    def __init__(self, chain):
        self._chain = chain
    def __await__(self):
        async def _resolve():
            return self._chain
        return _resolve().__await__()
    def __getattr__(self, name):
        attr = getattr(self._chain, name)
        if name == "execute":
            async def _async_execute():
                result = attr()
                if hasattr(result, '__await__'):
                    return await result
                return result
            return _async_execute
        if callable(attr):
            # Wrap the return value too (for chaining like .eq().select())
            def _wrapper(*a, **kw):
                return _AsyncChainWrapper(attr(*a, **kw))
            return _wrapper
        return attr

class _AtableDelegator:
    """Wrapper that delegates _atable(table) calls to the shared
    supabase mock's table() chain, but makes .execute() awaitable.
    This ensures that patching supabase.table also affects _atable."""
    def __call__(self, table_name):
        sync_chain = _mock_supabase.table(table_name)
        return _AsyncChainWrapper(sync_chain)

_mock_atable = _AtableDelegator()

mock_database = MagicMock()
mock_database.supabase = _mock_supabase
mock_database.async_table = _mock_atable
# `database_backend()` and `is_postgres_backend()` are the canonical
# way for callers to ask "which backend is active?". Tests run as if
# the supabase default is set — overriding to "postgres" is per-test.
mock_database.database_backend = lambda: "supabase"
mock_database.is_postgres_backend = lambda: False
sys.modules["app.database"] = mock_database

# Mock logger
mock_logger_mod = MagicMock()
mock_logger_mod.get_logger.return_value = MagicMock()
sys.modules["app.logger"] = mock_logger_mod

# Mock redis-dependent modules
mock_event_bus = MagicMock()
mock_event_bus.publish = MagicMock()
mock_event_bus.async_publish = AsyncMock()
mock_event_bus.subscribe = MagicMock()
sys.modules["app.event_bus"] = mock_event_bus

mock_cache = MagicMock()
mock_cache.get.return_value = None
mock_cache.set = MagicMock()
mock_cache.delete = MagicMock()
mock_cache.delete_pattern = MagicMock()
mock_cache.set_live_frame = MagicMock()
mock_cache.adelete = AsyncMock()
sys.modules["app.cache"] = mock_cache

# Mock emailer module
mock_emailer = MagicMock()
mock_emailer.send_invite_email.return_value = MagicMock(ok=True, provider_msg_id="test-msg-id")
mock_emailer.verify_webhook.side_effect = lambda body, headers: bool(headers.get("svix-signature"))
mock_emailer._reset_backend_for_tests = MagicMock()
sys.modules["app.emailer"] = mock_emailer


def shared_supabase_mock():
    """Return the shared supabase mock that ALL modules reference.

    Because every router imports supabase from dependencies (which imports
    from app.database), all code paths share this single MagicMock instance.
    Patch *this* object to affect every module at once.
    """
    return _mock_supabase

# Now we can import from the app
from fastapi.testclient import TestClient


@pytest.fixture
def supabase_mock():
    """Provides the mocked supabase client and resets it between tests."""
    _mock_supabase.reset_mock()
    return _mock_supabase


@pytest.fixture
def cache_mock():
    mock_cache.reset_mock()
    mock_cache.get.return_value = None
    mock_cache.aget = AsyncMock(return_value=None)
    mock_cache.aset = AsyncMock()
    mock_cache.adelete = AsyncMock()
    return mock_cache


@pytest.fixture
def event_bus_mock():
    mock_event_bus.reset_mock()
    mock_event_bus.async_publish = AsyncMock()
    return mock_event_bus


def make_student_token(roll: str = "ALICE001", tid: str = "teacher-1",
                       eid: str = "exam-1", expired: bool = False):
    """Create a valid student JWT for testing."""
    import jwt as jose_jwt
    from app.constants import EXAM_TOKEN_SIGNING_KEY
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll,
        "tid": tid,
        "eid": eid,
        "iat": now,
        "exp": now + timedelta(hours=-1 if expired else 10),
    }
    return jose_jwt.encode(payload, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")


def make_admin_token(teacher_id: str = "teacher-1", email: str = "prof@test.com"):
    """Create a valid admin JWT for testing."""
    import jwt as jose_jwt
    from app.constants import ADMIN_SIGNING_KEY
    now = datetime.now(timezone.utc)
    payload = {
        "tid": teacher_id,
        "email": email,
        "role": "teacher",
        "exp": now + timedelta(hours=12),
        "iat": now,
    }
    return jose_jwt.encode(payload, ADMIN_SIGNING_KEY, algorithm="HS256")


@pytest.fixture
def client():
    """FastAPI test client with mocked dependencies."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    """Disable rate limiting during tests so they don't bleed into each
    other.  The real limiter is enforced in production; in tests we
    only care about business-logic correctness."""
    from app.main import app
    from app.limiter import limiter as dep_limiter
    # Disable both limiter instances
    app.state.limiter.enabled = False
    dep_limiter.enabled = False
    yield
    app.state.limiter.enabled = True
    dep_limiter.enabled = True


@pytest.fixture
def student_headers():
    """Authorization headers for a student."""
    return {"Authorization": f"Bearer {make_student_token()}"}


@pytest.fixture
def admin_headers():
    """Authorization headers for a teacher/admin."""
    return {"Authorization": f"Bearer {make_admin_token()}"}
