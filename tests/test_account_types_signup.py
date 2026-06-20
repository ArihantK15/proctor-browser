"""Account-types signup: solo teacher vs organization (manager-only admin).

Covers Phase 1 of the account-types plan:
  - TeacherSignupIn.account_type field (default solo)
  - _create_teacher_signup_postgres_tx branches role + sets owner_teacher_id
  - is_billing_owner helper + its exposure on the profile payload

These are pure-unit tests (mocked asyncpg conn / mocked supabase chain); the
real-Postgres end-to-end shapes live in
integration_tests/test_signup_account_types_integration.py.
"""
import os
import sys

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Task 2: account_type on the signup model
# ---------------------------------------------------------------------------

def test_signup_model_defaults_to_solo():
    from app.models import TeacherSignupIn
    m = TeacherSignupIn(full_name="A", email="a@x.com", org_name="A", password="Str0ng!Passw0rd")
    assert m.account_type == "solo"


def test_signup_model_accepts_org():
    from app.models import TeacherSignupIn
    m = TeacherSignupIn(full_name="A", email="a@x.com", org_name="A",
                        password="Str0ng!Passw0rd", account_type="org")
    assert m.account_type == "org"


# ---------------------------------------------------------------------------
# Task 3: branch _create_teacher_signup_postgres_tx on account_type
#
# Reuses the fake asyncpg conn/pool harness style from
# tests/test_signup_transaction.py, but records the org-owner UPDATE so we can
# assert the role + owner wiring.
# ---------------------------------------------------------------------------

class _FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


class _FakeConn:
    def __init__(self):
        self.calls = []
        self.teacher_role = None
        self.owner_update = None  # (owner_teacher_id, org_id)

    def transaction(self):
        return _FakeTransaction(self)

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        if "INSERT INTO organizations" in sql:
            return {"id": params[0], "name": params[1], "slug": params[2],
                    "max_students": params[3]}
        if "INSERT INTO teachers" in sql:
            # org_role is now passed as a positional param (no longer a literal);
            # capture whatever role the caller chose so the test can assert it.
            role = None
            for p in params:
                if p in ("admin", "teacher"):
                    role = p
                    break
            self.teacher_role = role
            return {"id": params[0], "email": params[1], "full_name": params[2],
                    "supabase_uid": params[3], "org_id": params[4],
                    "org_role": role, "password_hash": None,
                    "auth_provider": "local", "password_changed_at": None}
        return None

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))
        if "UPDATE organizations" in sql and "owner_teacher_id" in sql:
            self.owner_update = params
        return "OK"


async def _fake_get_pool(conn):
    return _FakePool(conn)


def _run_tx(monkeypatch, *, account_type):
    from app.routers import auth
    from app import postgres_table

    conn = _FakeConn()
    monkeypatch.setattr(postgres_table, "get_pool", lambda: _fake_get_pool(conn))
    teacher, org_id, _exam = asyncio.run(
        auth._create_teacher_signup_postgres_tx(
            email="new@test.com", name="New Teacher", org_name="New Org",
            slug="new-org", supabase_uid="uid-1", password_hash="hash-1",
            account_type=account_type,
        )
    )
    return conn, teacher, org_id


def test_solo_signup_makes_teacher_owner(monkeypatch):
    conn, teacher, org_id = _run_tx(monkeypatch, account_type="solo")
    assert teacher["org_role"] == "teacher"
    # owner_teacher_id set on the org to the freshly-created teacher
    assert conn.owner_update is not None
    owner_id, updated_org = conn.owner_update
    assert str(owner_id) == str(teacher["id"])
    assert str(updated_org) == str(org_id)


def test_org_signup_makes_manager_admin(monkeypatch):
    conn, teacher, org_id = _run_tx(monkeypatch, account_type="org")
    assert teacher["org_role"] == "admin"
    assert conn.owner_update is not None
    owner_id, updated_org = conn.owner_update
    assert str(owner_id) == str(teacher["id"])
    assert str(updated_org) == str(org_id)


def test_signup_defaults_solo_when_account_type_omitted(monkeypatch):
    """Back-compat: existing callers that don't pass account_type get solo."""
    from app.routers import auth
    from app import postgres_table

    conn = _FakeConn()
    monkeypatch.setattr(postgres_table, "get_pool", lambda: _fake_get_pool(conn))
    teacher, _org, _exam = asyncio.run(
        auth._create_teacher_signup_postgres_tx(
            email="d@test.com", name="D", org_name="D Org", slug="d-org",
            supabase_uid="uid-d", password_hash="h",
        )
    )
    assert teacher["org_role"] == "teacher"


# ---------------------------------------------------------------------------
# Task 4: is_billing_owner helper + profile exposure
# ---------------------------------------------------------------------------

def test_is_billing_owner_true_when_teacher_owns_org(monkeypatch):
    from app.auth import scope

    tid = "11111111-1111-1111-1111-111111111111"
    oid = "22222222-2222-2222-2222-222222222222"

    async def _fake_owner(org_id):
        assert str(org_id) == oid
        return tid

    monkeypatch.setattr(scope, "_org_owner_teacher_id", _fake_owner)
    teacher = {"id": tid, "org_id": oid, "org_role": "teacher"}
    assert asyncio.run(scope.is_billing_owner(teacher)) is True


def test_is_billing_owner_false_for_invited_teacher(monkeypatch):
    from app.auth import scope

    owner = "11111111-1111-1111-1111-111111111111"
    invited = "33333333-3333-3333-3333-333333333333"
    oid = "22222222-2222-2222-2222-222222222222"

    async def _fake_owner(org_id):
        return owner

    monkeypatch.setattr(scope, "_org_owner_teacher_id", _fake_owner)
    teacher = {"id": invited, "org_id": oid, "org_role": "teacher"}
    assert asyncio.run(scope.is_billing_owner(teacher)) is False


def test_is_billing_owner_false_without_org():
    from app.auth import scope
    assert asyncio.run(scope.is_billing_owner({"id": "x", "org_id": None})) is False


def test_is_billing_owner_false_for_superadmin(monkeypatch):
    from app.auth import scope
    # Superadmin is cross-org tooling; never a per-org billing owner.
    teacher = {"id": "x", "org_id": "y", "org_role": "superadmin"}
    assert asyncio.run(scope.is_billing_owner(teacher)) is False
