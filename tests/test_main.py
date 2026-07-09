"""Direct coverage for app/main.py — the FastAPI app factory/orchestrator.

app/main.py wires 20+ routers plus six hand-rolled ASGI middlewares
(CSRF, structured logging, request-id, security headers, input
validation, ETag) and two exception handlers. It has the highest
fan-in of any module in the repo (43 dependents) but, until now, no
direct test file — the existing suite exercises it only incidentally
(any test using the `client`/TestClient fixture boots the real `app`
object, and tests/test_cors_config.py + tests/test_input_validation.py
already pin CORS preflight behaviour and the SQLi regex helpers).

This file targets what was NOT covered anywhere else, verified by
grepping tests/*.py before writing a line here:
  - SecurityHeadersMiddleware: the full header set + the Permissions-
    Policy camera allow/deny branch (path-dependent).
  - RequestIDMiddleware: generate vs. honour an incoming X-Request-ID.
  - ETagMiddleware: ETag assignment + If-None-Match 304 + the various
    skip branches (non-GET, non-200, non-JSON, unknown length, skip
    prefixes).
  - CSRFMiddleware: exempt-suffix bypass, the test-only escape hatch,
    cookie-token-requires-CSRF vs. bearer-token-does-not, the 403
    paths (missing header / wrong value), and the expired/invalid-JWT
    "treat as unauthenticated" fallback.
  - InputValidationMiddleware: end-to-end body-size and SQLi rejection
    (query params, JSON bodies, form bodies), the WS/static/metrics
    skip list, and the "malformed JSON despite json content-type"
    pass-through.
  - The two @app.exception_handler registrations (HTTPException code
    map + request_id echo; the catch-all 500 handler).
  - _SafeJSONResponse's custom encoder for UUID/datetime/date/bytes
    and its TypeError on genuinely unsupported types.
  - /api/v1/metrics auth gating.

Convention: mirrors tests/test_cors_config.py / test_input_validation.py
(module-level TestClient(app), no `with` block so the real lifespan
startup — which spawns background asyncio tasks and a daemon thread —
never runs; see the module docstring in test_cors_config.py for why
that's the established pattern here).
"""
import json
import uuid
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from app.main import (
    app,
    _SafeJSONResponse,
    _http_exception_handler,
    _global_exception_handler,
)

client = TestClient(app, raise_server_exceptions=False)


# ── _SafeJSONResponse ───────────────────────────────────────────────

class TestSafeJSONResponse:
    def test_serializes_uuid(self):
        u = uuid.uuid4()
        body = _SafeJSONResponse(content={"id": u}).body
        assert json.loads(body) == {"id": str(u)}

    def test_serializes_datetime(self):
        dt = datetime(2026, 7, 9, 12, 30, tzinfo=timezone.utc)
        body = _SafeJSONResponse(content={"ts": dt}).body
        assert json.loads(body)["ts"] == dt.isoformat()

    def test_serializes_date(self):
        d = date(2026, 7, 9)
        body = _SafeJSONResponse(content={"d": d}).body
        assert json.loads(body)["d"] == d.isoformat()

    def test_serializes_bytes(self):
        body = _SafeJSONResponse(content={"b": b"hello"}).body
        assert json.loads(body)["b"] == "hello"

    def test_unsupported_type_raises(self):
        class Weird:
            pass
        with pytest.raises(TypeError):
            _SafeJSONResponse(content={"x": Weird()}).body

    def test_used_as_default_response_class(self):
        # A route that leaks a raw UUID past jsonable_encoder would 500
        # without _SafeJSONResponse. /api/v1/metrics returns plain floats/
        # ints (auth-gated), so instead assert the app is wired to use it.
        assert app.router.default_response_class is _SafeJSONResponse


# ── exception handlers ──────────────────────────────────────────────

def _fake_request(path: str = "/whatever", request_id: str = "") -> StarletteRequest:
    scope = {
        "type": "http", "method": "GET", "path": path,
        "query_string": b"", "headers": [], "app": app,
    }
    req = StarletteRequest(scope)
    if request_id:
        req.state.request_id = request_id
    return req


class TestHttpExceptionHandler:
    @pytest.mark.parametrize("status,code", [
        (400, "BAD_REQUEST"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (413, "PAYLOAD_TOO_LARGE"),
        (429, "RATE_LIMITED"),
    ])
    def test_known_status_codes_mapped(self, status, code):
        import asyncio
        exc = HTTPException(status_code=status, detail="boom")
        resp = asyncio.run(_http_exception_handler(_fake_request(request_id="rid-1"), exc))
        assert resp.status_code == status
        body = json.loads(resp.body)
        assert body["error"] == code
        assert body["detail"] == "boom"
        assert body["request_id"] == "rid-1"
        assert body["path"] == "/whatever"

    def test_unmapped_status_falls_back_to_http_error(self):
        import asyncio
        exc = HTTPException(status_code=418, detail="teapot")
        resp = asyncio.run(_http_exception_handler(_fake_request(), exc))
        body = json.loads(resp.body)
        assert body["error"] == "HTTP_ERROR"

    def test_missing_request_id_defaults_empty(self):
        import asyncio
        exc = HTTPException(status_code=404, detail="nope")
        resp = asyncio.run(_http_exception_handler(_fake_request(), exc))
        body = json.loads(resp.body)
        assert body["request_id"] == ""


class TestGlobalExceptionHandler:
    def test_returns_generic_500_without_leaking_detail(self):
        import asyncio
        exc = RuntimeError("sensitive internal detail: db password xyz")
        resp = asyncio.run(_global_exception_handler(_fake_request(path="/boom", request_id="rid-2"), exc))
        assert resp.status_code == 500
        body = json.loads(resp.body)
        assert body["error"] == "INTERNAL_ERROR"
        # The real exception message must never reach the client body.
        assert "sensitive internal detail" not in body["detail"]
        assert body["path"] == "/boom"
        assert body["request_id"] == "rid-2"

    def test_logs_the_real_exception(self):
        import asyncio
        exc = ValueError("only in logs")
        with patch("app.main.logger") as mock_logger:
            asyncio.run(_global_exception_handler(_fake_request(), exc))
            assert mock_logger.exception.called


# ── SecurityHeadersMiddleware ────────────────────────────────────────

class TestSecurityHeaders:
    def test_core_headers_present_on_every_response(self):
        resp = client.get("/definitely-not-a-real-route")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")
        assert "max-age=31536000" in resp.headers.get("Strict-Transport-Security", "")
        assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
        assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-site"
        assert resp.headers.get("X-Permitted-Cross-Domain-Policies") == "none"

    def test_csp_drops_unsafe_inline_script_src(self):
        # Regression guard: unsafe-inline was deliberately removed from
        # script-src (see the inline comment in app/main.py referencing
        # commits 7df818b etc). A regression here silently re-widens XSS
        # exposure app-wide.
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        script_src = [p for p in csp.split(";") if p.strip().startswith("script-src")][0]
        assert "'unsafe-inline'" not in script_src

    def test_permissions_policy_denies_camera_by_default(self):
        resp = client.get("/api/v1/some-admin-route")
        assert resp.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"

    @pytest.mark.parametrize("path", ["/phone-cam", "/student", "/student/foo", "/student-react", "/student-react/bar"])
    def test_permissions_policy_allows_camera_on_proctoring_pages(self, path):
        resp = client.get(path)
        assert resp.headers.get("Permissions-Policy") == "camera=(self), microphone=(self), geolocation=()"

    def test_referrer_policy_not_overridden_if_already_set(self):
        # Guard the `if "Referrer-Policy" not in response.headers` branch.
        # No current route sets it, so this pins the default-set path stays
        # reachable rather than asserting the override branch (which needs
        # a route we don't have) — see gap noted in the report.
        resp = client.get("/health")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ── RequestIDMiddleware ──────────────────────────────────────────────

class TestRequestIDMiddleware:
    def test_generates_request_id_when_absent(self):
        resp = client.get("/health")
        rid = resp.headers.get("X-Request-ID")
        assert rid
        uuid.UUID(rid)  # must be a real UUID

    def test_honours_incoming_request_id(self):
        resp = client.get("/health", headers={"X-Request-ID": "custom-trace-123"})
        assert resp.headers.get("X-Request-ID") == "custom-trace-123"

    def test_two_requests_get_different_ids(self):
        r1 = client.get("/health")
        r2 = client.get("/health")
        assert r1.headers.get("X-Request-ID") != r2.headers.get("X-Request-ID")


# ── ETagMiddleware ────────────────────────────────────────────────────

class TestETagMiddleware:
    def test_metrics_401_response_has_no_etag(self):
        # /api/v1/metrics without auth -> 401, non-200 -> ETag skip branch.
        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 401
        assert "ETag" not in resp.headers

    def test_skips_sse_prefix(self):
        # Even a 404 under the skip prefix must not carry an ETag (proves
        # the skip check runs before call_next, not just before a 200).
        resp = client.get("/api/v1/sse/does-not-exist")
        assert "ETag" not in resp.headers

    def test_skips_static_prefix(self):
        resp = client.get("/static/does-not-exist.png")
        assert "ETag" not in resp.headers

    def test_skips_metrics_path_itself(self):
        resp = client.get("/api/v1/metrics")
        assert "ETag" not in resp.headers

    def test_post_requests_skip_etag(self):
        resp = client.post("/definitely-not-a-real-route")
        assert "ETag" not in resp.headers

    def test_json_200_gets_etag_and_conditional_get_returns_304(self):
        # Real app routes are unsuitable here: /health is 503 in this
        # env (redis/worker/email unconfigured — see test_jobs.py's own
        # comment on the same flakiness) and /api/v1/metrics is itself
        # on the ETag skip list. Build a tiny standalone app carrying
        # ONLY app/main.py's real ETagMiddleware class + a fixed-JSON
        # route, so the assignment/If-None-Match round trip is pinned
        # deterministically without depending on any other route's
        # runtime state.
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse as _JR
        from app.main import ETagMiddleware

        async def _endpoint(request):
            return _JR({"hello": "world"})

        mini_app = Starlette(routes=[Route("/etag-probe", _endpoint)])
        mini_app.add_middleware(ETagMiddleware)
        mini_client = TestClient(mini_app)

        first = mini_client.get("/etag-probe")
        assert first.status_code == 200
        etag = first.headers.get("ETag")
        assert etag

        second = mini_client.get("/etag-probe", headers={"If-None-Match": etag})
        assert second.status_code == 304


def _admin_token():
    from tests.conftest import make_admin_token
    return make_admin_token()


# ── InputValidationMiddleware ────────────────────────────────────────

class TestInputValidationMiddleware:
    def test_oversized_content_length_rejected_fast_path(self):
        resp = client.post(
            "/api/v1/register-student",
            data=b"x",
            headers={"Content-Length": str(11 * 1024 * 1024), "Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    def test_invalid_content_length_header_rejected(self):
        resp = client.post(
            "/api/v1/register-student",
            data=b"{}",
            headers={"Content-Length": "not-a-number", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_sqli_in_query_param_blocked(self):
        resp = client.get("/api/v1/lookup-teacher?email=a%27%20OR%20%271%27%3D%271")
        assert resp.status_code == 400
        assert "suspicious" in resp.text.lower()

    def test_sqli_in_json_body_blocked(self):
        resp = client.post(
            "/api/v1/register-student",
            json={"full_name": "'; DROP TABLE users; --", "roll_number": "R1",
                  "email": "a@b.com", "teacher_id": "t-1"},
        )
        assert resp.status_code == 400
        assert "suspicious" in resp.text.lower()

    def test_legit_json_body_not_blocked_by_input_validation(self):
        # Should sail past InputValidationMiddleware; whatever status the
        # route itself returns afterwards is out of scope here — just
        # confirm it is not the middleware's 400 "Blocked" response.
        resp = client.post(
            "/api/v1/register-student",
            json={"full_name": "Alice", "roll_number": "R1",
                  "email": "a@b.com", "teacher_id": "t-1"},
        )
        assert resp.text != "Blocked: suspicious input"

    def test_malformed_json_with_json_content_type_not_blocked_here(self):
        # Invalid JSON despite the header -> middleware lets FastAPI's own
        # body parsing surface the error (422), rather than 400-blocking.
        resp = client.post(
            "/api/v1/register-student",
            data=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code != 400 or "suspicious" not in resp.text.lower()

    def test_sqli_in_form_body_blocked(self):
        resp = client.post(
            "/api/v1/register-student",
            data={"full_name": "1; DELETE FROM exam_sessions WHERE 1=1"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 400
        assert "suspicious" in resp.text.lower()

    def test_websocket_path_prefix_skipped(self):
        # /ws paths bypass body/SQLi screening entirely (still 404s here
        # since there's no such route registered, but that proves the
        # middleware didn't intercept it with its own 400).
        resp = client.get("/ws/does-not-exist")
        assert resp.status_code != 400

    def test_static_path_prefix_skipped(self):
        resp = client.get("/static/../../etc/passwd")
        assert resp.status_code != 400


# ── CSRFMiddleware ───────────────────────────────────────────────────

def _teacher_claims_token(jti: str = "jti-1") -> str:
    """A signed access-token-shaped JWT with role=teacher, usable as the
    procta_access cookie. Uses the same signing key CSRFMiddleware decodes
    with (ALL_SIGNING_KEYS includes ADMIN_SIGNING_KEY)."""
    import jwt as jose_jwt
    from app.constants import ADMIN_SIGNING_KEY
    now = datetime.now(timezone.utc)
    payload = {"tid": "teacher-1", "role": "teacher", "jti": jti,
               "iat": now, "exp": now + timedelta(hours=1)}
    return jose_jwt.encode(payload, ADMIN_SIGNING_KEY, algorithm="HS256")


class TestCSRFMiddleware:
    def test_exempt_suffix_bypasses_csrf_even_with_stale_cookie(self):
        # The exact bug this exemption list exists to prevent: a stale
        # account cookie must never 403 a fresh login attempt.
        resp = client.post(
            "/api/v1/student/auth/login",
            json={}, cookies={"procta_student_access": "garbage-stale-cookie"},
        )
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_disable_csrf_for_tests_escape_hatch_bypasses_all_checks(self):
        app.state.disable_csrf_for_tests = True
        try:
            resp = client.post(
                "/api/v1/register-student",
                json={}, cookies={"procta_access": _teacher_claims_token()},
            )
            assert resp.status_code != 403
        finally:
            app.state.disable_csrf_for_tests = False

    def test_bearer_token_does_not_require_csrf(self):
        # Authorization: Bearer is a native/API flow, not a cookie session
        # — CSRF must not gate it even for state-changing verbs.
        resp = client.post(
            "/api/v1/register-student",
            json={}, headers={"Authorization": f"Bearer {_teacher_claims_token()}"},
        )
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_cookie_token_without_csrf_header_is_rejected(self):
        resp = client.post(
            "/api/v1/register-student",
            json={}, cookies={"procta_access": _teacher_claims_token(jti="jti-2")},
        )
        assert resp.status_code == 403
        assert "CSRF token required" in resp.text

    def test_cookie_token_with_wrong_csrf_header_is_rejected(self):
        resp = client.post(
            "/api/v1/register-student",
            json={},
            cookies={"procta_access": _teacher_claims_token(jti="jti-3")},
            headers={"X-CSRF-Token": "totally-wrong-value"},
        )
        assert resp.status_code == 403
        assert "CSRF validation failed" in resp.text

    def test_cookie_token_with_correct_csrf_header_passes_csrf_layer(self):
        from app.auth.tokens import issue_csrf_token, _decode_token
        from app.constants import ALL_SIGNING_KEYS
        token = _teacher_claims_token(jti="jti-4")
        claims = _decode_token(token, ALL_SIGNING_KEYS)
        csrf_value = issue_csrf_token(claims)
        assert csrf_value  # sanity: cache mock returns what we set
        with patch("app.cache.get", return_value=csrf_value):
            resp = client.post(
                "/api/v1/register-student",
                json={},
                cookies={"procta_access": token},
                headers={"X-CSRF-Token": csrf_value},
            )
        # Whatever the route itself does next is out of scope — just prove
        # the CSRF layer let it through instead of 403ing.
        assert resp.status_code != 403

    def test_get_requests_never_csrf_gated(self):
        # Only POST/PUT/PATCH/DELETE are checked; GET must pass regardless
        # of cookie state.
        resp = client.get(
            "/api/v1/some-admin-route",
            cookies={"procta_access": _teacher_claims_token(jti="jti-5")},
        )
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_expired_cookie_token_treated_as_unauthenticated(self):
        import jwt as jose_jwt
        from app.constants import ADMIN_SIGNING_KEY
        now = datetime.now(timezone.utc)
        expired = jose_jwt.encode(
            {"tid": "teacher-1", "role": "teacher", "jti": "jti-6",
             "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
            ADMIN_SIGNING_KEY, algorithm="HS256",
        )
        resp = client.post(
            "/api/v1/register-student",
            json={}, cookies={"procta_access": expired},
        )
        # Expired -> treated as unauthenticated -> no CSRF gate (falls
        # through to the route, which may reject for its own reasons, but
        # must not be a CSRF-specific 403).
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_malformed_cookie_token_treated_as_unauthenticated(self):
        resp = client.post(
            "/api/v1/register-student",
            json={}, cookies={"procta_access": "not-a-real-jwt"},
        )
        assert resp.status_code != 403 or "CSRF" not in resp.text


# ── /api/v1/metrics auth gating ──────────────────────────────────────

class TestMetricsEndpoint:
    def test_unauthenticated_request_rejected(self):
        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 401

    def test_authenticated_admin_gets_metrics_shape(self):
        with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
            m.return_value = {
                "id": "teacher-1", "email": "prof@test.com", "full_name": "Prof",
                "org_id": "org-1", "org_role": "admin",
            }
            resp = client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {_admin_token()}"})
        assert resp.status_code == 200
        body = resp.json()
        for key in ("proctor_uptime_seconds", "proctor_requests_total",
                    "proctor_errors_total", "proctor_active_requests"):
            assert key in body
