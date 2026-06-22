"""Regressions for public.py unauthenticated-endpoint hardening:
input length caps (RegisterIn/DemoRequest) + real email-format validation.

All assertions reject BEFORE any DB/teacher lookup (422 from Pydantic, or 400
from the email check which runs before the teacher lookup), so no mocking needed.
"""
import os

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _register(**over):
    body = {"full_name": "Alice", "roll_number": "A1", "email": "a@b.com", "teacher_id": "t-1"}
    body.update(over)
    return client.post("/api/v1/register-student", json=body)


# ── input length caps (RegisterIn) ────────────────────────────────────

def test_register_rejects_oversized_full_name():
    assert _register(full_name="x" * 101).status_code == 422


def test_register_rejects_oversized_email():
    assert _register(email="x" * 250 + "@b.com").status_code == 422


def test_register_rejects_oversized_roll_number():
    assert _register(roll_number="R" * 65).status_code == 422


def test_register_rejects_oversized_guardian_email():
    assert _register(guardian_email="g" * 250 + "@b.com").status_code == 422


# ── real email-format validation (was just "@" in email) ──────────────

def test_register_rejects_missing_dob():
    from unittest.mock import patch
    from tests.conftest import _AsyncTableMock
    fake_teacher = {"id": "t-1", "org_role": "teacher"}
    with patch("app.routers.public._get_teacher_by_id", return_value=fake_teacher), \
         patch("app.routers.public._atable", return_value=_AsyncTableMock(data=[])):
        r = _register(date_of_birth="")
        assert r.status_code == 400, f"empty DOB -> {r.status_code}"
    with patch("app.routers.public._get_teacher_by_id", return_value=fake_teacher), \
         patch("app.routers.public._atable", return_value=_AsyncTableMock(data=[])):
        r = _register(date_of_birth=None)
        assert r.status_code == 400, f"null DOB -> {r.status_code}"


def test_register_rejects_malformed_email():
    for bad in ("not-an-email", "a@", "@b.com", "a@b", "a b@c.com"):
        r = _register(email=bad)
        assert r.status_code == 400, f"{bad!r} -> {r.status_code}"


def test_lookup_teacher_rejects_malformed_email():
    assert client.get("/api/v1/lookup-teacher?email=garbage").status_code == 400


# ── DemoRequest caps ──────────────────────────────────────────────────

def test_demo_request_rejects_oversized_message():
    r = client.post("/api/v1/demo-request", json={
        "name": "A", "email": "a@b.com", "institution": "I",
        "role": "teacher", "message": "x" * 2001,
    })
    assert r.status_code == 422
