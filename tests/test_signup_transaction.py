from __future__ import annotations

import asyncio

import pytest


class _FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.transaction_entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.rolled_back = True
        else:
            self.conn.committed = True
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
    def __init__(self, *, fail_on_execute: int | None = None):
        self.calls = []
        self.fail_on_execute = fail_on_execute
        self.execute_count = 0
        self.transaction_entered = False
        self.committed = False
        self.rolled_back = False

    def transaction(self):
        return _FakeTransaction(self)

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        if "INSERT INTO organizations" in sql:
            return {
                "id": params[0],
                "name": params[1],
                "slug": params[2],
                "max_students": params[3],
            }
        if "INSERT INTO teachers" in sql:
            return {
                "id": params[0],
                "email": params[1],
                "full_name": params[2],
                "supabase_uid": params[3],
                "org_id": params[4],
                "org_role": "admin",
                "password_hash": params[5],
                "auth_provider": "local",
                "password_changed_at": params[6],
            }
        return None

    async def execute(self, sql, *params):
        self.execute_count += 1
        self.calls.append(("execute", sql, params))
        if self.fail_on_execute == self.execute_count:
            raise RuntimeError("forced insert failure")
        return "INSERT 0 1"


async def _fake_get_pool(conn):
    return _FakePool(conn)


def test_postgres_signup_transaction_creates_required_rows(monkeypatch):
    from app.routers import auth
    from app import postgres_table

    conn = _FakeConn()
    monkeypatch.setattr(postgres_table, "get_pool", lambda: _fake_get_pool(conn))

    teacher, org_id, default_exam_id = asyncio.run(
        auth._create_teacher_signup_postgres_tx(
            email="new@test.com",
            name="New Teacher",
            org_name="New Org",
            slug="new-org",
            supabase_uid="uid-1",
            password_hash="hash-1",
        )
    )

    assert conn.transaction_entered is True
    assert conn.committed is True
    assert conn.rolled_back is False
    assert teacher["email"] == "new@test.com"
    assert teacher["org_id"] == org_id
    assert default_exam_id
    assert any("INSERT INTO organizations" in call[1] for call in conn.calls)
    assert any("INSERT INTO subscriptions" in call[1] for call in conn.calls)
    assert any("INSERT INTO teachers" in call[1] for call in conn.calls)
    assert any("INSERT INTO exam_config" in call[1] for call in conn.calls)


def test_postgres_signup_transaction_rolls_back_on_late_failure(monkeypatch):
    from app.routers import auth
    from app import postgres_table

    conn = _FakeConn(fail_on_execute=2)
    monkeypatch.setattr(postgres_table, "get_pool", lambda: _fake_get_pool(conn))

    with pytest.raises(RuntimeError, match="forced insert failure"):
        asyncio.run(
            auth._create_teacher_signup_postgres_tx(
                email="new@test.com",
                name="New Teacher",
                org_name="New Org",
                slug="new-org",
                supabase_uid="uid-1",
                password_hash="hash-1",
            )
        )

    assert conn.transaction_entered is True
    assert conn.committed is False
    assert conn.rolled_back is True
