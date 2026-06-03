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
