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


# ── assert_session_accessible orphan-row hardening (fail-closed) ──────────────
from fastapi import HTTPException as _HTTPException


class _FakeQ:
    def __init__(self, data): self._data = data
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    async def execute(self):
        return type("R", (), {"data": self._data})()


def _atable_factory(sessions, violations):
    def _at(table):
        if table == "exam_sessions":
            return _FakeQ(sessions)
        if table == "violations":
            return _FakeQ(violations)
        return _FakeQ([])
    return _at


_ORGA_TIDS = {"a1", "a2"}
_ADMIN_A = {"role": "admin", "org_id": "orgA", "teacher_id": "a1"}
_ORPHAN = [{"session_key": "S1", "teacher_id": "", "roll_number": "R1", "score": 9}]


@pytest.fixture
def _orphan_db(monkeypatch):
    async def _verify(tid, org_id):
        return tid in _ORGA_TIDS and org_id == "orgA"
    monkeypatch.setattr(scope_mod, "_verify_teacher_in_org", _verify)
    return monkeypatch


@pytest.mark.asyncio
async def test_orphan_violation_other_org_denied(_orphan_db):
    """Orphan row whose violation belongs to ANOTHER org → 404 (was a leak)."""
    _orphan_db.setattr(scope_mod, "_atable", _atable_factory(_ORPHAN, [{"teacher_id": "b1"}]))
    with pytest.raises(_HTTPException) as ei:
        await scope_mod.assert_session_accessible("S1", _ADMIN_A)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_orphan_no_violations_denied(_orphan_db):
    """No violation can prove org membership → 404 (old code returned it)."""
    _orphan_db.setattr(scope_mod, "_atable", _atable_factory(_ORPHAN, []))
    with pytest.raises(_HTTPException) as ei:
        await scope_mod.assert_session_accessible("S1", _ADMIN_A)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_orphan_in_org_violation_allowed(_orphan_db):
    """A violation provably in the admin's org → session returned."""
    _orphan_db.setattr(scope_mod, "_atable", _atable_factory(_ORPHAN, [{"teacher_id": "a2"}]))
    sess = await scope_mod.assert_session_accessible("S1", _ADMIN_A)
    assert sess["session_key"] == "S1"


@pytest.mark.asyncio
async def test_orphan_in_org_proof_beyond_first_row(_orphan_db):
    """limit(1) bug: first violation empty, a later one is in-org → still granted."""
    _orphan_db.setattr(scope_mod, "_atable",
                       _atable_factory(_ORPHAN, [{"teacher_id": ""}, {"teacher_id": "a1"}]))
    sess = await scope_mod.assert_session_accessible("S1", _ADMIN_A)
    assert sess["session_key"] == "S1"


@pytest.mark.asyncio
async def test_orphan_other_org_proof_beyond_first_row_denied(_orphan_db):
    """limit(1) leak: first violation empty, a LATER one ties to another org → 404."""
    _orphan_db.setattr(scope_mod, "_atable",
                       _atable_factory(_ORPHAN, [{"teacher_id": ""}, {"teacher_id": "b1"}]))
    with pytest.raises(_HTTPException) as ei:
        await scope_mod.assert_session_accessible("S1", _ADMIN_A)
    assert ei.value.status_code == 404
