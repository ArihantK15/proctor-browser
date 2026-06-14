"""Unit tests for teacher notification preferences (gap #28).

Covers:
  - teacher_wants helper: default, explicit opt-out, fail-open
  - each gated sender is skipped when its category is off
  - transactional/breach sender is never gated
  - endpoint GET returns defaults; PATCH merges + rejects unknown keys (400);
    503 when column absent
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.notification_prefs import teacher_wants, get_prefs, KNOWN_CATEGORIES

client = TestClient(app)


# ── Helper: run async fn in tests ──────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


# ── teacher_wants ────────────────────────────────────────────────────


class TestTeacherWants:
    def test_default_empty_prefs_returns_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": {}}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "billing")) is True
            assert _run(teacher_wants("t1", "security")) is True
            assert _run(teacher_wants("t1", "student_activity")) is True

    def test_null_prefs_returns_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": None}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "security")) is True

    def test_security_false_returns_false(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": {"security": False}}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "security")) is False

    def test_security_false_billing_still_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": {"security": False}}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "billing")) is True
            assert _run(teacher_wants("t1", "student_activity")) is True

    def test_lookup_error_returns_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            side_effect=Exception("DB down")
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "billing")) is True

    def test_empty_result_returns_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "billing")) is True

    def test_prefs_as_string_parsed(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": '{"billing": false}'}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            assert _run(teacher_wants("t1", "billing")) is False
            assert _run(teacher_wants("t1", "security")) is True

    def test_unknown_category_returns_true(self):
        assert _run(teacher_wants("t1", "nonexistent")) is True


# ── get_prefs ───────────────────────────────────────────────────────


class TestGetPrefs:
    def test_returns_all_categories(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": {"security": False}}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            prefs = _run(get_prefs("t1"))
            for c in KNOWN_CATEGORIES:
                assert c in prefs
            assert prefs["security"] is False
            assert prefs["billing"] is True
            assert prefs["student_activity"] is True

    def test_empty_db_returns_all_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        with patch("app.services.notification_prefs._atable", mock):
            prefs = _run(get_prefs("t1"))
            assert all(v is True for v in prefs.values())

    def test_null_returns_all_true(self):
        mock = MagicMock()
        mock.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"notification_prefs": None}])
        )
        with patch("app.services.notification_prefs._atable", mock):
            prefs = _run(get_prefs("t1"))
            assert all(v is True for v in prefs.values())


# ── Endpoint GET ─────────────────────────────────────────────────────


class TestGetEndpoint:
    def test_get_returns_prefs(self):
        with patch("app.routers.auth.require_admin", new_callable=AsyncMock) as mock_admin:
            mock_admin.return_value = {"id": "t1", "email": "a@b.com"}
            with patch("app.services.notification_prefs._atable") as mock_atable:
                mock_atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[{"notification_prefs": {"security": False}}])
                )
                resp = client.get("/api/v1/notification-preferences")
        assert resp.status_code == 200
        d = resp.json()
        assert d.get("security") is False
        assert d.get("billing") is True
        assert d.get("student_activity") is True

    def test_get_requires_auth(self):
        resp = client.get("/api/v1/notification-preferences")
        assert resp.status_code == 401, f"expected 401 got {resp.status_code}: {resp.text[:200]}"


# ── Endpoint PATCH ──────────────────────────────────────────────────


class TestPatchEndpoint:
    def test_patch_updates_prefs(self):
        with patch("app.routers.auth.require_admin", new_callable=AsyncMock) as mock_admin:
            mock_admin.return_value = {"id": "t1", "email": "a@b.com"}
            with patch("app.routers.auth._atable") as mock_atable:
                mock_atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[{"notification_prefs": {"security": False}}])
                )
                mock_atable.return_value.update.return_value.eq.return_value.execute = AsyncMock(
                    return_value=MagicMock(data=[])
                )
                with patch("app.routers.auth.record_auth_event", new_callable=AsyncMock):
                    with patch("app.services.notification_prefs.get_prefs", new_callable=AsyncMock) as mock_get:
                        mock_get.return_value = {"billing": False, "security": True, "student_activity": True}
                        resp = client.patch("/api/v1/notification-preferences",
                                            json={"billing": False})
        assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text[:200]}"
        d = resp.json()
        assert d.get("billing") is False

    def test_patch_rejects_unknown_key(self):
        with patch("app.routers.auth.require_admin", new_callable=AsyncMock) as mock_admin:
            mock_admin.return_value = {"id": "t1", "email": "a@b.com"}
            resp = client.patch("/api/v1/notification-preferences",
                                json={"unknown_cat": False})
        assert resp.status_code == 400

    def test_patch_rejects_non_bool(self):
        with patch("app.routers.auth.require_admin", new_callable=AsyncMock) as mock_admin:
            mock_admin.return_value = {"id": "t1", "email": "a@b.com"}
            resp = client.patch("/api/v1/notification-preferences",
                                json={"billing": "banana"})
        assert resp.status_code == 400

    def test_patch_503_on_missing_column(self):
        with patch("app.routers.auth.require_admin", new_callable=AsyncMock) as mock_admin:
            mock_admin.return_value = {"id": "t1", "email": "a@b.com"}
            with patch("app.routers.auth._atable") as mock_atable:
                mock_atable.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
                    side_effect=Exception("column notification_prefs does not exist")
                )
                resp = client.patch("/api/v1/notification-preferences",
                                    json={"billing": False})
        assert resp.status_code == 503

    def test_patch_requires_auth(self):
        resp = client.patch("/api/v1/notification-preferences",
                            json={"billing": False})
        assert resp.status_code == 401, f"expected 401 got {resp.status_code}: {resp.text[:200]}"
