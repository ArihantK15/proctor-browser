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
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# ── Set required env vars BEFORE importing app modules ──────────────
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SCREENSHOTS_DIR", "/tmp/procta_test_screenshots")
os.environ.setdefault("QUESTION_IMG_DIR", "/tmp/procta_test_qimages")

# ── Mock heavy dependencies before they're imported by app code ─────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Supabase client — shared mock referenced by ALL modules
_mock_supabase = MagicMock()
_mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.select.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

# Pre-populate sys.modules so `from .database import supabase` resolves
mock_database = MagicMock()
mock_database.supabase = _mock_supabase
mock_database.async_table = MagicMock()
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
    return mock_cache


@pytest.fixture
def event_bus_mock():
    mock_event_bus.reset_mock()
    mock_event_bus.async_publish = AsyncMock()
    return mock_event_bus


def make_student_token(roll: str = "ALICE001", tid: str = "teacher-1",
                       eid: str = "exam-1", expired: bool = False):
    """Create a valid student JWT for testing."""
    from jose import jwt as jose_jwt
    secret = os.environ["SUPABASE_JWT_SECRET"]
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll,
        "tid": tid,
        "eid": eid,
        "iat": now,
        "exp": now + timedelta(hours=-1 if expired else 10),
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def make_admin_token(teacher_id: str = "teacher-1", email: str = "prof@test.com"):
    """Create a valid admin JWT for testing."""
    from jose import jwt as jose_jwt
    secret = os.environ["SUPABASE_JWT_SECRET"]
    now = datetime.now(timezone.utc)
    payload = {
        "tid": teacher_id,
        "email": email,
        "role": "teacher",
        "exp": now + timedelta(hours=12),
        "iat": now,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def client():
    """FastAPI test client with mocked dependencies."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def student_headers():
    """Authorization headers for a student."""
    return {"Authorization": f"Bearer {make_student_token()}"}


@pytest.fixture
def admin_headers():
    """Authorization headers for a teacher/admin."""
    return {"Authorization": f"Bearer {make_admin_token()}"}
