"""
Regression tests for /sessions — the Live tab payload.

What broke and why we care:
  Before this test pinned the behaviour, any session with
  status='in_progress' was rendered as "Live" on the dashboard,
  regardless of when the student's client last heartbeat'd.
  That left abandoned sessions (student closed laptop, crashed,
  network died) frozen on the Live tab for hours. Teachers
  would see "ACTIVE NOW: 5" that was entirely stale rows.

  Fix: the server now classifies each session into
  live_state ∈ {"live","stale","submitted"} based on the age of
  last_heartbeat vs _CLEAR_ACTIVE_WINDOW (120s).

These tests lock in:
  1. Fresh heartbeat → live_state == "live", counted in `sessions`.
  2. Old heartbeat → live_state == "stale", NOT counted in `sessions`
     (but still appears in all_sessions).
  3. No heartbeat at all → live_state == "stale".
  4. status=="completed" → live_state == "submitted".
  5. Exam scoping still filters as before.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import make_admin_token  # noqa: E402


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token()}"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _viol(sk, vtype="heartbeat", severity="low",
          ts=None, teacher_id="teacher-1"):
    return {
        "session_key":    sk,
        "violation_type": vtype,
        "severity":       severity,
        "created_at":     ts or _iso(datetime.now(timezone.utc)),
        "details":        "{}",
        "teacher_id":     teacher_id,
    }


class _SessionsStub:
    """Fluent-builder stub that dispatches on table + chained eq filters.

    Mirrors the shape of the Supabase Python client the route uses:
        supabase.table("foo").select(...).eq(...).gte(...).order(...).execute()
    """

    def __init__(self, sessions=None, violations=None, teachers=None):
        self.sessions = sessions or []
        self.violations = violations or []
        self.teachers = teachers or [{"id": "teacher-1", "email": "p@t.com"}]

    def __call__(self, table):
        chain = MagicMock()
        chain._table = table
        chain._eqs = {}

        def _select(*a, **k): return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _gte(*a, **k): return chain
        def _order(*a, **k): return chain
        def _limit(*a, **k): return chain

        def _execute():
            if table == "teachers":
                return MagicMock(data=self.teachers)
            if table == "exam_sessions":
                tid = chain._eqs.get("teacher_id")
                eid = chain._eqs.get("exam_id")
                rows = list(self.sessions)
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                if eid is not None:
                    rows = [r for r in rows if str(r.get("exam_id")) == str(eid)]
                return MagicMock(data=rows)
            if table == "violations":
                tid = chain._eqs.get("teacher_id")
                rows = list(self.violations)
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                return MagicMock(data=rows)
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.eq.side_effect = _eq
        chain.gte.side_effect = _gte
        chain.order.side_effect = _order
        chain.limit.side_effect = _limit
        chain.execute.side_effect = _execute
        return chain


class TestLiveSessions:

    def test_fresh_heartbeat_is_live(self, client, admin_headers):
        """Heartbeat 10s ago → live_state=='live', counted in Active."""
        import main
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_alice_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": _iso(now - timedelta(seconds=10)),
                "started_at":     _iso(now - timedelta(minutes=5)),
                "submitted_at":   None,
            }],
            violations=[_viol("sess_alice_1", vtype="face_missing")],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "compute_risk_score",
                          return_value={"risk_score": 12}):
            mock_sb.table.side_effect = stub
            r = client.get("/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["all_sessions"]) == 1
        s = d["all_sessions"][0]
        assert s["live_state"] == "live"
        assert s["submitted"] is False
        assert s["heartbeat_age_sec"] is not None
        assert s["heartbeat_age_sec"] < 120
        # Active counter uses live_state=="live"
        assert len(d["sessions"]) == 1

    def test_stale_heartbeat_is_stale_not_live(self, client, admin_headers):
        """Heartbeat 10 min ago → live_state=='stale' and NOT counted Active.

        This is the exact bug the user reported: sessions frozen at
        in_progress for hours because the student client died without
        submitting. The Live badge must downgrade.
        """
        import main
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_bob_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": _iso(now - timedelta(minutes=10)),
                "started_at":     _iso(now - timedelta(minutes=15)),
                "submitted_at":   None,
            }],
            violations=[_viol("sess_bob_1", vtype="vpn_detected",
                              severity="high",
                              ts=_iso(now - timedelta(minutes=10)))],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "compute_risk_score",
                          return_value={"risk_score": 50}):
            mock_sb.table.side_effect = stub
            r = client.get("/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["all_sessions"]) == 1
        s = d["all_sessions"][0]
        assert s["live_state"] == "stale", (
            f"Session 10 min stale must be classified 'stale', got "
            f"{s['live_state']!r}. The dashboard relies on this to stop "
            "showing abandoned sessions as Live."
        )
        # Still listed in all_sessions so the teacher can see it — but
        # absent from `sessions` which drives the 'Active Now' counter.
        assert len(d["sessions"]) == 0

    def test_missing_heartbeat_is_stale(self, client, admin_headers):
        """in_progress row with no last_heartbeat at all → stale.

        A session can land in this state when the row was written but
        the client died before the first heartbeat tick. It must not
        be counted as Live forever.
        """
        import main
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_ghost_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "in_progress",
                "risk_score":     None,
                "last_heartbeat": None,
                "started_at":     None,
                "submitted_at":   None,
            }],
            violations=[_viol("sess_ghost_1")],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "compute_risk_score",
                          return_value={"risk_score": 0}):
            mock_sb.table.side_effect = stub
            r = client.get("/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["all_sessions"][0]
        assert s["live_state"] == "stale"
        assert s["heartbeat_age_sec"] is None

    def test_completed_session_is_submitted(self, client, admin_headers):
        """status=='completed' → live_state=='submitted', submitted=True."""
        import main
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[{
                "session_key":    "sess_carol_1",
                "teacher_id":     "teacher-1",
                "exam_id":        "exam-1",
                "status":         "completed",
                "risk_score":     18,
                "last_heartbeat": _iso(now - timedelta(minutes=30)),
                "started_at":     _iso(now - timedelta(hours=1)),
                "submitted_at":   _iso(now - timedelta(minutes=30)),
            }],
            violations=[_viol("sess_carol_1", vtype="window_focus_lost",
                              severity="medium")],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "compute_risk_score",
                          return_value={"risk_score": 18}):
            mock_sb.table.side_effect = stub
            r = client.get("/sessions", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["all_sessions"][0]
        assert s["live_state"] == "submitted"
        assert s["submitted"] is True
        assert len(d["sessions"]) == 0

    def test_exam_scope_filters_other_exams(self, client, admin_headers):
        """exam_id query param must still filter — multi-tenant teachers
        viewing one exam should not see sessions from a sibling exam."""
        import main
        now = datetime.now(timezone.utc)
        stub = _SessionsStub(
            sessions=[
                {
                    "session_key":    "sess_in_exam_1",
                    "teacher_id":     "teacher-1",
                    "exam_id":        "exam-1",
                    "status":         "in_progress",
                    "risk_score":     None,
                    "last_heartbeat": _iso(now - timedelta(seconds=5)),
                    "started_at":     _iso(now - timedelta(minutes=2)),
                    "submitted_at":   None,
                },
                {
                    "session_key":    "sess_other_exam_1",
                    "teacher_id":     "teacher-1",
                    "exam_id":        "exam-2",
                    "status":         "in_progress",
                    "risk_score":     None,
                    "last_heartbeat": _iso(now - timedelta(seconds=5)),
                    "started_at":     _iso(now - timedelta(minutes=2)),
                    "submitted_at":   None,
                },
            ],
            violations=[
                _viol("sess_in_exam_1"),
                _viol("sess_other_exam_1"),
            ],
        )
        with patch.object(main, "supabase") as mock_sb, \
             patch.object(main, "compute_risk_score",
                          return_value={"risk_score": 5}):
            mock_sb.table.side_effect = stub
            r = client.get("/sessions?exam_id=exam-1", headers=admin_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        keys = {s["session_id"] for s in d["all_sessions"]}
        assert keys == {"sess_in_exam_1"}, (
            f"exam scope leak — expected only exam-1 sessions, got {keys}"
        )
