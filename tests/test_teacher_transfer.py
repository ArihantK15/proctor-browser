"""Tests for teacher reassign / offboarding — service + endpoint."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.teacher_transfer import (
    reassign_teaching_data,
    _MOVE_TABLES,
    _KEEP_TABLES,
)

client = TestClient(app)


# ─── Phase 1 / Task 1: service — move/keep classification ───────────

class TestReassignServiceClassification:
    """reassign_teaching_data must UPDATE every MOVE table and NO KEEP table."""

    def test_reassign_updates_only_teaching_tables(self):
        calls: list[str] = []

        class _Conn:
            async def execute(self, sql, *a):
                calls.append(sql)
                return "UPDATE 1"

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            reassign_teaching_data(_Conn(), "A", "B"))

        moved = {t for t in _MOVE_TABLES if any(f"UPDATE {t} " in c for c in calls)}
        assert moved == set(_MOVE_TABLES), (
            f"MOVE tables not fully covered: expected {set(_MOVE_TABLES)} got {moved}")

        for t in _KEEP_TABLES:
            assert not any(f"UPDATE {t} " in c for c in calls), (
                f"{t} must NOT be moved")

    def test_reassign_count_parsing(self):
        """Row counts are parsed correctly from the asyncpg tag string."""
        calls: list[str] = []

        class _Conn:
            async def execute(self, sql, *a):
                calls.append(sql)
                return "UPDATE 42"

        import asyncio
        counts = asyncio.get_event_loop().run_until_complete(
            reassign_teaching_data(_Conn(), "A", "B"))

        for table in _MOVE_TABLES:
            assert counts[table] == 42, f"Expected 42 for {table}, got {counts[table]}"


# ─── Phase 2 / Task 2: endpoint authz + validation ──────────────────

def _make_teacher_row(teacher_id: str, org_id: str = "org-1",
                      org_role: str = "admin") -> MagicMock:
    """Build a MagicMock that looks like an _atable result for teacher lookups."""
    return MagicMock(data=[{
        "id": teacher_id,
        "org_id": org_id,
        "org_role": org_role,
        "email": f"{teacher_id}@test.com",
        "full_name": teacher_id.replace("-", " ").title(),
    }])


def _admin_headers(teacher_id: str = "teacher-1",
                    email: str = "prof@test.com") -> dict:
    """Generate a valid admin JWT for test requests."""
    from tests.conftest import make_admin_token
    return {"Authorization": f"Bearer {make_admin_token(teacher_id, email)}"}


@pytest.fixture(autouse=True)
def _patch_common():
    """Patch _get_teacher_by_id so require_admin returns our admin user.

    Individual tests that need a DIFFERENT teacher override this patch
    by nesting another ``patch("app.auth.admin_auth._get_teacher_by_id")``.
    """
    with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
        m.return_value = {
            "id": "teacher-1",
            "email": "prof@test.com",
            "full_name": "Prof Test",
            "org_id": "org-1",
            "org_role": "admin",
        }
        yield


class TestReassignEndpointAuth:
    """Non-admin callers must get 403."""

    def test_teacher_cannot_reassign(self):
        """A plain teacher (org_role='teacher') gets 403."""
        with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
            m.return_value = {
                "id": "teacher-1",
                "email": "teacher@test.com",
                "org_id": "org-1",
                "org_role": "teacher",
            }
            r = client.post(
                "/api/v1/admin/teachers/from-1/reassign",
                json={"to_teacher_id": "to-1"},
                headers=_admin_headers(),
            )
        assert r.status_code == 403, r.text


class TestReassignEndpointValidation:
    """Input validation: same-org check, self-transfer, etc."""

    def test_target_not_in_org(self):
        """to_teacher_id must belong to the same org as the caller."""
        execute_call = [0]

        def _mock_atable(table_name):
            mq = MagicMock()
            mq.select.return_value = mq
            mq.eq.return_value = mq
            mq.limit.return_value = mq
            mq.insert.return_value = mq
            if table_name == "teachers":
                # Return data for the first call (from-1 found), empty for
                # the second (to-outside not found).
                async def _exec():
                    execute_call[0] += 1
                    if execute_call[0] == 1:
                        return MagicMock(data=[{"id": "from-1", "org_id": "org-1"}])
                    return MagicMock(data=[])
            else:
                async def _exec():
                    return MagicMock(data=[])
            mq.execute = _exec
            return mq

        with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
            m.return_value = {
                "id": "teacher-1", "email": "prof@test.com",
                "org_id": "org-1", "org_role": "admin",
            }
            with patch("app.routers.admin_org._atable", side_effect=_mock_atable):
                r = client.post(
                    "/api/v1/admin/teachers/from-1/reassign",
                    json={"to_teacher_id": "to-outside"},
                    headers=_admin_headers(),
                )
        assert r.status_code in (400, 404), r.text
        assert "receiving teacher must already be in your organization" in r.text.lower()

    def test_self_transfer_rejected(self):
        """from_id == to_id → 400."""
        r = client.post(
            "/api/v1/admin/teachers/from-1/reassign",
            json={"to_teacher_id": "from-1"},
            headers=_admin_headers(),
        )
        assert r.status_code == 400, r.text
        assert "same" in r.text.lower() or "self" in r.text.lower()

    def test_missing_to_teacher_id(self):
        """Missing body field → 422."""
        r = client.post(
            "/api/v1/admin/teachers/from-1/reassign",
            json={},
            headers=_admin_headers(),
        )
        assert r.status_code == 422

    def test_from_teacher_not_found(self):
        """Source teacher not in org → 404."""
        def _mock_atable(table_name):
            mq = MagicMock()
            mq.select.return_value = mq
            mq.eq.return_value = mq
            mq.limit.return_value = mq
            mq.insert.return_value = mq
            mq.execute = AsyncMock(return_value=MagicMock(data=[]))
            return mq

        with patch("app.routers.admin_org._atable", side_effect=_mock_atable):
            r = client.post(
                "/api/v1/admin/teachers/nonexistent/reassign",
                json={"to_teacher_id": "to-1"},
                headers=_admin_headers(),
            )
        assert r.status_code == 404, r.text


class TestReassignEndpointHappyPath:
    """Successful reassignment returns 200 with counts."""

    def test_happy_path(self):
        """Admin → reassign → 200 with per-table counts."""
        call_log: list[str] = []
        teacher_lookup_count = [0]

        async def _fake_execute(sql, *a):
            call_log.append(sql)
            return "UPDATE 1"

        class _FakeTxCtx:
            async def __aenter__(self):
                pass
            async def __aexit__(self, *a):
                pass

        class _FakeConn:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass
            async def execute(self, sql, *a):
                return await _fake_execute(sql, *a)
            def transaction(self):
                return _FakeTxCtx()

        class _FakePool:
            def acquire(self):
                return _FakeConn()

        def _mock_atable(table_name):
            mq = MagicMock()
            mq.select.return_value = mq
            mq.eq.return_value = mq
            mq.limit.return_value = mq
            mq.insert.return_value = mq
            # Return a teacher row for the first two teacher lookups
            if table_name == "teachers":
                async def _exec():
                    teacher_lookup_count[0] += 1
                    if teacher_lookup_count[0] <= 2:
                        return MagicMock(data=[{"id": "from-1", "org_id": "org-1"}])
                    return MagicMock(data=[])
            else:
                async def _exec():
                    return MagicMock(data=[])
            mq.execute = _exec
            return mq

        with patch("app.postgres_table.get_pool", return_value=_FakePool()):
            with patch("app.routers.admin_org._atable", side_effect=_mock_atable):
                r = client.post(
                    "/api/v1/admin/teachers/from-1/reassign",
                    json={"to_teacher_id": "to-1"},
                    headers=_admin_headers(),
                )

        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, dict)
        assert "counts" in data
        # Should have run UPDATE calls for all MOVE tables
        update_calls = [s for s in call_log if s.startswith("UPDATE")]
        assert len(update_calls) == len(_MOVE_TABLES), (
            f"Expected {len(_MOVE_TABLES)} UPDATEs, got {len(update_calls)}")
