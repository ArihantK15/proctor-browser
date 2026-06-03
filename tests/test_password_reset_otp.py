from unittest.mock import AsyncMock, MagicMock, patch


def test_student_password_reset_otp_request_is_generic(client):
    with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
         patch("app.routers.auth._track_b_send_password_reset_otp", new=AsyncMock()) as send_mock:
        resp = client.post("/api/v1/student/auth/reset-request", json={
            "email": "student@example.com",
        })

    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    send_mock.assert_awaited_once_with("student", "student@example.com")


def test_logged_in_student_password_reset_otp_skips_turnstile_for_own_email(client):
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value={
            "id": "student-1",
            "email": "student@example.com",
            "full_name": "Student",
         })), \
         patch("app.routers.auth.verify_or_403", new=AsyncMock()) as turnstile_mock, \
         patch("app.routers.auth._track_b_send_password_reset_otp", new=AsyncMock()) as send_mock:
        resp = client.post("/api/v1/student/auth/reset-request", json={
            "email": "student@example.com",
        })

    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    turnstile_mock.assert_not_awaited()
    send_mock.assert_awaited_once_with("student", "student@example.com")


def test_student_password_reset_otp_confirm_updates_password(client):
    user = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    with patch("app.routers.auth._track_b_find_user_for_reset", new=AsyncMock(return_value=user)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=True)) as verify_mock, \
         patch("app.routers.auth._track_b_set_password", new=AsyncMock()) as set_mock:
        resp = client.post("/api/v1/student/auth/reset-confirm", json={
            "email": "student@example.com",
            "code": "123456",
            "new_password": "NewStrong1!",
        })

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    verify_mock.assert_awaited_once_with("student", "student-1", "password_reset", "123456")
    assert set_mock.await_args.args[0] == "student"
    assert set_mock.await_args.args[1] == user
    assert set_mock.await_args.args[2] == "NewStrong1!"


def test_teacher_password_reset_otp_flow_uses_teacher_purpose(client):
    user = {"id": "teacher-1", "email": "teacher@example.com", "full_name": "Teacher"}
    with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
         patch("app.routers.auth._track_b_send_password_reset_otp", new=AsyncMock()) as send_mock:
        request_resp = client.post("/api/v1/teacher/auth/reset-request", json={
            "email": "teacher@example.com",
        })

    assert request_resp.status_code == 200
    send_mock.assert_awaited_once_with("teacher", "teacher@example.com")

    with patch("app.routers.auth._track_b_find_user_for_reset", new=AsyncMock(return_value=user)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=True)) as verify_mock, \
         patch("app.routers.auth._track_b_set_password", new=AsyncMock()) as set_mock:
        confirm_resp = client.post("/api/v1/teacher/auth/reset-confirm", json={
            "email": "teacher@example.com",
            "code": "654321",
            "new_password": "TeacherStrong1!",
        })

    assert confirm_resp.status_code == 200
    verify_mock.assert_awaited_once_with("teacher", "teacher-1", "teacher_password_reset", "654321")
    assert set_mock.await_args.args[0] == "teacher"


def test_password_reset_otp_rejects_cross_purpose_code(client):
    user = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    with patch("app.routers.auth._track_b_find_user_for_reset", new=AsyncMock(return_value=user)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=False)):
        resp = client.post("/api/v1/student/auth/reset-confirm", json={
            "email": "student@example.com",
            "code": "123456",
            "new_password": "NewStrong1!",
        })

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Invalid or expired code"
