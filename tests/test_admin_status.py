"""Admin operations status endpoint tests."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _chain(count=0, data=None):
    m = MagicMock()
    for attr in ("select", "eq", "gte", "limit"):
        getattr(m, attr).return_value = m
    m.execute = AsyncMock(return_value=MagicMock(data=data or [], count=count))
    return m


def test_admin_status_returns_checks_and_operator_metrics():
    async def fake_require_admin(_request):
        return {"id": "teacher-1", "email": "prof@test.com"}

    def fake_atable(table_name):
        if table_name == "exam_config":
            return _chain(data=[{"id": "cfg-1"}])
        if table_name == "exam_sessions":
            return _chain(count=3)
        if table_name == "violations":
            return _chain(count=1)
        return _chain()

    with patch("app.routers.admin_status.require_admin", side_effect=fake_require_admin), \
         patch("app.database.async_table", side_effect=fake_atable):
        resp = client.get("/api/v1/admin/status", headers={"Authorization": "Bearer test"})

    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "metrics" in body
    assert "release" in body
    assert body["metrics"]["active_sessions"] == 3
    assert body["metrics"]["submit_failures_24h"] == 1
    assert body["health_checks"] >= 1
