"""Org-admin dashboard views (student count + exam roll-up) — REAL Postgres.

Two fixes that only a real DB can prove, because both hinge on cross-table
joins/scoping a MagicMock can't model:

  • Org student count must run through the teacher (students JOIN teachers ON
    teacher_id WHERE teachers.org_id = X), NOT students.org_id — public
    self-registration writes students with a NULL org_id, so the old
    `students WHERE org_id = X` reported 0.

  • The exam selector must roll up across the org (resolve_scope), so an admin
    sees every teacher's exams and ?teacher_id= narrows to one — "teacher first,
    then exam". It was hardcoded to the caller's own teacher_id.
"""
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from starlette.requests import Request

from app.database import async_table
from app.routers import admin_org as org_mod
from app.routers import admin_exams as exams_mod
from app.routers.admin_org import _count_org_students
from app.routers.admin_exams import list_exams
from app.limiter import limiter

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _org(cap=500) -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert({
        "id": oid, "name": "O", "max_students": cap}).execute()
    return oid


async def _teacher(org_id, role="teacher") -> str:
    tid = str(uuid.uuid4())
    await async_table("teachers").insert({
        "id": tid, "org_id": org_id, "org_role": role,
        "email": f"{tid[:8]}@x.test"}).execute()
    return tid


async def _student(teacher_id, roll, *, org_id=None):
    row = {"roll_number": roll, "full_name": roll, "teacher_id": teacher_id,
           "email": f"{roll}@x.test"}
    if org_id is not None:
        row["org_id"] = org_id
    await async_table("students").insert(row).execute()


async def _exam(teacher_id, exam_id, title):
    await async_table("exam_config").insert({
        "teacher_id": teacher_id, "exam_id": exam_id, "exam_title": title,
        "duration_minutes": 60}).execute()


def _req(query: str = "") -> Request:
    scope = {"type": "http", "method": "GET", "path": "/api/v1/admin/exams",
             "query_string": query.encode(), "headers": []}
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request(scope, receive)


async def _list_exams_as(teacher: dict, query: str = "") -> list:
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(exams_mod, "require_admin", AsyncMock(return_value=teacher)):
            resp = await list_exams(_req(query))
        return resp["exams"]
    finally:
        limiter.enabled = prev


# ── Student count (#3) ────────────────────────────────────────────────

async def test_count_includes_students_with_null_org_id():
    org = await _org()
    t = await _teacher(org)
    # Public self-registration path: org_id is NULL, only teacher_id links them.
    await _student(t, "R1")
    await _student(t, "R2")
    await _student(t, "R3", org_id=org)   # admin-import path also counts
    # The old `students WHERE org_id = X` would have returned 1 (only R3).
    assert await _count_org_students(org) == 3


async def test_count_is_org_wide_across_teachers():
    org = await _org()
    t_a = await _teacher(org)
    t_b = await _teacher(org)
    await _student(t_a, "A1")
    await _student(t_b, "B1")
    await _student(t_b, "B2")
    assert await _count_org_students(org) == 3


async def test_count_excludes_other_orgs():
    org1, org2 = await _org(), await _org()
    t1, t2 = await _teacher(org1), await _teacher(org2)
    await _student(t1, "X1")
    await _student(t2, "Y1")
    await _student(t2, "Y2")
    assert await _count_org_students(org1) == 1
    assert await _count_org_students(org2) == 2


# ── Exam roll-up (#1) ─────────────────────────────────────────────────

async def test_org_admin_sees_all_teachers_exams():
    org = await _org()
    admin = await _teacher(org, role="admin")
    colleague = await _teacher(org)
    await _exam(admin, "ex-admin", "Admin Exam")
    await _exam(colleague, "ex-colleague", "Colleague Exam")

    admin_teacher = {"id": admin, "org_id": org, "org_role": "admin"}
    exams = await _list_exams_as(admin_teacher)
    ids = {e["exam_id"] for e in exams}
    assert ids == {"ex-admin", "ex-colleague"}   # rolls up across the org


async def test_teacher_id_filter_narrows_exam_list():
    org = await _org()
    admin = await _teacher(org, role="admin")
    colleague = await _teacher(org)
    await _exam(admin, "ex-admin", "Admin Exam")
    await _exam(colleague, "ex-colleague", "Colleague Exam")

    admin_teacher = {"id": admin, "org_id": org, "org_role": "admin"}
    exams = await _list_exams_as(admin_teacher, query=f"teacher_id={colleague}")
    ids = {e["exam_id"] for e in exams}
    assert ids == {"ex-colleague"}               # teacher-first narrowing


async def test_plain_teacher_sees_only_own_exams():
    org = await _org()
    admin = await _teacher(org, role="admin")
    plain = await _teacher(org)
    await _exam(admin, "ex-admin", "Admin Exam")
    await _exam(plain, "ex-plain", "Plain Exam")

    plain_teacher = {"id": plain, "org_id": org, "org_role": "teacher"}
    # A plain teacher is locked to their own id even if they pass ?teacher_id=
    exams = await _list_exams_as(plain_teacher, query=f"teacher_id={admin}")
    ids = {e["exam_id"] for e in exams}
    assert ids == {"ex-plain"}
