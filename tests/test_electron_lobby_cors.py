"""Electron lobby CORS regressions."""


def test_electron_lobby_origin_can_preflight_authenticated_student_api(client):
    response = client.options(
        "/api/student/exams",
        headers={
            "Origin": "procta-lobby://lobby",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "procta-lobby://lobby"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_electron_exam_window_origin_can_preflight_api(client):
    """The EXAM window's origin is procta-lobby://exam (d34926a moved it off
    file:// to fix a packaged-Windows ERR_FILE_NOT_FOUND). Every exam-window
    call — id-verification, save-answers-bulk, heartbeat, submit-exam, event —
    is cross-origin. When this origin was NOT allow-listed, all of them failed
    the CORS preflight ("Failed to fetch"), which also meant the teacher
    dashboard never received the student's ID/heartbeat/events. Regression
    guard for that missing origin."""
    response = client.options(
        "/api/v1/event",
        headers={
            "Origin": "procta-lobby://exam",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "procta-lobby://exam"
    assert response.headers.get("access-control-allow-credentials") == "true"
