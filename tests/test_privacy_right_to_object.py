"""Tests for right-to-object (GDPR Art 21 / DPDP Act §9)."""

from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


@pytest.fixture
def mock_teacher():
    with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
        m.return_value = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof Test", "org_id": "org-1"}
        yield m


@pytest.fixture
def mock_student_account():
    with patch("app.auth.admin_auth.verify_student_auth_token", new_callable=AsyncMock) as m:
        m.return_value = {"id": "student-1", "email": "alice@test.com", "full_name": "Alice Test"}
        yield m


def _make_chain(execute_return):
    """Build a fluent mock chain where every method returns self
    and .execute() is an AsyncMock returning *execute_return*."""
    m = MagicMock()
    for attr in ("select", "eq", "limit", "is_", "order", "maybe_single",
                 "neq", "in_", "gte", "lte", "like", "contains", "range",
                 "single", "delete"):
        getattr(m, attr).return_value = m
    # insert/update/upsert return themselves so the chain continues
    for attr in ("insert", "update", "upsert"):
        getattr(m, attr).return_value = m
    m.execute = AsyncMock(return_value=execute_return)
    return m


class TestObjectionAuth:
    def test_object_requires_auth(self):
        r = client.post("/api/v1/privacy/object", json={"grounds": "I object", "scope": "all"})
        assert r.status_code == 401

    def test_invalid_scope_rejected(self, admin_headers, mock_teacher):
        r = client.post("/api/v1/privacy/object", json={
            "grounds": "",
            "scope": "invalid_scope",
        }, headers=admin_headers)
        assert r.status_code == 422


class TestObjectionTeacher:
    def test_teacher_objection_succeeds(self, admin_headers, mock_teacher):
        insert_row = MagicMock(data=[{"id": "obj-1"}])
        org_data = MagicMock(data=[{"name": "Test University", "billing_email": "billing@test.edu"}])

        def mock_atable(table_name):
            if table_name == "objection_records":
                return _make_chain(insert_row)
            elif table_name == "organizations":
                return _make_chain(org_data)
            return _make_chain(MagicMock(data=[]))

        with patch("app.routers.privacy._atable", side_effect=mock_atable), \
             patch("app.routers.privacy.enqueue_job", new_callable=MagicMock) as mock_enqueue:
            r = client.post("/api/v1/privacy/object", json={
                "grounds": "I disagree with automated proctoring",
                "scope": "all",
            }, headers=admin_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
            d = r.json()
            assert d.get("status") == "objection_recorded"
            assert d.get("objection_id") == "obj-1"
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args[1]
            assert call_kwargs["to_email"] == "privacy@procta.net"
            assert call_kwargs["user_type"] == "teacher"

    def test_teacher_objection_no_org(self, admin_headers, mock_teacher):
        insert_row = MagicMock(data=[{"id": "obj-2"}])

        def mock_atable(table_name):
            if table_name == "objection_records":
                return _make_chain(insert_row)
            elif table_name == "organizations":
                return _make_chain(MagicMock(data=None))
            return _make_chain(MagicMock(data=[]))

        with patch("app.routers.privacy._atable", side_effect=mock_atable), \
             patch("app.routers.privacy.enqueue_job", new_callable=MagicMock) as mock_enqueue:
            r = client.post("/api/v1/privacy/object", json={
                "grounds": "",
                "scope": "proctoring",
            }, headers=admin_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
            d = r.json()
            assert d.get("status") == "objection_recorded"
            mock_enqueue.assert_called_once()


class TestObjectionStudent:
    def test_student_objection_succeeds(self, student_headers, mock_student_account):
        insert_row = MagicMock(data=[{"id": "obj-3"}])
        # students/teachers/organizations are list-returning queries in the
        # real builder, not single-row — mock them as lists.
        student_data = MagicMock(data=[{"teacher_id": "teacher-1"}])
        teacher_data = MagicMock(data=[{
            "id": "teacher-1",
            "email": "prof@test.com",
            "full_name": "Prof Test",
            "org_id": "org-1",
        }])
        org_data = MagicMock(data=[{
            "name": "Test University",
            "billing_email": "billing@test.edu",
        }])

        def mock_atable(table_name):
            if table_name == "objection_records":
                return _make_chain(insert_row)
            elif table_name == "students":
                return _make_chain(student_data)
            elif table_name == "teachers":
                return _make_chain(teacher_data)
            elif table_name == "organizations":
                return _make_chain(org_data)
            return _make_chain(MagicMock(data=[]))

        with patch("app.routers.privacy._atable", side_effect=mock_atable), \
             patch("app.routers.privacy.enqueue_job", new_callable=MagicMock) as mock_enqueue:
            r = client.post("/api/v1/privacy/object", json={
                "grounds": "I do not consent to automated analysis",
                "scope": "proctoring",
            }, headers=student_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
            d = r.json()
            assert d.get("status") == "objection_recorded"
            assert d.get("objection_id") == "obj-3"
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args[1]
            assert call_kwargs["to_email"] == "prof@test.com"
            assert call_kwargs["user_type"] == "student"
            assert call_kwargs["org_name"] == "Test University"

    def test_student_objection_no_teacher_found(self, student_headers, mock_student_account):
        insert_row = MagicMock(data=[{"id": "obj-4"}])

        def mock_atable(table_name):
            if table_name == "objection_records":
                return _make_chain(insert_row)
            elif table_name == "students":
                return _make_chain(MagicMock(data=None))
            return _make_chain(MagicMock(data=[]))

        with patch("app.routers.privacy._atable", side_effect=mock_atable), \
             patch("app.routers.privacy.enqueue_job", new_callable=MagicMock) as mock_enqueue:
            r = client.post("/api/v1/privacy/object", json={
                "grounds": "I object",
                "scope": "all",
            }, headers=student_headers)
            assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text[:200]}"
            d = r.json()
            assert d.get("status") == "objection_recorded"
            mock_enqueue.assert_not_called()
