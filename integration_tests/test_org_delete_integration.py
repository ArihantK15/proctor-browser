"""Org soft-delete / restore (gap #5) — REAL Postgres.

Proves two things a MagicMock cannot, because both hinge on actual stored
values rather than mock wiring:

  • The active-session guard genuinely matches live sessions — i.e. the status
    values delete_org queries (LIVE_STATUSES = SessionStatus.IN_PROGRESS/PAUSED,
    which the StrEnum stores as 'in_progress'/'paused') match what the DB holds.
    The original hardcoded-uppercase list matched nothing, so an org could be
    deleted out from under students mid-exam; the unit test mock returned the
    live count regardless of the status filter and never caught it.

  • restore re-activates ONLY the teachers this org-delete suspended (the
    org_suspended_at marker, phase108), leaving a teacher who was suspended on
    their own merits before the delete still suspended.
"""
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from starlette.requests import Request
from fastapi import HTTPException

from app.database import async_table
from app.routers import admin_org as org_mod
from app.routers.admin_org import delete_org, restore_org
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

SUPERADMIN = {"id": str(uuid.uuid4()), "org_role": "superadmin", "email": "super@x.test"}


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


def _req(body=None) -> Request:
    # delete_org reads its optional {"reason","force"} body via request.json(),
    # so the body must live ON the request (not a positional arg).
    raw = json.dumps(body).encode() if body is not None else b""
    headers = [(b"content-type", b"application/json")] if body is not None else []
    scope = {"type": "http", "method": "POST", "path": "/x",
             "query_string": b"", "headers": headers}
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive)


async def _org(name="O") -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert(
        {"id": oid, "name": name, "max_students": 30}).execute()
    return oid


async def _teacher(org_id, status="active", role="teacher") -> str:
    tid = str(uuid.uuid4())
    await async_table("teachers").insert({
        "id": tid, "org_id": org_id, "org_role": role, "status": status,
        "email": f"{tid[:8]}@x.test"}).execute()
    return tid


async def _session(teacher_id, status):
    await async_table("exam_sessions").insert({
        "session_key": str(uuid.uuid4()), "teacher_id": teacher_id,
        "exam_id": "ex1", "roll_number": "R1", "status": status}).execute()


async def _call(fn, org_id, body=...):
    """Invoke a router fn directly with auth/reauth/audit/cache patched out —
    we're exercising the DB cascade, not the gates (those are unit-tested)."""
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(org_mod, "require_admin", AsyncMock(return_value=SUPERADMIN)), \
             patch.object(org_mod, "require_reauth_or_403", lambda *a, **k: None), \
             patch.object(org_mod, "clear_teacher_cache", lambda *a, **k: None), \
             patch.object(org_mod, "log_admin_action", AsyncMock(return_value=None)):
            return await fn(org_id, _req(None if body is ... else body))
    finally:
        limiter.enabled = prev


async def _org_deleted_at(org_id):
    r = (await async_table("organizations").select("deleted_at")
         .eq("id", org_id).limit(1).execute()).data
    return r[0]["deleted_at"] if r else None


async def _teacher_row(tid):
    r = (await async_table("teachers").select("status,org_suspended_at")
         .eq("id", tid).limit(1).execute()).data
    return r[0] if r else None


# ── active-session guard: validates the SessionStatus value contract ─────

async def test_in_progress_session_blocks_delete():
    org = await _org()
    t = await _teacher(org)
    await _session(t, "in_progress")          # exactly what the enum stores
    with pytest.raises(HTTPException) as ei:
        await _call(delete_org, org, {"reason": "x"})
    assert ei.value.status_code == 409


async def test_force_overrides_active_session():
    org = await _org()
    t = await _teacher(org)
    await _session(t, "in_progress")
    resp = await _call(delete_org, org, {"reason": "x", "force": True})
    assert resp["ok"] is True
    assert await _org_deleted_at(org) is not None
    row = await _teacher_row(t)
    assert row["status"] == "suspended"
    assert row["org_suspended_at"] is not None


async def test_terminal_session_does_not_block():
    org = await _org()
    t = await _teacher(org)
    await _session(t, "submitted")            # terminal, not live
    resp = await _call(delete_org, org, {"reason": "x"})
    assert resp["ok"] is True
    assert await _org_deleted_at(org) is not None


# ── restore marker (gap #5 part b) ───────────────────────────────────────

async def test_restore_reactivates_only_org_suspended():
    org = await _org()
    active_t = await _teacher(org, status="active")
    abused_t = await _teacher(org, status="suspended")   # pre-existing, no marker

    await _call(delete_org, org, {"reason": "x"})
    # delete suspends the active member (with marker), leaves the abused one
    assert (await _teacher_row(active_t))["org_suspended_at"] is not None
    assert (await _teacher_row(abused_t))["org_suspended_at"] is None

    await _call(restore_org, org)
    assert await _org_deleted_at(org) is None
    assert (await _teacher_row(active_t))["status"] == "active"
    assert (await _teacher_row(active_t))["org_suspended_at"] is None
    # the independently-suspended teacher must STILL be suspended
    assert (await _teacher_row(abused_t))["status"] == "suspended"
