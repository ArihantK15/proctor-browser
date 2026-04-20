"""
Regression tests for /api/admin/clear-live-sessions.

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import make_admin_token  # noqa: E402


def _iso_ago(seconds: int) -> str:
    """ISO-8601 timestamp N seconds in the past (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _sess(key: str, roll: str, hb_seconds_ago: int | None,
          exam_id: str = "exam-A", teacher_id: str = "teacher-1") -> dict:
    """Build an exam_sessions row. hb_seconds_ago=None → no heartbeat (stale)."""
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
    """A callable side_effect for supabase.table(...) that records every
    query + chained filter, and returns data from canned fixtures keyed
    by (table, status-filter, exam_id-filter, teacher_id-filter).

    Real Supabase uses a fluent builder — we track each .eq/.is_/.gte and
    then dispatch on "which table + what filters" when .execute() fires.
    """

    def __init__(self, in_progress=None, completed=None, violations=None,
                 teachers=None):
        self.in_progress = in_progress or []
        self.completed = completed or []
        self.violations = violations or []
        self.teachers = teachers or [{"id": "teacher-1", "email": "p@t.com"}]
        # deletion log so tests can assert what was wiped
        self.deletes: list[tuple[str, dict]] = []

    def __call__(self, table_name):
        chain = MagicMock()
        chain._table = table_name
        chain._eqs: dict = {}
        chain._is_null: set = set()
        chain._op = None  # "select" | "delete" | ...

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
            # Everything below is a select.
            if table_name == "teachers":
                return MagicMock(data=self.teachers)
            if table_name == "exam_sessions":
                status = chain._eqs.get("status")
                eid    = chain._eqs.get("exam_id")
                tid    = chain._eqs.get("teacher_id")
                if status == "in_progress":
                    rows = list(self.in_progress)
                    if "teacher_id" in chain._is_null:
                        rows = []  # no orphans in these fixtures
                    elif tid == "":
                        rows = []  # no empty-tid orphans either
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
    """The request step should surface accurate stale/active/completed
    counts so the dashboard preview isn't a lie."""

    def test_active_sessions_protected_by_default(self, client, admin_headers):
        """Baseline safety: a freshly heartbeating session must land in
        active_count, NOT stale_count, when include_active is omitted."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),    # active
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),  # stale
            _sess("S_NOHB",   "carol", hb_seconds_ago=None), # stale
        ])
        with patch.object(main, "supabase") as mock_sb:
            mock_sb.table.side_effect = stub
            resp = client.post("/api/admin/clear-live-sessions",
                               headers=admin_headers, json={"step": "request"})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["active_count"] == 1, d
        assert d["stale_count"] == 2, d
        assert d["completed_count"] == 0, d

    def test_include_active_dissolves_protection(self, client, admin_headers):
        """The headline fix: with include_active=True the heartbeating
        row moves from active → stale so it actually gets wiped on
        confirm. This is the exact regression the user reported."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(main, "supabase") as mock_sb:
            mock_sb.table.side_effect = stub
            resp = client.post("/api/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request", "include_active": True})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["active_count"] == 0, (
            "include_active=True must flip heartbeating rows into stale; "
            f"got active_count={d['active_count']} — force-wipe still protected."
        )
        assert d["stale_count"] == 2, d
        assert d["include_active"] is True

    def test_exam_id_scopes_live_discovery(self, client, admin_headers):
        """Multi-exam teachers: asking to clear exam-A must not discover
        exam-B's rows at all (otherwise the count preview is misleading
        and a later confirm could wipe the wrong exam)."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_A1", "alice", hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_A2", "bob",   hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_B1", "carol", hb_seconds_ago=999, exam_id="exam-B"),
        ])
        with patch.object(main, "supabase") as mock_sb:
            mock_sb.table.side_effect = stub
            resp = client.post("/api/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request", "exam_id": "exam-A"})
        d = resp.json()
        preview_rolls = {p["roll_number"] for p in d["preview"]}
        assert preview_rolls == {"alice", "bob"}, preview_rolls
        assert d["stale_count"] == 2, d

    def test_exam_id_scopes_completed_sweep(self, client, admin_headers):
        """Same scoping applies when include_completed is on — the
        preview should only count completed rows from the selected
        exam, not every completed row the teacher owns."""
        import main
        stub = _SupabaseStub(
            in_progress=[],
            completed=[
                _completed("C_A1", "alice", exam_id="exam-A"),
                _completed("C_B1", "bob",   exam_id="exam-B"),
                _completed("C_B2", "carol", exam_id="exam-B"),
            ],
        )
        with patch.object(main, "supabase") as mock_sb:
            mock_sb.table.side_effect = stub
            resp = client.post("/api/admin/clear-live-sessions",
                               headers=admin_headers,
                               json={"step": "request",
                                     "include_completed": True,
                                     "exam_id": "exam-B"})
        d = resp.json()
        assert d["completed_count"] == 2, d
        completed_rolls = {p["roll_number"] for p in d["completed_preview"]}
        assert completed_rolls == {"bob", "carol"}

    def test_confirm_requires_correct_ack_and_token(self, client, admin_headers):
        """The two-step safety (ack='DELETE', token round-trip) must
        reject malformed confirms even when include_active is passed."""
        import main
        stub = _SupabaseStub()
        with patch.object(main, "supabase") as mock_sb:
            mock_sb.table.side_effect = stub
            # Wrong ack
            r1 = client.post("/api/admin/clear-live-sessions",
                             headers=admin_headers,
                             json={"step": "confirm", "token": "x",
                                   "ack": "YES"})
            # Wrong token
            r2 = client.post("/api/admin/clear-live-sessions",
                             headers=admin_headers,
                             json={"step": "confirm", "token": "bad-token",
                                   "ack": "DELETE"})
        assert r1.status_code == 400
        assert "DELETE" in r1.json()["detail"]
        assert r2.status_code == 400


# ─── Confirm step ─────────────────────────────────────────────────────

class TestConfirmStepDeletes:
    """Verify the confirm step actually issues delete() calls for the
    right keys under the right filters."""

    def _request_then_confirm(self, client, admin_headers, main, stub, body_extra):
        # Step 1: get a token.
        req = client.post("/api/admin/clear-live-sessions",
                          headers=admin_headers,
                          json={"step": "request", **body_extra})
        assert req.status_code == 200, req.text
        token = req.json()["token"]
        # Step 2: confirm with that token.
        return client.post("/api/admin/clear-live-sessions",
                           headers=admin_headers,
                           json={"step": "confirm", "token": token,
                                 "ack": "DELETE", **body_extra})

    def test_default_confirm_skips_active_sessions(self, client, admin_headers):
        """An active session should still be alive after a default
        confirm — only its stale sibling gets deleted."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_cache", None), \
             patch("main.Path") as mock_path:
            mock_sb.table.side_effect = stub
            # Short-circuit the filesystem screenshot cleanup. The code
            # calls `Path(SCREENSHOTS_DIR) / tid`, so we have to stub the
            # result of __truediv__ as well, not just Path(...) itself.
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(client, admin_headers, main, stub, {})
        assert resp.status_code == 200, resp.text
        d = resp.json()
        assert d["skipped_active"] == 1, d
        # The delete log must contain the stale session but NOT the active one.
        deleted_session_keys = [
            f["session_key"] for (t, f) in stub.deletes
            if t == "exam_sessions" and "session_key" in f
        ]
        assert "S_OLD" in deleted_session_keys
        assert "S_RECENT" not in deleted_session_keys, (
            "Active session got deleted despite no include_active flag — "
            "the 120s heartbeat protection has regressed."
        )

    def test_include_active_deletes_everything_inprogress(self, client, admin_headers):
        """With include_active=True the confirm must wipe both stale
        and actively heartbeating sessions."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_RECENT", "alice", hb_seconds_ago=5),
            _sess("S_OLD",    "bob",   hb_seconds_ago=999),
        ])
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_cache", None), \
             patch("main.Path") as mock_path:
            mock_sb.table.side_effect = stub
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(
                client, admin_headers, main, stub,
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
        """The confirm must not delete exam-B rows when the request was
        scoped to exam-A. Without this guarantee a teacher clearing exam
        A could accidentally destroy exam B's live sessions."""
        import main
        stub = _SupabaseStub(in_progress=[
            _sess("S_A1", "alice", hb_seconds_ago=999, exam_id="exam-A"),
            _sess("S_B1", "bob",   hb_seconds_ago=999, exam_id="exam-B"),
        ])
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_cache", None), \
             patch("main.Path") as mock_path:
            mock_sb.table.side_effect = stub
            mock_path.return_value.is_dir.return_value = False
            mock_path.return_value.__truediv__.return_value.is_dir.return_value = False
            resp = self._request_then_confirm(
                client, admin_headers, main, stub,
                {"exam_id": "exam-A"},
            )
        assert resp.status_code == 200, resp.text
        deleted_session_keys = [
            f["session_key"] for (t, f) in stub.deletes
            if t == "exam_sessions" and "session_key" in f
        ]
        assert "S_A1" in deleted_session_keys
        assert "S_B1" not in deleted_session_keys, (
            "exam_id scoping leaked — exam-B's session got deleted while "
            "clearing exam-A."
        )
