"""POST /api/v1/client-diagnostic — the only capture path for failures that
happen BEFORE the user has a session (login form 'Failed to fetch', Electron
lobby did-fail-load). Confirmed 2026-07-08: the Electron renderer has no
Sentry SDK wired in and the client swallowed these errors entirely, so a
recurring field report had zero real telemetry across multiple debugging
sessions. This endpoint forwards to Sentry server-side instead.
"""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_accepts_minimal_payload():
    resp = client.post("/api/v1/client-diagnostic", json={"context": "login_submit"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_accepts_full_payload():
    resp = client.post("/api/v1/client-diagnostic", json={
        "context": "student_login",
        "error_name": "TypeError",
        "error_message": "Failed to fetch",
        "target": "https://app.procta.net/api/v1/student/auth/login",
        "app_version": "2.5.6",
        "platform": "Win32",
    })
    assert resp.status_code == 200, resp.text


def test_rejects_unknown_fields():
    # strict model — protects against a client accidentally sending free-text
    # PII fields that were never declared.
    resp = client.post("/api/v1/client-diagnostic", json={
        "context": "login_submit", "user_email": "someone@example.com",
    })
    assert resp.status_code == 422


def test_rejects_oversized_fields():
    resp = client.post("/api/v1/client-diagnostic", json={
        "context": "x" * 1000,
    })
    assert resp.status_code == 422


def test_forwards_to_sentry_when_configured():
    with patch("app.routers.public.sentry_sdk") as mock_sentry:
        resp = client.post("/api/v1/client-diagnostic", json={
            "context": "login_submit", "error_name": "TypeError",
            "error_message": "Failed to fetch",
        })
    assert resp.status_code == 200
    mock_sentry.capture_message.assert_called_once()

def test_never_fails_when_sentry_unavailable():
    # Dev/test environments without sentry_sdk configured must not 500 —
    # this is best-effort observability, not load-bearing.
    resp = client.post("/api/v1/client-diagnostic", json={"context": "lobby_load"})
    assert resp.status_code == 200


def test_no_auth_required():
    # The entire point: this must be reachable BEFORE the user has a session.
    resp = client.post("/api/v1/client-diagnostic", json={"context": "login_submit"},
                       headers={})
    assert resp.status_code == 200
