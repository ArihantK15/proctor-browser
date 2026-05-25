from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request


def _request_with_cookie(cookie: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", cookie.encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
    })


@pytest.mark.asyncio
async def test_require_admin_accepts_httponly_cookie():
    from app.auth.admin_auth import require_admin

    req = _request_with_cookie("procta_access=teacher-token")
    teacher = {"id": "teacher-1", "email": "teacher@example.test", "org_role": "teacher"}
    with patch("app.auth.admin_auth.verify_admin_token", new=AsyncMock(return_value=teacher)) as verify:
        assert await require_admin(req) == teacher
        verify.assert_awaited_once_with("teacher-token")


@pytest.mark.asyncio
async def test_require_student_account_accepts_httponly_cookie():
    from app.auth.admin_auth import require_student_account

    req = _request_with_cookie("procta_student_access=student-token")
    account = {"id": "student-1", "email": "student@example.test", "full_name": "Student"}
    with patch("app.auth.admin_auth.verify_student_auth_token", new=AsyncMock(return_value=account)) as verify:
        assert await require_student_account(req) == account
        verify.assert_awaited_once_with("student-token")


@pytest.mark.asyncio
async def test_csrf_issue_accepts_account_cookie():
    from app.routers.auth import issue_csrf

    req = _request_with_cookie("procta_access=teacher-token")
    claims = {"role": "teacher", "jti": "jti-1"}
    with patch("app.routers.auth._decode_token", return_value=claims) as decode, \
         patch("app.routers.auth.issue_csrf_token", return_value="csrf-secret") as issue:
        assert await issue_csrf(req) == {"csrf_token": "csrf-secret"}
        decode.assert_called_once()
        issue.assert_called_once_with(claims)


@pytest.mark.asyncio
async def test_teacher_refresh_accepts_refresh_cookie_and_sets_cookies():
    from app.routers.auth import teacher_refresh

    req = _request_with_cookie("procta_refresh=refresh-token")
    teacher = {"id": "teacher-1", "email": "teacher@example.test", "org_role": "teacher"}
    with patch("app.routers.auth._verify_and_rotate_refresh_token", new=AsyncMock(return_value=("teacher-1", "new-refresh"))) as rotate, \
         patch("app.routers.auth._get_teacher_by_id", new=AsyncMock(return_value=teacher)), \
         patch("app.routers.auth.issue_admin_token", return_value="new-access"):
        response = await teacher_refresh(req)
        rotate.assert_awaited_once_with("refresh-token", "teacher", req)
        set_cookie = " ".join(response.headers.getlist("set-cookie"))
        assert "procta_access=new-access" in set_cookie
        assert "procta_refresh=new-refresh" in set_cookie


@pytest.mark.asyncio
async def test_student_refresh_accepts_refresh_cookie_and_sets_cookies():
    from app.routers.auth import student_refresh

    req = _request_with_cookie("procta_student_refresh=refresh-token")
    account = {"id": "student-1", "email": "student@example.test", "full_name": "Student"}
    with patch("app.routers.auth._verify_and_rotate_refresh_token", new=AsyncMock(return_value=("student-1", "new-refresh"))) as rotate, \
         patch("app.routers.auth._get_student_account_by_id", new=AsyncMock(return_value=account)), \
         patch("app.routers.auth.issue_student_auth_token", return_value="new-access"):
        response = await student_refresh(req)
        rotate.assert_awaited_once_with("refresh-token", "student", req)
        set_cookie = " ".join(response.headers.getlist("set-cookie"))
        assert "procta_student_access=new-access" in set_cookie
        assert "procta_student_refresh=new-refresh" in set_cookie
