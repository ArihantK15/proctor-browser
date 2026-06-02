"""Thin orchestrator — wires routers, middleware, startup tasks."""
import asyncio
import gc
import hashlib
import json
import time
import uuid
import logging
import threading
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter
import os
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

# ── shared deps (config, auth, helpers, models) ────────────────────
from .limiter import _rate_limit_key, _custom_rate_limit_handler
from .services.sessions import cleanup_screenshots as _cleanup_screenshots
from .reminders import _reminder_loop
from .constants import STATIC_DIR, CORS_ALLOWED_ORIGINS

# ── routers ───────────────────────────────────────────────────────
from .domains.identity import auth_router
from .domains.proctoring import exam_router
from .routers.admin import router as admin_router
from .routers.issues import router as issues_router
from .domains.exams import question_bank_router
from .routers.grading import router as grading_router
from .domains.ops import public_router
from .domains.sessions import sse_router
from .routers.chat import router as chat_router
from .domains.billing import billing_router
# from .routers.checkout import router as checkout_router
from .domains.lti import lti_router
from .routers.api import router as api_router
from .domains.lti import google_classroom_router
from .routers.lti_config import router as lti_config_router
from .domains.ops import admin_status_router
from .domains.compliance import privacy_router
from .domains.compliance import appeals_router

# ── structured logger ─────────────────────────────────────────────
logger = logging.getLogger("proctor.api")

# ── Sentry (optional — only initializes when SENTRY_DSN is set) ──
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            integrations=[StarletteIntegration(), FastApiIntegration()],
        )
        print("[sentry] initialized", flush=True)
    except ImportError:
        print("[sentry] sentry-sdk not installed — install with: pip install sentry-sdk", flush=True)
    except Exception as e:
        print(f"[sentry] init failed: {e}", flush=True)

# ── app bootstrap ─────────────────────────────────────────────────
limiter = Limiter(key_func=_rate_limit_key)


@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:
    """Startup + shutdown lifecycle handler (replaces deprecated on_event)."""
    # ── STARTUP ───────────────────────────────────────────────────
    from .database import async_table as _atable, database_backend
    db_backend = database_backend()
    try:
        await _atable("exam_config").select("id").limit(1).execute()
        print(f"[startup] database connected ({db_backend})", flush=True)
    except Exception as e:
        allow_unhealthy = os.environ.get("SUPABASE_SKIP_STARTUP_CHECK", "") == "1"
        if allow_unhealthy:
            print(f"[startup] WARNING: database unreachable: {e}", flush=True)
        else:
            raise RuntimeError(f"Database unreachable: {e}. Set SUPABASE_SKIP_STARTUP_CHECK=1 to override.") from e

    gc.set_threshold(300, 5, 50)
    gc.freeze()
    _cleanup_stop = threading.Event()
    _cleanup_thread = threading.Thread(target=_cleanup_screenshots, args=(_cleanup_stop,), daemon=True)
    _cleanup_thread.start()

    # ── Per-worker singleton tasks (the leader-worker pattern) ──
    # With uvicorn --workers N>1, the lifespan runs in EVERY worker.
    # Tasks like the reminder loop must only run in ONE worker or they
    # double-fire (students get duplicate emails, etc).
    #
    # Cheapest correct check: `multiprocessing.current_process().name`
    # is "SpawnProcess-1" for the first worker, "SpawnProcess-2" for
    # the second, and so on. We pick worker -1 to own the duties.
    # The room-frame cleanup is idempotent (sweep stale Redis keys),
    # so running it twice is harmless — but we gate it for consistency.
    #
    # If uvicorn ever changes worker naming, both workers will skip
    # the loop (silent failure) — set REMINDER_LEADER_OVERRIDE=1 on
    # the surviving worker to override.
    import multiprocessing
    worker_name = multiprocessing.current_process().name
    is_leader = (
        worker_name.endswith("-1")
        or worker_name == "MainProcess"  # single-worker / dev mode
        or os.environ.get("REMINDER_LEADER_OVERRIDE", "") == "1"
    )

    _reminder_task = None
    _room_frame_cleanup_task = None
    _reaper_task = None
    _ttl_sweeper_task = None

    if os.environ.get("REMINDER_LOOP_DISABLED", "") == "1":
        print(f"[startup] reminders loop disabled by env ({worker_name})", flush=True)
    elif is_leader:
        _reminder_task = asyncio.create_task(_reminder_loop())
        _reminder_task.add_done_callback(
            lambda t: print(f"[startup] reminders loop ended: {t.exception()}", flush=True)
            if not t.cancelled() and t.exception()
            else None
        )
        print(f"[startup] reminders loop started ({worker_name})", flush=True)
    else:
        print(f"[startup] reminders loop skipped — non-leader worker ({worker_name})", flush=True)

    if is_leader:
        _room_frame_cleanup_task = asyncio.create_task(_room_frame_cleanup_loop())
        _room_frame_cleanup_task.add_done_callback(
            lambda t: print(f"[startup] room-frame cleanup loop ended: {t.exception()}", flush=True)
            if not t.cancelled() and t.exception()
            else None
        )

    if is_leader:
        from .services.heartbeat_reaper import heartbeat_reaper_loop
        _reaper_task = asyncio.create_task(heartbeat_reaper_loop())
        _reaper_task.add_done_callback(
            lambda t: print(f"[startup] heartbeat reaper ended: {t.exception()}", flush=True)
            if not t.cancelled() and t.exception()
            else None
        )
        print(f"[startup] heartbeat reaper started ({worker_name})", flush=True)

    if is_leader and os.environ.get("TTL_SWEEPER_DISABLED", "") != "1":
        from .services.ttl_sweeper import ttl_sweeper_loop
        _ttl_sweeper_task = asyncio.create_task(ttl_sweeper_loop())
        _ttl_sweeper_task.add_done_callback(
            lambda t: print(f"[startup] ttl sweeper ended: {t.exception()}", flush=True)
            if not t.cancelled() and t.exception()
            else None
        )
        print(f"[startup] ttl sweeper started ({worker_name})", flush=True)

    yield  # ── APP RUNNING ────────────────────────────────────────

    # ── SHUTDOWN ──────────────────────────────────────────────────
    log = logging.getLogger("shutdown")
    log.info("[shutdown] Starting graceful shutdown...")

    if _room_frame_cleanup_task is not None and not _room_frame_cleanup_task.done():
        _room_frame_cleanup_task.cancel()
        log.info("[shutdown] Cancelled room-frame cleanup task")
    if _reaper_task is not None and not _reaper_task.done():
        _reaper_task.cancel()
        log.info("[shutdown] Cancelled heartbeat reaper task")
    if _ttl_sweeper_task is not None and not _ttl_sweeper_task.done():
        _ttl_sweeper_task.cancel()
        log.info("[shutdown] Cancelled ttl sweeper task")
    if _reminder_task is not None and not _reminder_task.done():
        _reminder_task.cancel()
        log.info("[shutdown] Cancelled reminder task")

    # Await cancelled tasks so they can run finally blocks
    cancelled_tasks = [_room_frame_cleanup_task, _reaper_task, _ttl_sweeper_task, _reminder_task]
    for t in cancelled_tasks:
        if t is not None:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # Belt-and-suspenders: cancel any remaining background tasks
    # (catches renamed/renamed-coroutine edge cases)
    for t in asyncio.all_tasks():
        name = getattr(t, "get_name", lambda: "")()
        if "reminder" in name.lower() or "reaper" in name.lower() or "cleanup" in name.lower() or "sweeper" in name.lower():
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    from .auth.admin_auth import _teacher_cache, _student_acct_cache
    _teacher_cache.clear()
    _student_acct_cache.clear()
    log.info("[shutdown] Cleared in-memory caches")

    try:
        from .cache import _r, _br
        if _r:
            _r.close()
            log.info("[shutdown] Closed Redis connection")
        if _br:
            _br.close()
            log.info("[shutdown] Closed Redis binary connection")
    except Exception:
        log.warning("[shutdown] Failed to close Redis connection")

    # Close the asyncpg pool when DATABASE_BACKEND=postgres. Safe no-op
    # otherwise — the supabase REST path doesn't open one. Without this,
    # every uvicorn reload leaks up to POSTGRES_POOL_MAX connections.
    try:
        from .postgres_table import close_pool as _close_pg_pool
        await _close_pg_pool()
        log.info("[shutdown] Closed Postgres pool")
    except Exception as e:
        log.warning("[shutdown] Failed to close Postgres pool: %s", e)

    # Signal and join the screenshot cleanup daemon thread
    if '_cleanup_stop' in dir() and _cleanup_stop is not None:
        _cleanup_stop.set()
        if '_cleanup_thread' in dir() and _cleanup_thread is not None:
            _cleanup_thread.join(timeout=30)
            log.info("[shutdown] Joined cleanup daemon thread")

    log.info("[shutdown] Graceful shutdown complete")


async def _room_frame_cleanup_loop():
    """Daily cleanup of any stale roomframe:* Redis keys.
    Redis TTL already expires frames after 10s. This loop is
    belt-and-suspenders to guarantee no room camera frame persists
    longer than 24h (FERPA / DPDP Act compliance).
    """
    from . import cache as _cache
    while True:
        try:
            await _cache.acleanup_room_frames()
        except Exception as e:
            logger.warning("[room_frame_cleanup] failed: %s", e)
        await asyncio.sleep(86400)  # 24 hours


app = FastAPI(title="AI Proctor Server", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)

# Note on proxy-header trust (Cloudflare + Caddy fronting Procta):
# Real-client-IP resolution is handled at the uvicorn layer via the
# `--forwarded-allow-ips="*"` flag in entrypoint.sh (uvicorn's built-in
# ProxyHeadersMiddleware). This is safer than enabling it at the app
# layer because uvicorn's middleware runs before slowapi sees the
# request, so the rate-limit key reflects the real client IP.
# Caddy's `client_ip_headers CF-Connecting-IP X-Forwarded-For` block
# (Caddyfile) only trusts these headers from Cloudflare ranges, so a
# spoofed header from a direct connection is ignored.

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    allow_credentials=True,
)
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


import re

# ── Input sanitization ─────────────────────────────────────────────
_XSS_PATTERNS = [
    re.compile(r'<\s*script', re.I),
    re.compile(r'javascript\s*:', re.I),
    re.compile(r'on\w+\s*=', re.I),
    re.compile(r'<\s*iframe', re.I),
    re.compile(r'<\s*object', re.I),
    re.compile(r'<\s*embed', re.I),
    re.compile(r'eval\s*\(', re.I),
    re.compile(r'expression\s*\(', re.I),
]

_SQLI_PATTERNS = [
    re.compile(r"'\s*(OR|AND)\s+'", re.I),
    re.compile(r';\s*(DROP|DELETE|UPDATE|INSERT)', re.I),
    re.compile(r'--\s*$', re.I),
    re.compile(r'/\*.*\*/', re.I),
]

_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB

def _sanitize_value(val: str) -> str:
    """Strip XSS patterns from a string value.

    WARNING: This is a defense-in-depth measure, NOT a replacement
    for proper output encoding in HTML templates. Use only on
    user-supplied strings that will be rendered in the browser.
    """
    for pat in _XSS_PATTERNS:
        val = pat.sub('', val)
    return val

def _looks_malicious(val: str) -> bool:
    """Check if a string contains SQL injection patterns (block, don't sanitize)."""
    for pat in _SQLI_PATTERNS:
        if pat.search(val):
            return True
    return False


def _json_contains_malicious(value) -> bool:
    if isinstance(value, str):
        return _looks_malicious(value)
    if isinstance(value, dict):
        return any(_json_contains_malicious(v) for v in value.values())
    if isinstance(value, list):
        return any(_json_contains_malicious(v) for v in value)
    return False


class InputValidationMiddleware(BaseHTTPMiddleware):
    """Validate and sanitize incoming request inputs.

    - Blocks bodies > 10 MB
    - Rejects requests with obvious SQL injection patterns
    """

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket upgrades, static files, and metrics
        if (request.url.path.startswith('/ws') or
            request.url.path.startswith('/static') or
            request.url.path == '/api/v1/metrics' or
            request.scope.get("type") == "websocket"):
            return await call_next(request)

        # Body size limit — check declared header first (fast path), then
        # enforce against actual streamed bytes so a missing or spoofed
        # Content-Length header cannot bypass the limit.
        cl = request.headers.get('content-length')
        try:
            if cl and int(cl) > _MAX_BODY_BYTES:
                return Response(status_code=413, content='Payload too large')
        except ValueError:
            return Response(status_code=400, content='Invalid Content-Length')
        if request.method in ("POST", "PUT", "PATCH"):
            # Stream-and-count rather than `await request.body()` so an
            # attacker who omits / spoofs Content-Length cannot push
            # arbitrary bytes into RAM before the size check fires.
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_BODY_BYTES:
                    return Response(status_code=413, content='Payload too large')
                chunks.append(chunk)
            body_bytes = b"".join(chunks)
            # Re-inject the already-consumed body so downstream handlers can read it.
            request.state.body_bytes = body_bytes
            request._body = body_bytes

        # Reject SQLi in query parameters
        for key, values in request.query_params.multi_items():
            if _looks_malicious(values):
                return Response(status_code=400, content='Blocked: suspicious input')

        # Reject SQLi in request bodies (defense-in-depth).
        if request.method in ('POST', 'PUT', 'PATCH'):
            ct = request.headers.get('content-type', '')
            if 'application/json' in ct:
                try:
                    body = await request.json()
                    if _json_contains_malicious(body):
                        return Response(status_code=400, content='Blocked: suspicious input')
                except Exception as e:
                    # Body isn't valid JSON despite the content-type header — let
                    # FastAPI's own validation surface the error rather than blocking.
                    logger.debug("InputValidation: JSON parse skipped (%s)", e)
            elif 'application/x-www-form-urlencoded' in ct or 'multipart/form-data' in ct:
                try:
                    form = await request.form()
                    for field_value in form.values():
                        if isinstance(field_value, str) and _looks_malicious(field_value):
                            return Response(status_code=400, content='Blocked: suspicious input')
                except Exception as e:
                    logger.debug("InputValidation: form parse skipped (%s)", e)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if "Referrer-Policy" not in response.headers:
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CSP — hardened after the Caddy short-circuit removal finally
        # surfaced live CSP enforcement on /dashboard:
        #   • 'unsafe-inline' DROPPED from script-src — all inline event
        #     handlers migrated to data-* delegated listeners. References:
        #     7df818b, a1315d2, 5683710, 57fe44d.
        #   • https://fonts.googleapis.com stays on style-src — Google
        #     Fonts serves CSS from googleapis and woff2 files from gstatic.
        #   • media-src 'self' data: allows the dashboard alert chime,
        #     which is an inline base64-encoded WAV (data:audio/wav;base64,…).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'; "
            "script-src 'self' https://challenges.cloudflare.com https://checkout.razorpay.com https://cdn.razorpay.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data: blob: https://*.razorpay.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self' https://challenges.cloudflare.com https://*.razorpay.com; "
            "media-src 'self' data:; "
            "frame-src https://challenges.cloudflare.com https://*.razorpay.com; "
            "frame-ancestors 'none'"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Per-route Permissions-Policy: deny camera + mic by default,
        # but allow `self` on the specific HTML pages that need
        # getUserMedia(). Without this carve-out the global camera=()
        # blocks the browser's permission prompt on /phone-cam,
        # /student, and /student-react, breaking proctoring entirely.
        # The path comparison uses startswith() so /student/foo and
        # /student-react/* are covered without an exhaustive list.
        path = request.url.path
        camera_allowed = (
            path == "/phone-cam"
            or path.startswith("/student")            # /student, /student/*
            or path == "/student-react"
            # /student-react/* assets go through Caddy, not here, but
            # belt-and-suspenders cover them too.
            or path.startswith("/student-react/")
        )
        if camera_allowed:
            response.headers["Permissions-Policy"] = (
                "camera=(self), microphone=(self), geolocation=()"
            )
        else:
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate a unique X-Request-ID per request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        from .logger import set_trace_context
        set_trace_context(request_id, request.method, request.url.path)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class StructuredLogMiddleware(BaseHTTPMiddleware):
    """Log each request's method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = response.status_code
        except HTTPException as exc:
            status = exc.status_code
            raise
        except Exception:
            status = 500
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            msg = f"{request.method} {request.url.path} → {status} ({duration_ms}ms)"
            if status >= 500:
                logger.error(msg)
            elif status >= 400:
                logger.warning(msg)
            else:
                logger.info(msg)
        return response


class ETagMiddleware(BaseHTTPMiddleware):
    """Add ETag headers to JSON responses and honour If-None-Match.

    Skips SSE, WebSocket, static, metrics, and responses >1 MB.
    """

    _SKIP_PREFIXES = ("/api/v1/sse/", "/ws/", "/static/", "/api/v1/metrics")
    _MAX_BODY = 10 * 1024 * 1024  # 10 MB

    @staticmethod
    def _digest(body: bytes) -> str:
        try:
            return hashlib.md5(body, usedforsecurity=False).hexdigest()
        except TypeError:
            # Python <3.9 compatibility for local tooling.
            return hashlib.md5(body).hexdigest()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD"):
            return await call_next(request)
        if any(path.startswith(p) for p in self._SKIP_PREFIXES):
            return await call_next(request)

        response = await call_next(request)

        if response.status_code != 200:
            return response
        ct = response.headers.get("content-type", "")
        if "application/json" not in ct:
            return response
        content_length = response.headers.get("content-length")
        if content_length is None:
            # Avoid buffering streaming/unknown-size JSON responses just to
            # compute a weak ETag. Bounded non-streaming responses still get
            # ETags below.
            return response
        try:
            if int(content_length) > self._MAX_BODY:
                return response
        except ValueError:
            return response

        chunks = []
        total = 0
        async for chunk in response.body_iterator:
            chunks.append(chunk)
            total += len(chunk)
            if total > self._MAX_BODY:
                # Body too large for ETag — stream what we've buffered
                # then drain the remaining iterator.
                async def _drain():
                    for c in chunks:
                        yield c
                    async for c in response.body_iterator:
                        yield c
                return StreamingResponse(
                    _drain(),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=ct,
                )

        body = b"".join(chunks)
        etag = f'"{self._digest(body)[:12]}"'

        inm = request.headers.get("if-none-match", "")
        if etag in inm:
            return Response(status_code=304, headers={"ETag": etag})

        new_headers = {k: v for k, v in response.headers.items()}
        new_headers["ETag"] = etag
        return Response(content=body, status_code=200,
                        headers=new_headers, media_type=ct)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Protect state-changing endpoints from cross-site request forgery.

    Browser account tokens must pair state-changing requests with an
    ``X-CSRF-Token`` value issued by ``/api/v1/auth/csrf``.  The CSRF
    value is stored server-side by access-token JTI; it is not embedded
    in the JWT, so stealing the bearer token alone does not reveal it.

    Exam-runtime bearer tokens are intentionally excluded because those
    calls are native/API flows, not browser cookie-like account sessions.
    """
    async def dispatch(self, request: Request, call_next):
        # Test-only escape hatch. Do not key CSRF bypasses off ambient
        # environment variables; local processes can set those accidentally.
        # Tests that truly need to bypass CSRF must set
        # app.state.disable_csrf_for_tests explicitly.
        if getattr(request.app.state, "disable_csrf_for_tests", False):
            return await call_next(request)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # Auth token can arrive via Authorization: Bearer ... (legacy native
            # clients) OR via HttpOnly cookie (browser sessions). Cookie auth is
            # the exact path CSRF defense exists for — never skip the check just
            # because the Authorization header is absent.
            token = ""
            token_from_cookie = False
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
            else:
                token = (
                    request.cookies.get("procta_access")
                    or request.cookies.get("procta_student_access")
                    or ""
                )
                token_from_cookie = bool(token)
            if token:
                try:
                    import jwt
                    from .constants import ALL_SIGNING_KEYS
                    from .auth.tokens import _decode_token, csrf_required_for_claims
                    claims = _decode_token(token, ALL_SIGNING_KEYS)
                    if token_from_cookie and csrf_required_for_claims(claims):
                        from .auth.tokens import verify_csrf
                        csrf_header = request.headers.get("x-csrf-token", "")
                        if not csrf_header:
                            return Response(status_code=403, content="CSRF token required. Include X-CSRF-Token header.")
                        if not verify_csrf(claims, csrf_header):
                            return Response(status_code=403, content="CSRF validation failed")
                except jwt.ExpiredSignatureError:
                    token = None  # Expired JWT — treat as unauthenticated
                except jwt.InvalidTokenError:
                    token = None  # Malformed JWT — treat as unauthenticated
        return await call_next(request)


app.add_middleware(CSRFMiddleware)
app.add_middleware(StructuredLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(InputValidationMiddleware)
app.add_middleware(ETagMiddleware)

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

@app.exception_handler(HTTPException)
async def _http_exception_handler(request: StarletteRequest, exc: HTTPException):
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        413: "PAYLOAD_TOO_LARGE",
        429: "RATE_LIMITED",
    }
    # L5: surface request_id in error body so users can quote it when
    # filing bug reports — the RequestIDMiddleware also sets it as a
    # response header, but body-level inclusion is easier to copy from
    # a JSON viewer or curl output.
    request_id = getattr(request.state, "request_id", "") if hasattr(request, "state") else ""
    return JSONResponse(status_code=exc.status_code, content={
        "error": code_map.get(exc.status_code, "HTTP_ERROR"),
        "detail": exc.detail,
        "path": request.url.path,
        "request_id": request_id,
    })

@app.exception_handler(Exception)
async def _global_exception_handler(request: StarletteRequest, exc: Exception):
    request_id = getattr(request.state, "request_id", "") if hasattr(request, "state") else ""
    logger.exception("[UNHANDLED] %s %s [%s]: %s", request.method, request.url.path, request_id, exc)
    return JSONResponse(status_code=500, content={
        "error": "INTERNAL_ERROR",
        "detail": "Internal server error",
        "path": request.url.path,
        "request_id": request_id,
    })

# Static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── metrics endpoint ──────────────────────────────────────────────
_METRICS = {
    "request_count": 0,
    "error_count": 0,
    "active_requests": 0,
    "start_time": time.time(),
}

@app.middleware("http")
async def _count_requests(request: Request, call_next):
    _METRICS["request_count"] += 1
    _METRICS["active_requests"] += 1
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            _METRICS["error_count"] += 1
        return response
    except Exception:
        _METRICS["error_count"] += 1
        raise
    finally:
        _METRICS["active_requests"] -= 1

@app.get("/api/v1/metrics")
async def metrics(request: Request):
    """Prometheus-style metrics for monitoring. Requires admin auth to prevent information leakage."""
    from .auth.admin_auth import require_admin
    await require_admin(request)
    uptime = round(time.time() - _METRICS["start_time"], 1)
    return {
        "proctor_uptime_seconds": uptime,
        "proctor_requests_total": _METRICS["request_count"],
        "proctor_errors_total": _METRICS["error_count"],
        "proctor_active_requests": _METRICS["active_requests"],
    }

# ── include routers ───────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(exam_router)
app.include_router(admin_router)
app.include_router(issues_router)
app.include_router(question_bank_router)
app.include_router(grading_router)
app.include_router(public_router)
app.include_router(sse_router)
app.include_router(chat_router)
app.include_router(billing_router)
# app.include_router(checkout_router)  # see import block — disabled until use case ships
app.include_router(lti_router)
app.include_router(api_router)
app.include_router(google_classroom_router)
app.include_router(lti_config_router)
app.include_router(privacy_router)
app.include_router(appeals_router)
app.include_router(admin_status_router)
