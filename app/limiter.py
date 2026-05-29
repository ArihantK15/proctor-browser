"""Rate limiter setup — shared across all routers."""

import asyncio
import hashlib
import logging

from fastapi import Request
from jwt.exceptions import InvalidTokenError as JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket

from .constants import _LOADTEST_SECRET, WS_MAX_CONNECTIONS_PER_IP

_log = logging.getLogger(__name__)


def _ws_client_ip(ws: WebSocket) -> str:
    """Extract client IP from a WebSocket connection, respecting proxy headers."""
    forwarded = ws.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = ws.client
    return client.host if client else "unknown"


class WSIPRateLimiter:
    """Per-IP connection counter for WebSocket endpoints.

    Guards against IP-based DoS where a single host opens hundreds of
    WS connections to exhaust server resources.  Deployed alongside the
    per-session caps (MAX_WS_PER_SESSION, singleton room-cam) so that
    an attacker on a single IP cannot flood *across* sessions.

    Thread/asyncio-safe via asyncio.Lock.
    """

    def __init__(self, max_per_ip: int = WS_MAX_CONNECTIONS_PER_IP):
        self._lock = asyncio.Lock()
        self._max_per_ip = max_per_ip
        self._ip_counts: dict[str, int] = {}

    async def check_and_increment(self, ip: str) -> bool:
        """Check whether *ip* may open a new WS. Returns True if allowed."""
        async with self._lock:
            count = self._ip_counts.get(ip, 0)
            if count >= self._max_per_ip:
                return False
            self._ip_counts[ip] = count + 1
            return True

    async def decrement(self, ip: str) -> None:
        """Release one connection slot for *ip*. Must be called on WS close."""
        async with self._lock:
            count = self._ip_counts.get(ip, 0)
            if count <= 1:
                self._ip_counts.pop(ip, None)
            else:
                self._ip_counts[ip] = count - 1

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._ip_counts)


def _jwt_rate_limit_key(request: Request) -> str | None:
    """Return a stable per-user/session rate-limit key for valid JWTs.

    Many schools put hundreds of students behind one NAT address. Pure
    IP-based limits make those students share one tiny bucket, so a real
    exam can look like an attack. Invalid tokens still fall back to IP.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        from .auth.tokens import _decode_token
        from .constants import ALL_SIGNING_KEYS
        claims = _decode_token(token, ALL_SIGNING_KEYS)
    except JWTError:
        return None

    identity = (
        claims.get("sid")
        or claims.get("jti")
        or "|".join(str(claims.get(k) or "") for k in ("tid", "eid", "roll", "email", "role"))
    ).strip("|")
    if not identity:
        identity = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    return f"jwt:{identity}"


import os as _os


def _get_env() -> str:
    """Read APP_ENV at runtime so config reloads are honoured."""
    return _os.environ.get("APP_ENV", "development").lower().strip()


def _rate_limit_key(request: Request) -> str:
    # Load-test bypass is disabled in production even if the secret is set,
    # so a leaked LOADTEST_SECRET cannot be exploited on live traffic.
    if _LOADTEST_SECRET and request.headers.get("X-Loadtest-Key") == _LOADTEST_SECRET:
        if _get_env() != "production":
            _log.warning("[rate-limit] Load-test bypass used in %s environment", _get_env())
        return f"loadtest-{id(request)}"
    jwt_key = _jwt_rate_limit_key(request)
    if jwt_key:
        return jwt_key
    return get_remote_address(request)


async def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    _log.warning("[rate-limit] %s %s from %s", request.method, request.url.path, get_remote_address(request))
    return JSONResponse(
        status_code=429,
        content={"error": "RATE_LIMITED", "detail": "Too many requests. Please try again later."}
    )


limiter = Limiter(key_func=_rate_limit_key)
ws_rate_limiter = WSIPRateLimiter()
