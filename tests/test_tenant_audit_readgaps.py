"""Tenancy audit — the two read roll-up gaps closed in the audit:
  - GET /api/v1/admin/answers/{session_id}
  - GET /api/v1/admin/sessions/{session_id}/triage
Both must (a) 404 a cross-tenant session and (b) key reads on the session
OWNER's tid (not the caller's) so an org admin sees a co-teacher's data.
"""
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token  # noqa: E402

ADMIN = {"id": "teacher-1", "email": "admin@test.com", "org_id": "org-1",
         "org_role": "admin", "full_name": "Admin", "status": "active"}


def _hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1')}"}


# ── /admin/answers ────────────────────────────────────────────────

def test_answers_cross_tenant_404(client):
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.auth.scope.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.auth.scope.assert_session_accessible",
               AsyncMock(side_effect=HTTPException(status_code=404, detail="Session not found"))):
        r = client.get("/api/v1/admin/answers/mallory_sess", headers=_hdr())
    assert r.status_code == 404


def test_answers_keyed_on_owner_tid(client):
    captured = {}

    class _Chain:
        def select(self, *a): return self
        def eq(self, col, val):
            captured.setdefault("eqs", {})[col] = val
            return self
        async def execute(self):
            r = MagicMock(); r.data = []; return r

    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.auth.scope.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.auth.scope.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": "teacher-2"})), \
         patch("app.routers.question_bank._load_questions", AsyncMock(return_value=[])), \
         patch("app.routers.question_bank._atable", side_effect=lambda t: _Chain()):
        r = client.get("/api/v1/admin/answers/co_teacher_sess", headers=_hdr())
    assert r.status_code == 200, r.text
    assert captured["eqs"]["teacher_id"] == "teacher-2"   # owner, not caller


def test_answers_orphan_session_no_cross_tenant_leak(client):
    # An ownerless/orphan session (teacher_id == "") must NOT fall through to
    # _load_questions("") — which load_questions treats as "no filter" and would
    # return EVERY teacher's questions. Endpoint must early-return empty.
    leaked = [{"id": "q-other", "question": "another teacher's question",
               "options": {}, "correct": "X", "question_type": "mcq_single"}]
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.auth.scope.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.auth.scope.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": ""})), \
         patch("app.routers.question_bank._load_questions", AsyncMock(return_value=leaked)):
        r = client.get("/api/v1/admin/answers/orphan_sess", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0 and body["answers"] == []   # nothing leaked


def test_triage_orphan_session_404(client):
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.auth.scope.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.auth.scope.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": ""})):
        r = client.get("/api/v1/admin/sessions/orphan_sess/triage", headers=_hdr())
    assert r.status_code == 404


# ── /sessions/{id}/triage ─────────────────────────────────────────

def test_triage_cross_tenant_404(client):
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.auth.scope.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.auth.scope.assert_session_accessible",
               AsyncMock(side_effect=HTTPException(status_code=404, detail="Session not found"))):
        r = client.get("/api/v1/admin/sessions/mallory_sess/triage", headers=_hdr())
    assert r.status_code == 404


# ── terminate: emergency recovery (owner OR org-admin), with attribution ──────

def test_terminate_cross_tenant_404(client):
    # Outside the caller's scope: assert_session_accessible 404s.
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.routers.admin.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.routers.admin.assert_session_accessible",
               AsyncMock(side_effect=HTTPException(status_code=404, detail="Session not found"))):
        r = client.post("/api/v1/admin/sessions/mallory_sess/terminate", headers=_hdr())
    assert r.status_code == 404


def _term_chain(captured):
    class _Chain:
        def update(self, fields): captured["update"] = fields; return self
        def insert(self, row): captured.setdefault("insert", row); return self
        def eq(self, col, val): captured.setdefault("eqs", {})[col] = val; return self
        async def execute(self):
            r = MagicMock(); r.data = [{"session_key": "s"}]; return r
    return _Chain()


def test_terminate_by_admin_on_co_teacher_attributes_admin(client):
    # Org admin terminating a co-teacher's (teacher-2) session: allowed, write
    # scoped to the OWNER, evidence attributes the ADMIN actor.
    captured = {}
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.routers.admin.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.routers.admin.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": "teacher-2"})), \
         patch("app.routers.admin._atable", side_effect=lambda t: _term_chain(captured)):
        r = client.post("/api/v1/admin/sessions/co_teacher_sess/terminate", headers=_hdr())
    assert r.status_code == 200, r.text
    assert captured["eqs"]["teacher_id"] == "teacher-2"          # write scoped to owner
    assert captured["insert"]["teacher_id"] == "teacher-2"        # evidence under owner
    assert "admin" in captured["insert"]["details"]              # attributed to admin
    assert "teacher-1" in captured["insert"]["details"]          # caller id recorded


SUPERADMIN = {"id": "super-1", "email": "founder@procta.com", "org_id": None,
              "org_role": "superadmin", "full_name": "Founder", "status": "active"}


def _super_hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='super-1')}"}


def test_superadmin_mutation_blocked_403(client):
    # The global require_admin guard 403s ANY mutating request from a
    # superadmin (monitor-only debug role) — covering all ~90 admin writes.
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=SUPERADMIN):
        r = client.post("/api/v1/admin/sessions/any_sess/terminate", headers=_super_hdr())
    assert r.status_code == 403
    assert "monitor-only" in r.text.lower()


def test_superadmin_get_allowed(client):
    # Reads (GET) are NOT blocked — superadmin can still observe.
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=SUPERADMIN):
        r = client.get("/api/v1/auth/me", headers=_super_hdr())
    assert r.status_code == 200, r.text


def test_superadmin_auth_route_exempt(client):
    # Identity routes are exempt so superadmin can still authenticate.
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=SUPERADMIN):
        r = client.post("/api/v1/auth/reauth", headers=_super_hdr())
    assert r.status_code != 403


def test_terminate_by_owner_attributes_owner(client):
    captured = {}
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=ADMIN), \
         patch("app.routers.admin.resolve_scope",
               AsyncMock(return_value={"role": "admin", "teacher_id": None, "org_id": "org-1"})), \
         patch("app.routers.admin.assert_session_accessible",
               AsyncMock(return_value={"teacher_id": "teacher-1"})), \
         patch("app.routers.admin._atable", side_effect=lambda t: _term_chain(captured)):
        r = client.post("/api/v1/admin/sessions/my_sess/terminate", headers=_hdr())
    assert r.status_code == 200, r.text
    assert "owner" in captured["insert"]["details"]              # attributed to owner
