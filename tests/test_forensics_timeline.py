"""
Regression tests for /api/admin/timeline/{session_id}.

What we're pinning down:
  1. Completed session: teacher sees every violation/event plus the
     risk/score meta — the baseline Forensics Timeline path.
  2. In-progress session with a valid exam_sessions row: still returns
     a timeline (teachers need to review live students too).
  3. No exam_sessions row yet but matching violations: the teacher-scoped
     fallback in `_assert_session_owned` kicks in and the timeline still
     works. This is the exact path that broke after the multi-tenant
     migration and had to be retrofitted (see main.py:1213).
  4. Cross-tenant access: teacher A cannot read teacher B's session —
     404, never leak data.
  5. Screenshot pairing: `_match_screenshot_for_violation` is called and
     matching screenshots get `screenshot` URLs stamped onto their events.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import make_admin_token  # noqa: E402


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


def _viol(vtype, severity="medium", ts="2026-04-20T10:00:00+00:00",
          details=None, teacher_id="teacher-1", vid=1):
    return {
        "id":             vid,
        "session_key":    "sess_alice_1",
        "violation_type": vtype,
        "severity":       severity,
        "created_at":     ts,
        "details":        details or "{}",
        "teacher_id":     teacher_id,
    }


class _TimelineSupabaseStub:
    """Simple fluent-builder mock that dispatches on table + chained eq
    filters to canned data. Much easier to reason about than a generic
    chained-MagicMock."""

    def __init__(self, sessions=None, violations=None, teachers=None):
        self.sessions = sessions or []
        self.violations = violations or []
        self.teachers = teachers or [{"id": "teacher-1", "email": "p@t.com"}]

    def __call__(self, table):
        chain = MagicMock()
        chain._table = table
        chain._eqs = {}
        chain._neqs = {}

        def _select(*a, **k): return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _neq(c, v): chain._neqs[c] = v; return chain
        def _limit(*a, **k): return chain
        def _order(*a, **k): return chain

        def _execute():
            if table == "teachers":
                return MagicMock(data=self.teachers)
            if table == "exam_sessions":
                sk = chain._eqs.get("session_key")
                tid = chain._eqs.get("teacher_id")
                rows = [r for r in self.sessions
                        if r.get("session_key") == sk]
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                return MagicMock(data=rows)
            if table == "violations":
                sk = chain._eqs.get("session_key")
                tid = chain._eqs.get("teacher_id")
                rows = [v for v in self.violations
                        if v.get("session_key") == sk]
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                # Honour .neq() used by fallback1's "anyone ELSE owns this?" probe
                for col, val in chain._neqs.items():
                    rows = [r for r in rows if str(r.get(col)) != str(val)]
                return MagicMock(data=rows)
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.eq.side_effect = _eq
        chain.neq.side_effect = _neq
        chain.limit.side_effect = _limit
        chain.order.side_effect = _order
        chain.execute.side_effect = _execute
        return chain


class TestForensicsTimeline:

    def test_completed_session_returns_full_timeline(self, client, admin_headers):
        """Happy path for the Forensics Timeline button on Results rows:
        a completed session should yield every violation as a timeline
        entry with severity, type, and a formatted timestamp."""
        import main
        stub = _TimelineSupabaseStub(
            sessions=[{
                "session_key":  "sess_alice_1",
                "teacher_id":   "teacher-1",
                "roll_number":  "alice",
                "full_name":    "Alice",
                "status":       "completed",
                "started_at":   "2026-04-20T09:00:00+00:00",
                "submitted_at": "2026-04-20T10:30:00+00:00",
                "score":        8,
                "total":        10,
                "risk_score":   22,
            }],
            violations=[
                _viol("face_missing", "medium", vid=1),
                _viol("multiple_faces", "high", vid=2),
                _viol("heartbeat", "low", vid=3),  # non-violation event
            ],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_collect_session_screenshots",
                          return_value={}) as _coll:
            mock_sb.table.side_effect = stub
            r = client.get("/api/admin/timeline/sess_alice_1",
                           headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["session_id"] == "sess_alice_1"
        assert d["roll_number"] == "alice"
        assert d["full_name"] == "Alice"
        assert d["status"] == "completed"
        assert d["total_events"] == 3
        assert len(d["timeline"]) == 3
        types = {e["type"] for e in d["timeline"]}
        assert types == {"face_missing", "multiple_faces", "heartbeat"}
        # is_violation flag must be accurate — heartbeats are events,
        # not violations, so filters on "violations only" work.
        hb = next(e for e in d["timeline"] if e["type"] == "heartbeat")
        assert hb["is_violation"] is False
        mf = next(e for e in d["timeline"] if e["type"] == "multiple_faces")
        assert mf["is_violation"] is True

    def test_in_progress_session_still_returns_timeline(self, client, admin_headers):
        """The Timeline button on the LIVE tab must work — an in-progress
        session with a valid row should return events, not 404."""
        import main
        stub = _TimelineSupabaseStub(
            sessions=[{
                "session_key": "sess_bob_1",
                "teacher_id":  "teacher-1",
                "roll_number": "bob",
                "full_name":   "Bob",
                "status":      "in_progress",
                "started_at":  "2026-04-20T09:30:00+00:00",
            }],
            violations=[_viol("window_focus_lost", "medium", vid=10)],
        )
        stub.violations[0]["session_key"] = "sess_bob_1"
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_collect_session_screenshots",
                          return_value={}):
            mock_sb.table.side_effect = stub
            r = client.get("/api/admin/timeline/sess_bob_1",
                           headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "in_progress"
        assert d["total_events"] == 1
        assert d["timeline"][0]["type"] == "window_focus_lost"

    def test_missing_session_row_falls_back_to_violations(self, client, admin_headers):
        """When a student has submitted ID photos but hasn't started the
        exam yet, there's no exam_sessions row — only violations. The
        teacher-scoped fallback in _assert_session_owned should still
        let the teacher open the timeline. This is the bug that broke
        right after the multi-tenant migration (main.py:1213)."""
        import main
        v = _viol("id_verification", "low", vid=42)
        v["session_key"] = "sess_new_student"
        stub = _TimelineSupabaseStub(
            sessions=[],  # no exam_sessions row at all
            violations=[v],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_collect_session_screenshots",
                          return_value={}):
            mock_sb.table.side_effect = stub
            r = client.get("/api/admin/timeline/sess_new_student",
                           headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "in_progress", (
            "Fallback2 must synthesise an in_progress status so the "
            "dashboard doesn't render it as 'unknown'."
        )
        assert d["total_events"] == 1
        assert d["timeline"][0]["type"] == "id_verification"

    def test_cross_tenant_access_is_denied(self, client, admin_headers):
        """Teacher A must never read teacher B's timeline — the ownership
        assertion should 404, not leak the session."""
        import main
        stub = _TimelineSupabaseStub(
            sessions=[{
                "session_key": "sess_victim_1",
                "teacher_id":  "teacher-OTHER",   # not us
                "roll_number": "mallory",
                "full_name":   "Mallory",
                "status":      "completed",
            }],
            violations=[_viol("face_missing", vid=99,
                              teacher_id="teacher-OTHER")],
        )
        stub.violations[0]["session_key"] = "sess_victim_1"
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_collect_session_screenshots",
                          return_value={}):
            mock_sb.table.side_effect = stub
            r = client.get("/api/admin/timeline/sess_victim_1",
                           headers=admin_headers)
        assert r.status_code == 404, (
            f"Cross-tenant access must 404 — got {r.status_code} with "
            f"body {r.text}. This is a data-leak class bug."
        )

    def test_screenshot_pairing_stamps_urls(self, client, admin_headers):
        """When a violation has a matching screenshot on disk, the
        endpoint should stamp a /api/admin/screenshot/... URL onto that
        event so the dashboard can render the thumbnail inline."""
        import main
        stub = _TimelineSupabaseStub(
            sessions=[{
                "session_key": "sess_alice_2",
                "teacher_id":  "teacher-1",
                "roll_number": "alice",
                "full_name":   "Alice",
                "status":      "completed",
            }],
            violations=[_viol("face_missing", "medium", vid=7)],
        )
        stub.violations[0]["session_key"] = "sess_alice_2"
        fake_screenshot = Path("face_missing_20260420_100005.jpg")
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "_collect_session_screenshots",
                          return_value={fake_screenshot.name: fake_screenshot}), \
             patch.object(main, "_match_screenshot_for_violation",
                          return_value=fake_screenshot):
            mock_sb.table.side_effect = stub
            r = client.get("/api/admin/timeline/sess_alice_2",
                           headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        event = d["timeline"][0]
        assert event.get("screenshot"), (
            "Timeline event should carry a screenshot URL when a match "
            "was found — the dashboard relies on this field to render "
            "thumbnails inline next to the event."
        )
        assert event["screenshot"].startswith("/api/admin/screenshot/alice/")
        assert event["screenshot"].endswith(fake_screenshot.name)
