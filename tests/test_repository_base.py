"""Fixtures for the typed repository base (repositories/base.py).

QueryBuilder accumulates filters and replays them onto the underlying
_atable in execute(); a mistranslation (wrong op, dropped desc flag, etc.)
would silently return wrong rows. We pin the QueryResult wrapper semantics
and the full filter→underlying-call translation via a recording table.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.repositories import base
from app.repositories.base import QueryResult, Repository


# ── QueryResult wrapper ──────────────────────────────────────────────

def test_query_result_first_and_empty():
    assert QueryResult(data=[{"id": 1}, {"id": 2}]).first == {"id": 1}
    assert QueryResult(data=[]).first is None
    assert QueryResult(data=[]).empty is True
    assert QueryResult(data=[{"id": 1}]).empty is False


def test_query_result_bool_and_len():
    assert bool(QueryResult(data=[{"id": 1}])) is True
    assert bool(QueryResult(data=[])) is False
    assert len(QueryResult(data=[1, 2, 3])) == 3


# ── recording table to capture translation ──────────────────────────

class _Rec:
    def __init__(self, data):
        self.calls = []
        self._data = data

    def __getattr__(self, name):
        if name == "execute":
            async def _ex():
                self.calls.append(("execute", (), {}))
                r = MagicMock()
                r.data = self._data
                r.count = 7
                return r
            return _ex

        def _m(*a, **k):
            self.calls.append((name, a, k))
            return self
        return _m


class _Repo(Repository):
    table = "things"


@pytest.fixture
def rec(monkeypatch):
    r = _Rec([{"id": "x"}])
    monkeypatch.setattr(base, "_atable", lambda name: r)
    return r


@pytest.mark.asyncio
async def test_select_filters_translate(rec):
    res = await (_Repo().select("a,b")
                 .eq("x", 1).neq("y", 2).gt("g", 3).gte("ge", 4)
                 .lt("l", 5).lte("le", 6).in_("z", [1, 2])
                 .order("created", desc=True).limit(10).offset(5)
                 .maybe_single().execute())
    assert isinstance(res, QueryResult)
    assert res.data == [{"id": "x"}]
    assert res.count == 7
    names = [c[0] for c in rec.calls]
    assert names[0] == "select" and rec.calls[0][1] == ("a,b",)
    assert ("eq", ("x", 1), {}) in rec.calls
    assert ("neq", ("y", 2), {}) in rec.calls
    assert ("in_", ("z", [1, 2]), {}) in rec.calls
    assert ("order", ("created",), {"desc": True}) in rec.calls
    assert ("limit", (10,), {}) in rec.calls
    assert ("offset", (5,), {}) in rec.calls
    assert ("maybe_single", (), {}) in rec.calls
    assert names[-1] == "execute"


@pytest.mark.asyncio
async def test_insert_passes_payload(rec):
    await _Repo().insert({"name": "n"}).execute()
    assert ("insert", ({"name": "n"},), {}) in rec.calls


@pytest.mark.asyncio
async def test_update_passes_payload(rec):
    await _Repo().update({"name": "n"}).eq("id", "1").execute()
    assert ("update", ({"name": "n"},), {}) in rec.calls
    assert ("eq", ("id", "1"), {}) in rec.calls


@pytest.mark.asyncio
async def test_delete_op(rec):
    await _Repo().delete().eq("id", "1").execute()
    assert ("delete", (), {}) in rec.calls


@pytest.mark.asyncio
async def test_get_one_returns_first(rec):
    row = await _Repo().get_one(roll_number="R1")
    assert row == {"id": "x"}
    assert ("eq", ("roll_number", "R1"), {}) in rec.calls


@pytest.mark.asyncio
async def test_upsert_passes_on_conflict(rec):
    await _Repo().upsert({"id": "1", "v": 2}, on_conflict="id")
    assert ("upsert", ({"id": "1", "v": 2},), {"on_conflict": "id"}) in rec.calls
