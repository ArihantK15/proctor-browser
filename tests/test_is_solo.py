"""Unit tests for the is_solo signal (two-mode dashboard gate).

Solo = a non-superadmin caller who is effectively alone: no org, or an
org with a single member. Drives whether the legacy dashboard shows any
org/admin chrome at all (spec section B, two-mode UI).
"""
from __future__ import annotations

import pytest

import app.auth.scope as scope_mod


def test_superadmin_is_never_solo():
    assert scope_mod.compute_is_solo("superadmin", None, 1) is False
    assert scope_mod.compute_is_solo("superadmin", "orgA", 5) is False


def test_no_org_is_solo():
    assert scope_mod.compute_is_solo("teacher", None, 0) is True
    assert scope_mod.compute_is_solo("admin", None, 0) is True


def test_single_member_org_is_solo():
    assert scope_mod.compute_is_solo("admin", "orgA", 1) is True
    assert scope_mod.compute_is_solo("teacher", "orgA", 1) is True


def test_multi_member_org_is_not_solo():
    assert scope_mod.compute_is_solo("admin", "orgA", 2) is False
    assert scope_mod.compute_is_solo("teacher", "orgA", 3) is False


class _CountChain:
    """Stub _atable() chain for .select(...).eq('org_id', X).execute()."""
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    async def execute(self):
        return type("R", (), {"data": self._rows})()


@pytest.mark.asyncio
async def test_org_is_solo_superadmin_short_circuits(monkeypatch):
    def _boom(_table):
        raise AssertionError("org_is_solo must not query for superadmin")
    monkeypatch.setattr(scope_mod, "_atable", _boom)
    teacher = {"id": "x", "org_id": "orgA", "org_role": "superadmin"}
    assert await scope_mod.org_is_solo(teacher) is False


@pytest.mark.asyncio
async def test_org_is_solo_no_org_short_circuits(monkeypatch):
    def _boom(_table):
        raise AssertionError("org_is_solo must not query when org_id is empty")
    monkeypatch.setattr(scope_mod, "_atable", _boom)
    teacher = {"id": "t1", "org_id": None, "org_role": "teacher"}
    assert await scope_mod.org_is_solo(teacher) is True


@pytest.mark.asyncio
async def test_org_is_solo_counts_members(monkeypatch):
    monkeypatch.setattr(scope_mod, "_atable", lambda t: _CountChain([{"id": "a1"}]))
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    assert await scope_mod.org_is_solo(teacher) is True
    monkeypatch.setattr(scope_mod, "_atable",
                        lambda t: _CountChain([{"id": "a1"}, {"id": "a2"}]))
    assert await scope_mod.org_is_solo(teacher) is False
