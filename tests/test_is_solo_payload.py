"""Endpoint test: /api/v1/auth/me surfaces the is_solo signal."""
from unittest.mock import patch, AsyncMock

from tests.conftest import make_admin_token  # noqa: E402


def test_me_payload_includes_is_solo(client):
    teacher = {"id": "a1", "email": "a@x.com", "full_name": "A",
               "org_id": "orgA", "org_role": "admin", "email_verified_at": None}
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=teacher), \
         patch("app.routers.auth.org_is_solo", AsyncMock(return_value=False)):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {make_admin_token(teacher_id='a1', email='a@x.com')}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_solo"] is False
    assert body["org_role"] == "admin"
