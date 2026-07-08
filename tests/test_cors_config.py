"""CORS preflight coverage — this exact gap caused a real, hard-to-diagnose
production bug: student-app.js added X-Procta-App-Attestation/-Signature
headers to the Electron login/signup fetch (bcfb1b32) without updating
CORSMiddleware's allow_headers, so any signed production build that
successfully generated an attestation had its login preflight rejected
with 400 — which surfaces to the renderer as a plain, indistinguishable-
from-network "TypeError: Failed to fetch" (confirmed via Sentry, 2026-07-08,
student_login / Electron / Windows / v2.6.1). Dev builds never hit this
because getAppAttestation() has no valid signing key to work with there,
so the bug was invisible until a real signed build was tested.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _preflight(extra_request_headers: str):
    return client.options(
        "/api/v1/student/auth/login",
        headers={
            "Origin": "procta-lobby://lobby",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": extra_request_headers,
        },
    )


def test_preflight_allows_app_attestation_headers():
    resp = _preflight("content-type,x-procta-app-attestation,x-procta-app-signature")
    assert resp.status_code == 200, resp.text
    allowed = resp.headers.get("access-control-allow-headers", "")
    assert "X-Procta-App-Attestation" in allowed
    assert "X-Procta-App-Signature" in allowed


def test_preflight_allows_procta_lobby_origin():
    resp = _preflight("content-type")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("access-control-allow-origin") == "procta-lobby://lobby"
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_preflight_still_rejects_unlisted_headers():
    # Guards against a future header being added to a fetch() call without
    # this list being updated too — the exact mistake that caused the bug
    # this file exists to catch.
    resp = _preflight("content-type,x-some-made-up-header")
    assert resp.status_code == 400
