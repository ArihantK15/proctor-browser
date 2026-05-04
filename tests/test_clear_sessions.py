"""
Regression tests for /api/v1/admin/clear-live-sessions.

Bug history (April 2026):
  • Teachers reported that "Clear Sessions" wiped old completed rows but
    left live sessions behind. Root cause: every session whose last
    heartbeat was within _CLEAR_ACTIVE_WINDOW (120s) was silently
    classified as "active" and protected. With students sitting on the
    lobby page heartbeating, that meant nothing in-progress ever got
    cleared.
  • Fix: expose `include_active` as an opt-in body flag that disables
    the protection. Also accept `exam_id` so multi-exam teachers only
    wipe the exam they're viewing. Defaults preserve the old safe
    behaviour.

These tests pin down:
  1. Default call (no flags) protects heartbeating sessions — safety.
  2. include_active=True drops the protection — the core fix.
  3. exam_id scopes both the live discovery and the completed wipe.
  4. include_completed still works (scoped or unscoped).
  5. The request step surfaces stale/active/completed counts correctly
     so the dashboard can show an accurate preview.

All tests mock Supabase through the shared conftest so they run offline
in milliseconds.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock,  make_admin_token  # noqa: E402
from app.dependencies import supabase as _supabase, _cache as _cache


def _iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _sess(key: str, roll: str, hb_seconds_ago: int | None,
          exam_id: str = "exam-A", teacher_id: str = "teacher-1") -> dict:
    return {
        "session_key":    key,
        "roll_number":    roll,
        "full_name":      roll.title(),
        "started_at":     _iso_ago(3600),
        "last_heartbeat": _iso_ago(hb_seconds_ago) if hb_seconds_ago is not None else None,
        "teacher_id":     teacher_id,
        "exam_id":        exam_id,
        "status":         "in_progress",
    }


def _completed(key: str, roll: str, exam_id: str = "exam-A") -> dict:
    return {
        "session_key":  key,
        "roll_number":  roll,
        "full_name":    roll.title(),
        "submitted_at": _iso_ago(7200),
        "exam_id":      exam_id,
    }


class _SupabaseStub:
    def __init__(self, in_progress=None, completed=None, violations=None,
                 teachers=None):
        self.in_progress = in_progress or []
        self.completed = completed or []
        self.violations = violations or []
        self.teachers = teachers or [{"id": "teacher-1", "email": "p@t.com"}]
        self.deletes: list[tuple[str, dict]] = []

    def __call__(self, table_name):
        chain = MagicMock()
        chain._table = table_name
        chain._eqs: dict = {}
        chain._is_null: set = set()
        chain._op = None

        def _select(*a, **k):
            chain._op = "select"
            return chain
        def _delete(*a, **k):
            chain._op = "delete"
            return chain
        def _insert(row):
            chain._op = "insert"
            chain._insert_row = row
            return chain
        def _eq(col, val):
            chain._eqs[col] = val
            return chain
        def _is_(col, val):
            if str(val).lower() == "null":
                chain._is_null.add(col)
            return chain
        def _gte(col, val):
            chain._eqs[f"__gte_{col}"] = val
            return chain
        def _order(*a, **k): return chain
        def _limit(*a, **k): return chain

        def _execute():
            if chain._op == "delete":
                self.deletes.append((table_name, dict(chain._eqs)))
                return MagicMock(data=[])
            if table_name == "teachers":
                return MagicMock(data=self.teachers)
            if table_name == "exam_sessions":
                status = chain._eqs.get("status")
                eid    = chain._eqs.get("exam_id")
                tid    = chain._eqs.get("teacher_id")
                if status == "in_progress":
                    rows = list(self.in_progress)
                    if "teacher_id" in chain._is_null:
                        rows = []
                    elif tid == "":
                        rows = []
                    else:
                        rows = [r for r in rows if r.get("teacher_id") == tid]
                    if eid:
                        rows = [r for r in rows if r.get("exam_id") == eid]
                    return MagicMock(data=rows)
                if status == "completed":
                    rows = [r for r in self.completed]
                    if eid:
                        rows = [r for r in rows if r.get("exam_id") == eid]
                    return MagicMock(data=rows)
                return MagicMock(data=[])
            if table_name == "violations":
                return MagicMock(data=self.violations)
            if table_name == "answers":
                return MagicMock(data=[])
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.delete.side_effect = _delete
        chain.insert.side_effect = _insert
        chain.eq.side_effect = _eq
        chain.is_.side_effect = _is_
        chain.gte.side_effect = _gte
        chain.order.side_effect = _order
        chain.limit.side_effect = _limit
        chain.execute.side_effect = _execute
        return chain


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


# ─── Request step ─────────────────────────────────────────────────────

class TestRequestStepPartitioning:

    def test_active_sessions_protected_by_default(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
            _sess("S_NOHB",   "carol", hb_seconds_ago=None),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None):
            mock_table.side_effect = stub
            resp = client.post("/api/v1/admin/clear-live-sessions",
                               headers=admin_headers, json={"step": "request"})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["active_count"] == 1, d
        assert d["stale_count"] == 2, d
        assert d["completed_count"] == 0, d

    def test_include_active_dissolves_protection(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None):
            mock_table.side_effect = stub
            resp = client.post("/api/v1/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request", "include_active": True})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["active_count"] == 0, d
        assert d["stale_count"] == 2, d
        assert d["include_active"] is True

    def test_exam_id_scopes_live_discovery(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_A1", "alice", hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_A2", "bob",   hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_B1", "carol", hb_seconds_ago=999, exam_id="exam-B"),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None):
            mock_table.side_effect = stub
            resp = client.post("/api/v1/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request", "exam_id": "exam-A"})
        d = resp.json()
        preview_rolls = {p["roll_number"] for p in d["preview"]}
        assert preview_rolls == {"alice", "bob"}, preview_rolls
        assert d["stale_count"] == 2, d

    def test_exam_id_scopes_completed_sweep(self, client, admin_headers):
        stub = _SupabaseStub(
            in_progress=[],
            completed=[
                _completed("C_A1", "alice", exam_id="exam-A"),
                _completed("C_B1", "bob",   exam_id="exam-B"),
                _completed("C_B2", "carol", exam_id="exam-B"),
            ],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None):
            mock_table.side_effect = stub
            resp = client.post("/api/v1/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request",
                                     "include_completed": True,
                                     "exam_id": "exam-B"})
        d = resp.json()
        assert d["completed_count"] == 2, d
        completed_rolls = {p["roll_number"] for p in d["completed_preview"]}
        assert completed_rolls == {"bob", "carol"}

    def test_confirm_requires_correct_ack_and_token(self, client, admin_headers):
        stub = _SupabaseStub()
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None):
            mock_table.side_effect = stub
            r1 = client.post("/api/v1/admin/clear-live-sessions",
                             headers=admin_headers,
                             json={"step": "confirm", "token": "x", "ack": "YES"})
            r2 = client.post("/api/v1/admin/clear-live-sessions",
                             headers=admin_headers,
                             json={"step": "confirm", "token": "bad-token", "ack": "DELETE"})
        assert r1.status_code == 400
        assert "DELETE" in r1.json()["detail"]
        assert r2.status_code == 400


# ─── Confirm step ─────────────────────────────────────────────────────

class TestConfirmStepDeletes:

    def _request_then_confirm(self, client, admin_headers, stub, body_extra):
        req = client.post("/api/v1/admin/clear-live-sessions",
                          headers=admin_headers,
                          json={"step": "request", **body_extra})
        assert req.status_code == 200, req.text
        token = req.json()["token"]
        return client.post("/api/v1/admin/clear-live-sessions",
                           headers=admin_headers,
                           json={"step": "confirm", "token": token,
                                 "ack": "DELETE", **body_extra})

    def test_default_confirm_skips_active_sessions(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None), \
             patch("app.dependencies.Path") as mock_path:
            mock_table.side_effect = stub
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(client, admin_headers, stub, {})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["skipped_active"] == 1, d
        deleted_session_keys = [
            f["session_key"] for (t, f) in stub.deletes
            if t == "exam_sessions" and "session_key" in f
        ]
        assert "S_OLD" in deleted_session_keys
        assert "S_RECENT" not in deleted_session_keys

    def test_include_active_deletes_everything_inprogress(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None), \
             patch("app.dependencies.Path") as mock_path:
            mock_table.side_effect = stub
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(
                client, admin_headers, stub,
                {"include_active": True},
            )
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["skipped_active"] == 0, d
        deleted_session_keys = [
            f["session_key"] for (t, f) in stub.deletes
            if t == "exam_sessions" and "session_key" in f
        ]
        assert "S_RECENT" in deleted_session_keys
        assert "S_OLD" in deleted_session_keys

    def test_exam_id_scoped_confirm_leaves_other_exams_alone(self, client, admin_headers):
        stub = _SupabaseStub(in_progress=[
            _sess("S_A1", "alice", hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_B1", "bob",   hb_seconds_ago=999, exam_id="exam-B"),
        ])
        with patch.object(shared_supabase_mock(), "table") as mock_table, \
             patch("app.dependencies._cache", None), \
             patch("app.dependencies.Path") as mock_path:
            mock_table.side_effect = stub
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(
                client, admin_headers, stub,
                {"exam_id": "exam-A"},
            )
        assert resp.status_code == 200, resp.text
        deleted_session_keys = [
            f["session_key"] for (t, f) in stub.deletes
            if t == "exam_sessions" and "session_key" in f
        ]
        assert "S_A1" in deleted_session_keys
        assert "S_B1" not in deleted_session_keys
