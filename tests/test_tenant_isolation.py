"""Tenant-isolation regression guard for the org-scope spine.

These exercise app/auth/scope.py — the single point every admin read
path funnels through — with two orgs (A, B) and assert that an admin in
org A can never resolve to a teacher in org B. This is the existential
data-leak guard for a B2B exam product; it MUST stay green before any
admin read path widens from teacher_id to org scope.
"""

from __future__ import annotations

import pytest

import app.auth.scope as scope_mod


class _ScopeDB:
    """Minimal _atable() stub backed by an in-memory teachers table.

    Supports the two query shapes scope.py issues:
      • _verify_teacher_in_org: .select("id").eq("id",X).eq("org_id",Y).limit(1)
      • scope_to_teacher_ids:   .select("id").eq("org_id",Y)
    """

    def __init__(self, teachers: list[dict]):
        self._teachers = teachers

    def __call__(self, table_name: str):
        assert table_name == "teachers", f"unexpected table {table_name}"
        return _ScopeChain(self._teachers)


class _ScopeChain:
    def __init__(self, teachers: list[dict]):
        self._rows = teachers
        self._eqs: dict[str, str] = {}

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._eqs[col] = str(val)
        return self

    def limit(self, n):
        return self

    async def execute(self):
        rows = [
            {"id": t["id"]}
            for t in self._rows
            if all(str(t.get(c)) == v for c, v in self._eqs.items())
        ]
        return type("R", (), {"data": rows})()


class _Req:
    """Stub Request exposing only .query_params.get used by resolve_scope."""

    def __init__(self, teacher_id: str | None = None):
        self._params = {"teacher_id": teacher_id} if teacher_id else {}

    @property
    def query_params(self):
        return _Params(self._params)


class _Params:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=""):
        return self._d.get(key, default)


# Two orgs: A has teachers a1,a2 ; B has teacher b1.
TEACHERS = [
    {"id": "a1", "org_id": "orgA", "org_role": "admin"},
    {"id": "a2", "org_id": "orgA", "org_role": "teacher"},
    {"id": "b1", "org_id": "orgB", "org_role": "admin"},
]


@pytest.fixture
def patched_db(monkeypatch):
    monkeypatch.setattr(scope_mod, "_atable", _ScopeDB(TEACHERS))


@pytest.mark.asyncio
async def test_plain_teacher_locked_to_self(patched_db):
    teacher = {"id": "a2", "org_id": "orgA", "org_role": "teacher"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_teacher_cannot_widen_via_query_param(patched_db):
    """A plain teacher passing ?teacher_id=a1 is ignored, not honored."""
    teacher = {"id": "a2", "org_id": "orgA", "org_role": "teacher"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="a1"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_admin_sees_whole_own_org(patched_db):
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert set(tids) == {"a1", "a2"}
    assert "b1" not in tids


@pytest.mark.asyncio
async def test_admin_cannot_target_other_org_teacher(patched_db):
    """Admin in A passing ?teacher_id=b1 (org B) is silently dropped and
    falls back to org-A-wide — never resolves to b1."""
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="b1"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert "b1" not in tids
    assert set(tids) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_admin_can_narrow_to_own_org_teacher(patched_db):
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    scope = await scope_mod.resolve_scope(teacher, _Req(teacher_id="a2"))
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids == ["a2"]


@pytest.mark.asyncio
async def test_superadmin_unrestricted(patched_db):
    teacher = {"id": "x", "org_id": None, "org_role": "superadmin"}
    scope = await scope_mod.resolve_scope(teacher, _Req())
    tids = await scope_mod.scope_to_teacher_ids(scope)
    assert tids is None  # None == "no filter"
