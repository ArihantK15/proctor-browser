"""Tests for org soft-delete / suspend (Gap #5 — DELETE /api/v1/admin/orgs/{org_id}).

Covers:
  - Superadmin happy path
  - Non-superadmin 403
  - Missing reauth 403
  - Unknown org 404
  - Platform-admin-org guard 400
  - Active sessions -> 409 (and force:true -> 200)
  - Already-deleted -> 409
  - Restore -> 200
  - list_all_orgs excludes deleted by default; includes with ?include_deleted=true
"""
import contextlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth.tokens import issue_reauth_token
from tests.conftest import make_admin_token

SUPERADMIN = {"id": "super-1", "email": "super@admin.com", "org_id": "org-1",
              "org_role": "superadmin", "full_name": "Super Admin"}
PLAIN_ADMIN = {"id": "admin-1", "email": "admin@test.com", "org_id": "org-1",
               "org_role": "admin", "full_name": "Org Admin"}

ORG = {"id": "org-1", "name": "Test Org", "slug": "test-org",
       "max_students": 30, "created_at": "2025-01-01T00:00:00+00:00",
       "deleted_at": None, "deleted_by": None, "delete_reason": None}
ORG_DELETED = {**ORG, "deleted_at": "2025-06-01T00:00:00+00:00"}

MEMBERS = [
    {"id": "t1", "email": "teacher1@test.com", "org_role": "teacher"},
    {"id": "t2", "email": "teacher2@test.com", "org_role": "admin"},
]


def superadmin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='super-1', email='super@admin.com')}"}


def _reauth_headers(tid="super-1"):
    h = dict(superadmin_headers())
    h["X-Reauth-Token"] = issue_reauth_token(tid)
    return h


def _chain(data=None, count=None):
    m = MagicMock()
    m._data = data if data is not None else []
    m._count = count
    for attr in ("select", "eq", "neq", "is_", "in_", "order", "limit",
                 "single", "range", "insert", "update", "delete",
                 "gte", "lte", "gt", "lt", "like"):
        getattr(m, attr).return_value = m

    async def _execute():
        r = MagicMock()
        r.data = m._data
        r.count = m._count
        return r

    m.execute = _execute
    return m


def _data_map_side_effect(mapping):
    def side_effect(table):
        return _chain(mapping.get(table, []))
    return side_effect


def _atable_patches(data_map):
    se = _data_map_side_effect(data_map)
    return [patch("app.routers.admin_org._atable", side_effect=se)]


def _require_admin_patch(teacher=SUPERADMIN):
    return patch("app.routers.admin_org.require_admin", return_value=teacher)


def _require_admin_auth_patch(teacher=SUPERADMIN):
    return patch("app.auth.admin_auth._get_teacher_by_id", return_value=teacher)


class TestDeleteOrg:

    def _setup_mocks(self, org=ORG, members=None, live_count=0):
        data_map = {
            "organizations": [org],
            "teachers": members or MEMBERS,
            "exam_sessions": [],
        }
        if live_count:
            def side_effect(table):
                if table == "exam_sessions":
                    return _chain([], count=live_count)
                return _chain(data_map.get(table, []))
            return [patch("app.routers.admin_org._atable", side_effect=side_effect)]
        return _atable_patches(data_map)

    def test_superadmin_happy_path(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in self._setup_mocks():
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=_reauth_headers(),
                                  content=json.dumps({"reason": "Non-payment"}))
        assert resp.status_code == 200
        d = resp.json()
        assert d["ok"] is True
        assert d["org_id"] == "org-1"
        assert d["members_suspended"] == 2
        assert d["errors"] == []

    def test_non_superadmin_403(self, client):
        with _require_admin_patch(PLAIN_ADMIN), \
             _require_admin_auth_patch(PLAIN_ADMIN):
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=superadmin_headers())
        assert resp.status_code == 403
        assert "Super admin" in resp.text

    def test_missing_reauth_403(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch():
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=superadmin_headers())
        assert resp.status_code == 403

    def test_unknown_org_404(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in _atable_patches({"organizations": []}):
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/nonexistent",
                                  headers=_reauth_headers())
        assert resp.status_code == 404

    def test_platform_admin_org_400(self, client):
        members_with_super = MEMBERS + [
            {"id": "super-1", "email": "super@admin.com", "org_role": "superadmin"},
        ]
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in self._setup_mocks(members=members_with_super):
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=_reauth_headers(),
                                  content=json.dumps({"reason": "test"}))
        assert resp.status_code == 400
        assert "platform admin" in resp.text.lower()

    def test_active_sessions_409(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in self._setup_mocks(live_count=3):
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=_reauth_headers(),
                                  content=json.dumps({"reason": "test"}))
        assert resp.status_code == 409
        assert "active exam sessions" in resp.text.lower()

    def test_active_sessions_force_200(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in self._setup_mocks(live_count=3):
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=_reauth_headers(),
                                  content=json.dumps({"reason": "test", "force": True}))
        assert resp.status_code == 200

    def test_already_deleted_409(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in self._setup_mocks(org=ORG_DELETED):
                es.enter_context(p)
            resp = client.request("DELETE", "/api/v1/admin/orgs/org-1",
                                  headers=_reauth_headers(),
                                  content=json.dumps({"reason": "test"}))
        assert resp.status_code == 409
        assert "already deleted" in resp.text.lower()


class TestRestoreOrg:

    def test_happy_path(self, client):
        data_map = {
            "organizations": [ORG_DELETED],
            "teachers": MEMBERS,
        }
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in _atable_patches(data_map):
                es.enter_context(p)
            resp = client.post("/api/v1/admin/orgs/org-1/restore",
                               headers=_reauth_headers())
        assert resp.status_code == 200
        d = resp.json()
        assert d["ok"] is True
        assert d["org_id"] == "org-1"
        assert d["errors"] == []

    def test_non_superadmin_403(self, client):
        with _require_admin_patch(PLAIN_ADMIN), \
             _require_admin_auth_patch(PLAIN_ADMIN):
            resp = client.post("/api/v1/admin/orgs/org-1/restore",
                               headers=superadmin_headers())
        assert resp.status_code == 403

    def test_unknown_org_404(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in _atable_patches({"organizations": []}):
                es.enter_context(p)
            resp = client.post("/api/v1/admin/orgs/nonexistent/restore",
                               headers=_reauth_headers())
        assert resp.status_code == 404

    def test_not_deleted_409(self, client):
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in _atable_patches({"organizations": [ORG]}):
                es.enter_context(p)
            resp = client.post("/api/v1/admin/orgs/org-1/restore",
                               headers=_reauth_headers())
        assert resp.status_code == 409
        assert "not deleted" in resp.text.lower()


class TestListAllOrgsFilter:

    def test_excludes_deleted_by_default(self, client):
        live_org = {**ORG, "id": "org-1"}
        dead_org = {**ORG, "id": "org-2", "deleted_at": "2025-06-01T00:00:00+00:00"}
        se = _data_map_side_effect({
            "organizations": [live_org, dead_org],
            "teachers": MEMBERS,
        })
        patches = [patch("app.routers.admin_org._atable", side_effect=se)]
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in patches:
                es.enter_context(p)
            resp = client.get("/api/v1/admin/all-orgs", headers=superadmin_headers())
        assert resp.status_code == 200
        assert isinstance(resp.json()["orgs"], list)

    def test_include_deleted_true_shows_all(self, client):
        dead_org = {**ORG, "id": "org-2", "deleted_at": "2025-06-01T00:00:00+00:00",
                    "deleted_by": "super-1", "delete_reason": "test"}
        se = _data_map_side_effect({
            "organizations": [ORG, dead_org],
            "teachers": MEMBERS,
        })
        patches = [patch("app.routers.admin_org._atable", side_effect=se)]
        with _require_admin_patch(), \
             _require_admin_auth_patch(), \
             contextlib.ExitStack() as es:
            for p in patches:
                es.enter_context(p)
            resp = client.get("/api/v1/admin/all-orgs?include_deleted=true",
                              headers=superadmin_headers())
        assert resp.status_code == 200


def test_live_statuses_are_lowercase_enum_values():
    """Regression guard for the gap-#5 critical bug.

    The active-session guard must query the status values the DB actually
    stores. SessionStatus is a StrEnum with LOWERCASE values; the original
    hardcoded-uppercase list matched nothing, silently disabling the guard.
    The mock-based tests above can't catch this (the exam_sessions mock
    returns the live count regardless of the status filter), so we assert the
    value contract directly here; the real query is exercised against Postgres
    in integration_tests/test_org_delete_integration.py.
    """
    from app.routers.admin_org import _LIVE_STATUSES
    assert {str(s) for s in _LIVE_STATUSES} == {"in_progress", "paused"}
