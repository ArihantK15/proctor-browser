"""Thin orchestrator — wires routers, middleware, startup tasks.

All business logic, auth helpers, and utility functions live in
``dependencies.py``.  Domain endpoints are split across the
``routers/`` package.
"""
import gc
import hashlib
import json
import time
import uuid
import logging
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ── shared deps (config, auth, helpers, models) ────────────────────
from .dependencies import (
    _rate_limit_key,
    _cleanup_screenshots,
    _reminder_loop,
    SCREENSHOTS_DIR,
    QUESTION_IMG_DIR,
    STATIC_DIR,
    CORS_ALLOWED_ORIGINS,
    os,
)

# ── routers ───────────────────────────────────────────────────────
from .routers.auth import router as auth_router
from .routers.exam import router as exam_router
from .routers.admin import router as admin_router
from .routers.question_bank import router as question_bank_router
from .routers.grading import router as grading_router
from .routers.public import router as public_router
from .routers.sse import router as sse_router
from .routers.chat import router as chat_router

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

app = FastAPI(title="AI Proctor Server")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


import re
from starlette.datastructures import UploadFile

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


class InputValidationMiddleware(BaseHTTPMiddleware):
    """Validate and sanitize incoming request inputs.

    - Blocks bodies > 10 MB
    - Rejects requests with obvious SQL injection patterns
    """

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket, static files, and metrics
        if request.url.path.startswith('/static') or request.url.path == '/api/v1/metrics':
            return await call_next(request)

        # Body size limit
        cl = request.headers.get('content-length')
        if cl and int(cl) > _MAX_BODY_BYTES:
            return Response(status_code=413, content='Payload too large')

        # Reject SQLi in query parameters
        for key, values in request.query_params.multi_items():
            if _looks_malicious(values):
                return Response(status_code=400, content='Blocked: suspicious input')

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'none'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate a unique X-Request-ID per request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class StructuredLogMiddleware(BaseHTTPMiddleware):
    """Log each request as structured JSON with method, path, status, duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            request_id = getattr(request.state, "request_id", "unknown")
            log_entry = {
                "ts": time.time(),
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else "-",
            }
            if status >= 500:
                logger.error(json.dumps(log_entry))
            elif status >= 400:
                logger.warning(json.dumps(log_entry))
            else:
                logger.info(json.dumps(log_entry))
        return response


class ETagMiddleware(BaseHTTPMiddleware):
    """Add ETag headers to JSON responses and honour If-None-Match.

    Skips SSE, WebSocket, static, metrics, and responses >1 MB.
    """

    _SKIP_PREFIXES = ("/api/v1/sse/", "/ws/", "/static/", "/api/v1/metrics")
    _MAX_BODY = 1024 * 1024  # 1 MB

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self._SKIP_PREFIXES):
            return await call_next(request)

        response = await call_next(request)

        if response.status_code != 200:
            return response
        ct = response.headers.get("content-type", "")
        if "application/json" not in ct:
            return response

        chunks = []
        total = 0
        async for chunk in response.body_iterator:
            chunks.append(chunk)
            total += len(chunk)
            if total > self._MAX_BODY:
                return Response(content=b"".join(chunks), status_code=200,
                                headers=dict(response.headers), media_type=ct)

        body = b"".join(chunks)
        etag = f'"{hashlib.md5(body).hexdigest()[:12]}"'

        inm = request.headers.get("if-none-match", "")
        if etag in inm:
            return Response(status_code=304, headers={"ETag": etag})

        new_headers = {k: v for k, v in response.headers.items()}
        new_headers["ETag"] = etag
        return Response(content=body, status_code=200,
                        headers=new_headers, media_type=ct)


app.add_middleware(StructuredLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(InputValidationMiddleware)
app.add_middleware(ETagMiddleware)

# Global exception handler
import traceback as _tb
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

@app.exception_handler(HTTPException)
async def _http_exception_handler(request: StarletteRequest, exc: HTTPException):
    code_map = {
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        413: "PAYLOAD_TOO_LARGE",
        429: "RATE_LIMITED",
    }
    return JSONResponse(status_code=exc.status_code, content={
        "error": code_map.get(exc.status_code, "HTTP_ERROR"),
        "detail": exc.detail,
        "path": request.url.path,
    })

@app.exception_handler(Exception)
async def _global_exception_handler(request: StarletteRequest, exc: Exception):
    print(f"[UNHANDLED] {request.method} {request.url.path}: {exc}")
    _tb.print_exc()
    return JSONResponse(status_code=500, content={
        "error": "INTERNAL_ERROR",
        "detail": "Internal server error",
        "path": request.url.path,
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
async def metrics():
    """Prometheus-style metrics for monitoring."""
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
app.include_router(question_bank_router)
app.include_router(grading_router)
app.include_router(public_router)
app.include_router(sse_router)
app.include_router(chat_router)

# ── startup tasks ─────────────────────────────────────────────────
@app.on_event("startup")
async def _on_startup():
    # Supabase connectivity check — fail fast instead of serving 502s
    from .dependencies import supabase
    try:
        supabase.table("exam_config").select("id").limit(1).execute()
        print("[startup] Supabase connected", flush=True)
    except Exception as e:
        allow_unhealthy = os.environ.get("SUPABASE_SKIP_STARTUP_CHECK", "") == "1"
        if allow_unhealthy:
            print(f"[startup] WARNING: Supabase unreachable: {e}", flush=True)
        else:
            raise RuntimeError(f"Supabase unreachable: {e}. Set SUPABASE_SKIP_STARTUP_CHECK=1 to override.") from e

    # Memory tuning for 2GB RAM droplets:
    # Reduce GC thresholds to collect short-lived objects more aggressively
    # and limit gen-2 pauses. Default is (700, 10, 10); on a server that
    # runs indefinitely this causes periodic memory spikes. We tighten gen-0
    # so request/response cycles get collected faster, and raise the gen-1
    # threshold so mid-lived objects survive one extra collection pass.
    gc.set_threshold(300, 5, 50)
    gc.freeze()  # Ignore objects alive at startup (stdlib + imports)

    # Screenshot cleanup background thread
    threading.Thread(target=_cleanup_screenshots, daemon=True).start()

    # Reminder loop (can be disabled via env var)
    if os.environ.get("REMINDER_LOOP_DISABLED", "") == "1":
        print("[reminders] loop disabled via REMINDER_LOOP_DISABLED=1", flush=True)
    else:
        import asyncio
        asyncio.create_task(_reminder_loop())
        print("[reminders] loop started", flush=True)
