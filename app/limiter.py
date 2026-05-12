"""Rate limiter setup — shared across all routers."""

import logging

from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from .constants import _LOADTEST_SECRET

_log = logging.getLogger(__name__)


def _rate_limit_key(request: Request) -> str:
    if _LOADTEST_SECRET and request.headers.get("X-Loadtest-Key") == _LOADTEST_SECRET:
        return f"loadtest-{id(request)}"
    return get_remote_address(request)


async def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    _log.warning("[rate-limit] %s %s from %s", request.method, request.url.path, get_remote_address(request))
    return JSONResponse(
        status_code=429,
        content={"error": "RATE_LIMITED", "detail": "Too many requests. Please try again later."}
    )


limiter = Limiter(key_func=_rate_limit_key)
