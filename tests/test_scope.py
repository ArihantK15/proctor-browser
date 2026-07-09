"""Unit tests for app/auth/scope.py — tenancy scope resolution and
cross-tenant access guards.

This module is the single audit point for "which sessions/results is this
caller allowed to see" (see module docstring in app/auth/scope.py), so these
tests focus on:
  - normal-path scope resolution for each of the three role categories
    (teacher / admin / superadmin);
  - tenant isolation: an admin cannot pull a ?teacher_id= from another org,
    and assert_session_accessible() must 404 (not leak) cross-tenant rows;
  - the higher-complexity branches inside assert_session_accessible (direct
    row / orphan row / no-row-violations-only, for each role); and
  - edge cases: missing org_id, malformed/blank query params, empty rows.

Convention: this repo's async app code is exercised with `asyncio.run(...)`
inside plain `def test_*()` functions (see test_account_types_signup.py) —
no pytest-asyncio marks are configured, so we follow the same pattern.
Database access is faked with small local stub classes rather than the
shared supabase mock, since scope.py talks to Postgres via `_atable(...)`
(the async_table wrapper) rather than the sync supabase client.
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException, Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from app.auth import scope  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_request(query_string: str = "") -> Request:
    """Build a minimal ASGI Request carrying the given raw query string."""
    scope_dict = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": query_string.encode("utf-8"),
        "headers": [],
    }
    return Request(scope_dict)


class _Rows:
    """Fluent stub mimicking the _atable(...) chain builder.

    `.select().eq().eq().limit().execute()` all return self except the
    final `.execute()`, which is async and returns an object with `.data`.
    Records every `.eq(col, val)` call so tests can assert on the query
    that scope.py built, and can be configured per-table via a dict of
    table_name -> list[dict] (or a callable for multi-call sequences).
    """

    def __init__(self, data):
        self._data = data
        self.eq_calls = []

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    def limit(self, *a, **kw):
        return self

    async def execute(self):
        class _Result:
            pass
        r = _Result()
        r.data = self._data
        return r


class _AtableRouter:
    """Routes _atable("table_name") calls to per-table canned responses.

    `responses` maps table name -> list[dict] (a fixed row set) or a
    zero-arg callable returning list[dict] (for tables queried more than
    once with different expected results per call).
    """

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls = []  # records table names requested, in order
        self._instances = []  # every _Rows created, for eq_calls introspection

    def __call__(self, table_name):
        self.calls.append(table_name)
        resp = self._responses.get(table_name, [])
        data = resp() if callable(resp) else resp
        inst = _Rows(data)
        self._instances.append(inst)
        return inst


# ─────────────────────────────────────────────────────────────────────────
# compute_is_solo — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────

def test_compute_is_solo_superadmin_never_solo():
    # Even with no org_id and zero members, superadmin is cross-org tooling.
    assert scope.compute_is_solo("superadmin", None, 0) is False
    assert scope.compute_is_solo("superadmin", "org-1", 0) is False


def test_compute_is_solo_case_insensitive_superadmin():
    # Guards against a non-canonical "Superadmin" slipping past the check
    # and being mis-classified as a solo teacher (per the inline comment).
    assert scope.compute_is_solo("Superadmin", None, 0) is False
    assert scope.compute_is_solo("SUPERADMIN", "org-1", 5) is False


def test_compute_is_solo_no_org_id_is_solo():
    assert scope.compute_is_solo("teacher", None, 0) is True
    assert scope.compute_is_solo("admin", "", 0) is True  # falsy org_id


def test_compute_is_solo_single_member_org_is_solo():
    assert scope.compute_is_solo("teacher", "org-1", 1) is True
    assert scope.compute_is_solo("teacher", "org-1", 0) is True


def test_compute_is_solo_multi_member_org_is_not_solo():
    assert scope.compute_is_solo("admin", "org-1", 2) is False
    assert scope.compute_is_solo("teacher", "org-1", 50) is False


def test_compute_is_solo_none_role_treated_as_non_superadmin():
    assert scope.compute_is_solo(None, None, 0) is True
    assert scope.compute_is_solo(None, "org-1", 5) is False


# ─────────────────────────────────────────────────────────────────────────
# org_is_solo — short-circuits vs. DB-backed member count
# ─────────────────────────────────────────────────────────────────────────

def test_org_is_solo_superadmin_short_circuits_no_db_hit(monkeypatch):
    called = {"hit": False}

    async def _boom(table_name):
        called["hit"] = True
        raise AssertionError("should not query DB for superadmin")

    monkeypatch.setattr(scope, "_atable", _boom)
    teacher = {"org_role": "superadmin", "org_id": "org-1"}
    assert _run(scope.org_is_solo(teacher)) is False
    assert called["hit"] is False


def test_org_is_solo_no_org_id_short_circuits_no_db_hit(monkeypatch):
    async def _boom(table_name):
        raise AssertionError("should not query DB when org_id is falsy")

    monkeypatch.setattr(scope, "_atable", _boom)
    teacher = {"org_role": "teacher", "org_id": None}
    assert _run(scope.org_is_solo(teacher)) is True


def test_org_is_solo_counts_members_for_real_org(monkeypatch):
    router = _AtableRouter({"teachers": [{"id": "t1"}]})
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"org_role": "teacher", "org_id": "org-1"}
    assert _run(scope.org_is_solo(teacher)) is True  # 1 member -> solo
    assert router.calls == ["teachers"]


def test_org_is_solo_multi_member_org_not_solo(monkeypatch):
    router = _AtableRouter({"teachers": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]})
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"org_role": "admin", "org_id": "org-1"}
    assert _run(scope.org_is_solo(teacher)) is False


def test_org_is_solo_defaults_role_to_teacher_when_missing(monkeypatch):
    # org_role missing entirely -> defaults to "teacher", not superadmin,
    # so it still hits the DB and uses the member count.
    router = _AtableRouter({"teachers": []})
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"org_id": "org-1"}
    assert _run(scope.org_is_solo(teacher)) is True  # 0 members -> solo
    assert router.calls == ["teachers"]


# ─────────────────────────────────────────────────────────────────────────
# _org_owner_teacher_id / is_billing_owner
# ─────────────────────────────────────────────────────────────────────────

def test_org_owner_teacher_id_returns_owner(monkeypatch):
    router = _AtableRouter({"organizations": [{"owner_teacher_id": "teacher-9"}]})
    monkeypatch.setattr(scope, "_atable", router)
    assert _run(scope._org_owner_teacher_id("org-1")) == "teacher-9"


def test_org_owner_teacher_id_no_rows_returns_none(monkeypatch):
    router = _AtableRouter({"organizations": []})
    monkeypatch.setattr(scope, "_atable", router)
    assert _run(scope._org_owner_teacher_id("org-missing")) is None


def test_org_owner_teacher_id_null_owner_returns_none(monkeypatch):
    # Legacy org, pre-backfill: row exists but owner_teacher_id is null.
    router = _AtableRouter({"organizations": [{"owner_teacher_id": None}]})
    monkeypatch.setattr(scope, "_atable", router)
    assert _run(scope._org_owner_teacher_id("org-legacy")) is None


def test_is_billing_owner_superadmin_never_owner():
    teacher = {"id": "s", "org_role": "superadmin", "org_id": "org-1"}
    assert _run(scope.is_billing_owner(teacher)) is False


def test_is_billing_owner_no_org_id_false():
    teacher = {"id": "t", "org_role": "teacher", "org_id": None}
    assert _run(scope.is_billing_owner(teacher)) is False


def test_is_billing_owner_true_for_owner(monkeypatch):
    router = _AtableRouter({"organizations": [{"owner_teacher_id": "teacher-1"}]})
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"id": "teacher-1", "org_role": "teacher", "org_id": "org-1"}
    assert _run(scope.is_billing_owner(teacher)) is True


def test_is_billing_owner_false_for_non_owner_org_member(monkeypatch):
    router = _AtableRouter({"organizations": [{"owner_teacher_id": "teacher-1"}]})
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"id": "teacher-2", "org_role": "teacher", "org_id": "org-1"}
    assert _run(scope.is_billing_owner(teacher)) is False


# ─────────────────────────────────────────────────────────────────────────
# assert_can_author
# ─────────────────────────────────────────────────────────────────────────

def test_assert_can_author_blocks_manager_admin():
    with pytest.raises(HTTPException) as exc:
        scope.assert_can_author({"id": "a", "org_role": "admin"})
    assert exc.value.status_code == 403


def test_assert_can_author_case_insensitive_block():
    with pytest.raises(HTTPException):
        scope.assert_can_author({"id": "a", "org_role": "Admin"})


def test_assert_can_author_allows_teacher_superadmin_and_missing_role():
    scope.assert_can_author({"id": "t", "org_role": "teacher"})
    scope.assert_can_author({"id": "s", "org_role": "superadmin"})
    scope.assert_can_author({"id": "n", "org_role": None})
    scope.assert_can_author({"id": "m"})  # key entirely absent


# ─────────────────────────────────────────────────────────────────────────
# resolve_scope — the three caller categories + edge cases
# ─────────────────────────────────────────────────────────────────────────

def test_resolve_scope_superadmin_passes_through_requested_teacher_id():
    teacher = {"id": "s", "org_role": "superadmin", "org_id": None}
    req = _make_request("teacher_id=teacher-42")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "superadmin", "teacher_id": "teacher-42", "org_id": None}


def test_resolve_scope_superadmin_no_filter_when_absent():
    teacher = {"id": "s", "org_role": "superadmin", "org_id": None}
    req = _make_request("")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "superadmin", "teacher_id": None, "org_id": None}


def test_resolve_scope_admin_with_valid_in_org_filter(monkeypatch):
    router = _AtableRouter({"teachers": [{"id": "teacher-2"}]})  # in-org -> verified
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"id": "teacher-1", "org_role": "admin", "org_id": "org-1"}
    req = _make_request("teacher_id=teacher-2")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "admin", "teacher_id": "teacher-2", "org_id": "org-1"}


def test_resolve_scope_admin_cross_tenant_filter_silently_dropped(monkeypatch):
    # Requested teacher_id is NOT in this org -> _verify_teacher_in_org
    # returns False -> the filter must be dropped (org-wide scope), never
    # leak a cross-tenant teacher_id back to the caller.
    router = _AtableRouter({"teachers": []})  # not found in org
    monkeypatch.setattr(scope, "_atable", router)
    teacher = {"id": "teacher-1", "org_role": "admin", "org_id": "org-1"}
    req = _make_request("teacher_id=mallory-teacher")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "admin", "teacher_id": None, "org_id": "org-1"}


def test_resolve_scope_admin_without_filter_is_org_wide(monkeypatch):
    async def _boom(table_name):
        raise AssertionError("should not verify when no teacher_id requested")

    monkeypatch.setattr(scope, "_atable", _boom)
    teacher = {"id": "teacher-1", "org_role": "admin", "org_id": "org-1"}
    req = _make_request("")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "admin", "teacher_id": None, "org_id": "org-1"}


def test_resolve_scope_admin_without_org_id_falls_back_to_teacher_locked():
    # "admin without an org_id, which shouldn't happen but stays safe":
    # locked to own teacher_id like a plain teacher, ignoring the filter.
    teacher = {"id": "teacher-1", "org_role": "admin", "org_id": None}
    req = _make_request("teacher_id=someone-else")
    result = _run(scope.resolve_scope(teacher, req))
    assert result == {"role": "teacher", "teacher_id": "teacher-1", "org_id": None}


def test_resolve_scope_plain_teacher_ignores_url_filter(monkeypatch):
    async def _boom(table_name):
        raise AssertionError("plain teacher path must not hit the DB")

    monkeypatch.setattr(scope, "_atable", _boom)
    teacher = {"id": "teacher-1", "org_role": "teacher", "org_id": "org-1"}
    req = _make_request("teacher_id=someone-else")
    result = _run(scope.resolve_scope(teacher, req))
    # Locked to own id; the requested filter is ignored entirely (not even
    # dropped-with-a-check, just never consulted for a plain teacher).
    assert result == {"role": "teacher", "teacher_id": "teacher-1", "org_id": "org-1"}


def test_resolve_scope_defaults_missing_org_role_to_teacher():
    teacher = {"id": "teacher-1"}  # org_role key absent entirely
    req = _make_request("")
    result = _run(scope.resolve_scope(teacher, req))
    assert result["role"] == "teacher"
    assert result["teacher_id"] == "teacher-1"


def test_resolve_scope_role_is_case_normalised():
    teacher = {"id": "s", "org_role": "SuperAdmin", "org_id": None}
    req = _make_request("")
    result = _run(scope.resolve_scope(teacher, req))
    assert result["role"] == "superadmin"


def test_resolve_scope_blank_teacher_id_param_treated_as_absent(monkeypatch):
    # ?teacher_id= (empty string) or whitespace-only must resolve to None,
    # not to a truthy empty/blank filter value.
    async def _boom(table_name):
        raise AssertionError("blank filter should short-circuit before DB call")

    monkeypatch.setattr(scope, "_atable", _boom)
    teacher = {"id": "teacher-1", "org_role": "admin", "org_id": "org-1"}
    req = _make_request("teacher_id=+++")  # decodes to whitespace
    result = _run(scope.resolve_scope(teacher, req))
    assert result["teacher_id"] is None


# ─────────────────────────────────────────────────────────────────────────
# _verify_teacher_in_org
# ─────────────────────────────────────────────────────────────────────────

def test_verify_teacher_in_org_true_when_row_found(monkeypatch):
    router = _AtableRouter({"teachers": [{"id": "teacher-2"}]})
    monkeypatch.setattr(scope, "_atable", router)
    assert _run(scope._verify_teacher_in_org("teacher-2", "org-1")) is True
    inst = router._instances[0]
    assert ("id", "teacher-2") in inst.eq_calls
    assert ("org_id", "org-1") in inst.eq_calls


def test_verify_teacher_in_org_false_when_no_row(monkeypatch):
    router = _AtableRouter({"teachers": []})
    monkeypatch.setattr(scope, "_atable", router)
    assert _run(scope._verify_teacher_in_org("teacher-2", "org-1")) is False


# ─────────────────────────────────────────────────────────────────────────
# scope_to_teacher_ids
# ─────────────────────────────────────────────────────────────────────────

def test_scope_to_teacher_ids_single_teacher_filter(monkeypatch):
    async def _boom(table_name):
        raise AssertionError("should not query when a teacher_id filter is set")

    monkeypatch.setattr(scope, "_atable", _boom)
    result = _run(scope.scope_to_teacher_ids({"teacher_id": "teacher-1", "org_id": "org-1"}))
    assert result == ["teacher-1"]


def test_scope_to_teacher_ids_org_wide_lists_all_members(monkeypatch):
    router = _AtableRouter({"teachers": [{"id": "t1"}, {"id": "t2"}]})
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.scope_to_teacher_ids({"teacher_id": None, "org_id": "org-1"}))
    assert result == ["t1", "t2"]


def test_scope_to_teacher_ids_org_wide_empty_org_returns_empty_list(monkeypatch):
    router = _AtableRouter({"teachers": []})
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.scope_to_teacher_ids({"teacher_id": None, "org_id": "org-1"}))
    assert result == []


def test_scope_to_teacher_ids_superadmin_no_filter_returns_none():
    result = _run(scope.scope_to_teacher_ids({"teacher_id": None, "org_id": None}))
    assert result is None


def test_scope_to_teacher_ids_empty_dict_returns_none():
    result = _run(scope.scope_to_teacher_ids({}))
    assert result is None


# ─────────────────────────────────────────────────────────────────────────
# apply_teacher_scope
# ─────────────────────────────────────────────────────────────────────────

class _QuerySpy:
    def __init__(self):
        self.calls = []

    def eq(self, col, val):
        self.calls.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.calls.append(("in_", col, vals))
        return self


def test_apply_teacher_scope_none_means_unfiltered():
    q = _QuerySpy()
    result = scope.apply_teacher_scope(q, None)
    assert result is q
    assert q.calls == []


def test_apply_teacher_scope_empty_list_matches_nothing():
    q = _QuerySpy()
    scope.apply_teacher_scope(q, [])
    assert q.calls == [("eq", "teacher_id", "__none__")]


def test_apply_teacher_scope_single_id_uses_eq():
    q = _QuerySpy()
    scope.apply_teacher_scope(q, ["teacher-1"])
    assert q.calls == [("eq", "teacher_id", "teacher-1")]


def test_apply_teacher_scope_multiple_ids_uses_in():
    q = _QuerySpy()
    scope.apply_teacher_scope(q, ["teacher-1", "teacher-2"])
    assert q.calls == [("in_", "teacher_id", ["teacher-1", "teacher-2"])]


# ─────────────────────────────────────────────────────────────────────────
# assert_session_accessible — the highest-complexity function.
# Branches: teacher delegate / admin direct-row-in-org / admin direct-row
# cross-org (orphan fallback) / superadmin direct-row / no-row synthesis
# for admin, superadmin, and plain-teacher-not-found roles.
# ─────────────────────────────────────────────────────────────────────────

def test_assert_session_accessible_teacher_delegates_to_legacy_helper(monkeypatch):
    calls = {}

    async def _fake_owned(session_id, teacher_id):
        calls["args"] = (session_id, teacher_id)
        return {"session_key": session_id, "teacher_id": teacher_id}

    monkeypatch.setattr("app.repositories.sessions.assert_session_owned", _fake_owned)
    result = _run(scope.assert_session_accessible(
        "sess-1", {"role": "teacher", "teacher_id": "teacher-1", "org_id": None}))
    assert result == {"session_key": "sess-1", "teacher_id": "teacher-1"}
    assert calls["args"] == ("sess-1", "teacher-1")


def test_assert_session_accessible_teacher_delegate_propagates_404(monkeypatch):
    async def _fake_owned(session_id, teacher_id):
        raise HTTPException(status_code=404, detail="Session not found")

    monkeypatch.setattr("app.repositories.sessions.assert_session_owned", _fake_owned)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "sess-mallory", {"role": "teacher", "teacher_id": "teacher-1", "org_id": None}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_superadmin_sees_any_row(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": "some-other-teacher"}],
    })
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.assert_session_accessible(
        "s1", {"role": "superadmin", "teacher_id": None, "org_id": None}))
    assert result == {"session_key": "s1", "teacher_id": "some-other-teacher"}


def test_assert_session_accessible_admin_same_org_row_allowed(monkeypatch):
    def _teachers():
        # _verify_teacher_in_org("teacher-2", "org-1") -> found
        return [{"id": "teacher-2"}]

    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": "teacher-2"}],
        "teachers": _teachers,
    })
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.assert_session_accessible(
        "s1", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert result == {"session_key": "s1", "teacher_id": "teacher-2"}


def test_assert_session_accessible_admin_cross_org_row_denied(monkeypatch):
    # Row exists with a teacher_id, but that teacher is NOT in the admin's
    # org -> must 404, never return the row (the core tenant-isolation
    # guarantee this module exists to provide).
    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": "rival-org-teacher"}],
        "teachers": [],  # _verify_teacher_in_org finds nothing -> False
    })
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "s1", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_admin_orphan_row_with_matching_violation_allowed(monkeypatch):
    # Direct row has no teacher_id (orphan). A violation for the same
    # session ties it to a teacher who IS in the admin's org -> allowed.
    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": ""}],
        "violations": [{"teacher_id": "teacher-2"}],
        "teachers": [{"id": "teacher-2"}],  # in-org
    })
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.assert_session_accessible(
        "s1", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert result == {"session_key": "s1", "teacher_id": ""}


def test_assert_session_accessible_admin_orphan_row_no_matching_violation_denied(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": None}],
        "violations": [{"teacher_id": "rival-org-teacher"}],
        "teachers": [],  # rival-org-teacher not in this admin's org
    })
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "s1", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_admin_orphan_row_zero_violations_denied(monkeypatch):
    # Fail-closed case the fix explicitly targets: orphan row, no
    # violations at all -> must 404, not fall through to granting access.
    router = _AtableRouter({
        "exam_sessions": [{"session_key": "s1", "teacher_id": None}],
        "violations": [],
    })
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "s1", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_no_row_synthesises_from_violation_for_admin(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [],  # no direct row at all
        "violations": [{"teacher_id": "teacher-2"}],
        "teachers": [{"id": "teacher-2"}],
    })
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.assert_session_accessible(
        "roll42_abc", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert result["teacher_id"] == "teacher-2"
    assert result["session_key"] == "roll42_abc"
    assert result["roll_number"] == "roll42"  # split on last "_"
    assert result["status"] is not None


def test_assert_session_accessible_no_row_synthesises_from_violation_for_superadmin(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [],
        "violations": [{"teacher_id": "any-teacher"}],
    })
    monkeypatch.setattr(scope, "_atable", router)
    result = _run(scope.assert_session_accessible(
        "sess_only_violation", {"role": "superadmin", "teacher_id": None, "org_id": None}))
    assert result["teacher_id"] == "any-teacher"


def test_assert_session_accessible_no_row_no_violations_denied(monkeypatch):
    router = _AtableRouter({"exam_sessions": [], "violations": []})
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "ghost_sess", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_no_row_violation_teacher_id_blank_is_skipped(monkeypatch):
    # A violation row with a blank/missing teacher_id must be skipped
    # rather than treated as an in-scope match.
    router = _AtableRouter({
        "exam_sessions": [],
        "violations": [{"teacher_id": ""}, {"teacher_id": None}],
    })
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "ghost_sess", {"role": "superadmin", "teacher_id": None, "org_id": None}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_no_row_cross_org_violation_denied_for_admin(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [],
        "violations": [{"teacher_id": "rival-org-teacher"}],
        "teachers": [],  # not in this admin's org
    })
    monkeypatch.setattr(scope, "_atable", router)
    with pytest.raises(HTTPException) as exc:
        _run(scope.assert_session_accessible(
            "ghost_sess", {"role": "admin", "teacher_id": None, "org_id": "org-1"}))
    assert exc.value.status_code == 404


def test_assert_session_accessible_roll_number_fallback_when_no_underscore(monkeypatch):
    router = _AtableRouter({
        "exam_sessions": [],
        "violations": [{"teacher_id": "any-teacher"}],
    })
    monkeypatch.setattr(scope, "_atable", router)
    long_id = "x" * 30  # no underscore -> first 20 chars used as roll_number
    result = _run(scope.assert_session_accessible(
        long_id, {"role": "superadmin", "teacher_id": None, "org_id": None}))
    assert result["roll_number"] == long_id[:20]
