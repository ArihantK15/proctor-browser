"""Admin org-scope coverage for the scorecard export read paths."""
from __future__ import annotations

import asyncio

import app.repositories.sessions as sess_mod


class _CaptureQuery:
    captured = {}

    def select(self, *a, **kw): return self
    def in_(self, col, vals):
        if col == "teacher_id":
            _CaptureQuery.captured["in"] = list(vals)
        return self
    def eq(self, col, val):
        if col == "teacher_id":
            _CaptureQuery.captured.setdefault("eq", []).append(str(val))
        return self
    def order(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    async def execute(self):
        return type("R", (), {"data": []})()


def test_stream_csv_uses_in_for_multi_teacher_org(monkeypatch):
    _CaptureQuery.captured = {}
    monkeypatch.setattr(sess_mod, "_atable", lambda t: _CaptureQuery())
    async def run():
        gen = sess_mod.stream_csv_results(teacher_ids=["a1", "a2"])
        async for _ in gen:
            pass
    asyncio.run(run())
    assert _CaptureQuery.captured.get("in") == ["a1", "a2"]
