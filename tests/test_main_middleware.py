"""app/main.py wires ~20 routers and 6 security middlewares (CSRF, security
headers, input validation, ETag, request-ID, structured logging) but had
zero test coverage despite 41 dependents and a flagged 'hidden coupling'
biomarker (files changing together with no import link). These are the
security-critical pieces most worth pinning down first.
"""
from __future__ import annotations

from tests.conftest import make_admin_token


class TestMaliciousInputDetection:
    def test_sqli_tautology_detected(self):
        from app.main import _looks_malicious
        assert _looks_malicious("' OR '1'='1")
        assert _looks_malicious("admin' AND 1=1")

    def test_sqli_stacked_query_detected(self):
        from app.main import _looks_malicious
        assert _looks_malicious("x'; DROP TABLE users; --")
        assert _looks_malicious("'; DELETE FROM sessions")

    def test_normal_prose_with_and_or_not_flagged(self):
        # The regex requires the tautology/comparison shape — plain prose
        # joined by "and"/"or" must not false-positive on every free-text
        # field (exam answers, teacher notes, etc).
        from app.main import _looks_malicious
        assert not _looks_malicious("I like cats and dogs")
        assert not _looks_malicious("either the phone or the earbuds were visible")

    def test_json_recurses_into_nested_dicts_and_lists(self):
        from app.main import _json_contains_malicious
        assert _json_contains_malicious({"a": {"b": ["safe", "'; DROP TABLE x;"]}})
        assert not _json_contains_malicious({"a": {"b": ["safe", "still safe"]}})


class TestSafeJSONResponse:
    def test_serializes_uuid_datetime_date_bytes(self):
        import uuid
        from datetime import datetime, date, timezone
        from app.main import _SafeJSONResponse

        u = uuid.uuid4()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        d = date(2026, 1, 1)
        body = _SafeJSONResponse(
            content={"id": u, "at": now, "day": d, "raw": b"hi"}
        ).render({"id": u, "at": now, "day": d, "raw": b"hi"})
        text = body.decode("utf-8")
        assert str(u) in text
        assert now.isoformat() in text
        assert d.isoformat() in text
        assert "hi" in text

    def test_unsupported_type_raises_typeerror(self):
        from app.main import _SafeJSONResponse

        class Unserializable:
            pass

        try:
            _SafeJSONResponse(content={}).render({"x": Unserializable()})
            assert False, "expected TypeError"
        except TypeError:
            pass


class TestSecurityHeaders:
    def test_core_security_headers_present(self, client):
        resp = client.get("/api/v1/metrics")  # any real route; headers apply regardless of auth outcome
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in resp.headers
        assert "max-age=31536000" in resp.headers.get("Strict-Transport-Security", "")
        assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin"

    def test_camera_permission_denied_by_default(self, client):
        resp = client.get("/api/v1/metrics")
        assert resp.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"

    def test_camera_permission_allowed_on_student_paths(self, client):
        resp = client.get("/student")
        assert "camera=(self)" in resp.headers.get("Permissions-Policy", "")


class TestCsrfMiddleware:
    def test_exempt_suffixes_cover_auth_bootstrap_routes(self):
        from app.main import _CSRF_EXEMPT_SUFFIXES
        for path in ("/auth/login", "/auth/signup", "/auth/refresh", "/auth/logout"):
            assert path in _CSRF_EXEMPT_SUFFIXES

    def test_cookie_auth_without_csrf_header_is_blocked(self, client):
        token = make_admin_token()
        client.cookies.set("procta_access", token)
        resp = client.post("/api/v1/auth/change-password", json={})
        assert resp.status_code == 403
        assert "CSRF token required" in resp.text

    def test_bearer_auth_is_not_csrf_gated(self, client):
        # Native/API bearer-token flows are intentionally exempt (the CSRF
        # threat model is browser cookie auth, not a stolen bearer token
        # the caller already had to obtain directly).
        token = make_admin_token()
        resp = client.post(
            "/api/v1/auth/change-password",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_disable_csrf_for_tests_escape_hatch(self, client):
        from app.main import app
        token = make_admin_token()
        client.cookies.set("procta_access", token)
        app.state.disable_csrf_for_tests = True
        try:
            resp = client.post("/api/v1/auth/change-password", json={})
            assert "CSRF token required" not in resp.text
        finally:
            app.state.disable_csrf_for_tests = False
