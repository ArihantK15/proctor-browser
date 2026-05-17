"""Rate limiter setup — shared across all routers."""

import hashlib
import logging

from fastapi import Request
import jwt
from jwt.exceptions import InvalidTokenError as JWTError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from .constants import SECRET_KEY, _LOADTEST_SECRET

_log = logging.getLogger(__name__)


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
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
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
    if _LOADTEST_SECRET and _get_env() != "production" and request.headers.get("X-Loadtest-Key") == _LOADTEST_SECRET:
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
