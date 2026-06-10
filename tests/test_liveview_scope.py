"""
Org-scope tests for the live-view router (app/routers/admin_liveview.py).

The live monitoring view widened from owner-scoped to org-scoped so an
org admin can watch every in-progress session run by any teacher in
their org. Per-session live endpoints must still 404 for cross-tenant
sessions (a different org) so we never leak another org's feed.

We exercise the per-session guard (`assert_session_accessible`) through
the room-cam status endpoint, which is a clean read-only single-session
handler returning a JSON body.

  1. Cross-tenant access (session owned by a teacher in a DIFFERENT org)
     must 404 — never leak existence.
  2. In-org access (session owned by another teacher in the SAME org)
     must succeed for an admin caller.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock, make_admin_token  # noqa: E402


# Caller is teacher-1, an ADMIN of org "org-A".
CALLER_ID = "teacher-1"
CALLER_ORG = "org-A"


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id=CALLER_ID)}"}


class _LiveviewSupabaseStub:
    """Fluent-builder mock dispatching on table + chained eq filters.

    Models the three tables the request path touches:
      • teachers       — require_admin hydration + _verify_teacher_in_org
      • exam_sessions   — assert_session_accessible row lookup
      • violations      — assert_session_accessible fallback probe
    """

    def __init__(self, sessions=None, violations=None, teachers=None):
        self.sessions = sessions or []
        self.violations = violations or []
        # Default: caller is an admin of org-A.
        self.teachers = teachers or [
            {"id": CALLER_ID, "email": "p@t.com",
             "org_id": CALLER_ORG, "org_role": "admin", "status": "active"},
        ]

    def __call__(self, table):
        chain = MagicMock()
        chain._table = table
        chain._eqs = {}

        def _select(*a, **k): return chain
        def _eq(c, v): chain._eqs[c] = v; return chain
        def _limit(*a, **k): return chain
        def _order(*a, **k): return chain
        def _not_(*a, **k): return chain
        def _is_(*a, **k): return chain

        def _execute():
            if table == "teachers":
                rows = list(self.teachers)
                tid = chain._eqs.get("id")
                if tid is not None:
                    rows = [r for r in rows if str(r.get("id")) == str(tid)]
                org = chain._eqs.get("org_id")
                if org is not None:
                    rows = [r for r in rows if str(r.get("org_id")) == str(org)]
                return MagicMock(data=rows)
            if table == "exam_sessions":
                sk = chain._eqs.get("session_key")
                rows = [r for r in self.sessions if r.get("session_key") == sk]
                tid = chain._eqs.get("teacher_id")
                if tid is not None:
                    rows = [r for r in rows if str(r.get("teacher_id")) == str(tid)]
                return MagicMock(data=rows)
            if table == "violations":
                sk = chain._eqs.get("session_key")
                rows = [v for v in self.violations if v.get("session_key") == sk]
                return MagicMock(data=rows)
            if table == "auth_sessions":
                # No revoked sessions in tests.
                return MagicMock(data=[])
            return MagicMock(data=[])

        chain.select.side_effect = _select
        chain.eq.side_effect = _eq
        chain.limit.side_effect = _limit
        chain.order.side_effect = _order
        chain.not_ = MagicMock()
        chain.not_.is_.side_effect = _is_
        chain.execute.side_effect = _execute
        return chain


class TestLiveviewScope:

    def test_cross_tenant_room_cam_status_is_denied(self, client, admin_headers):
        """An admin of org-A must NOT read a session owned by a teacher in
        a different org — the per-session guard must 404, not leak."""
        stub = _LiveviewSupabaseStub(
            sessions=[{
                "session_key": "sess_victim_1",
                "teacher_id":  "teacher-OTHER",   # belongs to a different org
                "status":      "in_progress",
                "room_cam_status": "approved",
            }],
            violations=[],
            teachers=[
                {"id": CALLER_ID, "email": "p@t.com",
                 "org_id": CALLER_ORG, "org_role": "admin", "status": "active"},
                # teacher-OTHER is in a DIFFERENT org → not in caller's org.
                {"id": "teacher-OTHER", "email": "x@y.com",
                 "org_id": "org-B", "org_role": "teacher", "status": "active"},
            ],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get(
                "/api/v1/admin/sessions/sess_victim_1/room-cam/status",
                headers=admin_headers,
            )
        assert r.status_code == 404, (
            f"Cross-tenant live access must 404 — got {r.status_code} "
            f"with body {r.text}. This is a data-leak class bug."
        )

    def test_in_org_room_cam_status_is_allowed(self, client, admin_headers):
        """An admin of org-A CAN read a session owned by another teacher
        in the SAME org — this is the widened live-monitoring path."""
        stub = _LiveviewSupabaseStub(
            sessions=[{
                "session_key": "sess_colleague_1",
                "teacher_id":  "teacher-2",   # same org as caller
                "status":      "in_progress",
                "room_cam_status": "approved",
                "room_cam_approved_at": "2026-06-07T10:00:00+00:00",
            }],
            violations=[],
            teachers=[
                {"id": CALLER_ID, "email": "p@t.com",
                 "org_id": CALLER_ORG, "org_role": "admin", "status": "active"},
                # teacher-2 is in the SAME org → in scope.
                {"id": "teacher-2", "email": "c@t.com",
                 "org_id": CALLER_ORG, "org_role": "teacher", "status": "active"},
            ],
        )
        with patch.object(shared_supabase_mock(), "table") as mock_table:
            mock_table.side_effect = stub
            r = client.get(
                "/api/v1/admin/sessions/sess_colleague_1/room-cam/status",
                headers=admin_headers,
            )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "approved"
        assert d["approved_at"] == "2026-06-07T10:00:00+00:00"
