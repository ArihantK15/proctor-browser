"""Cloudflare Turnstile (CAPTCHA) verification.

Free, invisible by default (Managed mode). Wired on signup, login,
password-reset, and resend-verification.

Sandbox mode: if `TURNSTILE_SECRET_KEY` is unset, `verify()` returns
True so local dev works without a Cloudflare account. Production
deployments MUST set the key — log a loud warning if it's missing
when running outside dev.

Usage:
    from ..services.turnstile import verify_or_403

    @router.post("/api/v1/auth/signup")
    async def signup(body, request):
        await verify_or_403(request, body.captcha_token)
        # ... rest of handler ...
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Cloudflare's verify endpoint. Public — no auth header, just a
# POST with `secret` + `response` (the token from the widget) in the
# form body.
_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Hard 5s ceiling on the siteverify call. If Cloudflare is slow we
# err on the "let the user through" side — auth-flow latency matters
# more than 100% bot rejection. Tracked in logs so we can spot trends.
_VERIFY_TIMEOUT = 5.0


def _is_configured() -> bool:
    return bool(os.environ.get("TURNSTILE_SECRET_KEY"))


async def verify(token: Optional[str], remote_ip: str = "") -> bool:
    """Server-side check that the Turnstile token is genuine.

    Returns:
        True  — token valid, OR Turnstile not configured (dev sandbox)
        False — token missing, malformed, expired, or rejected by CF
    """
    if not _is_configured():
        # Dev sandbox: allow everything. Log once per process so the
        # warning doesn't spam every request.
        if not getattr(verify, "_warned", False):
            logger.warning(
                "[turnstile] sandbox mode (TURNSTILE_SECRET_KEY unset) — "
                "all CAPTCHA checks pass automatically. Set the key "
                "before production deploy."
            )
            verify._warned = True  # type: ignore[attr-defined]
        return True

    if not token:
        return False

    secret = os.environ["TURNSTILE_SECRET_KEY"]
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT) as client:
            resp = await client.post(_VERIFY_URL, data=payload)
    except httpx.RequestError as e:
        logger.error("[turnstile] siteverify unreachable — denying (fail-closed): %s", e)
        return False

    if resp.status_code != 200:
        logger.warning("[turnstile] siteverify HTTP %s — denying", resp.status_code)
        return False

    data = resp.json()
    ok = bool(data.get("success"))
    if not ok:
        # error-codes is a list like ["invalid-input-response", "timeout-or-duplicate"]
        # Useful in logs to spot replay vs widget bugs vs config errors.
        codes = data.get("error-codes", [])
        logger.info("[turnstile] verify failed: codes=%s", codes)
    return ok


async def verify_or_403(request: Request, token: Optional[str]) -> None:
    """Convenience wrapper: raise 403 on failure.

    Use in endpoint bodies for one-line CAPTCHA gating:
        await verify_or_403(request, body.captcha_token)
    """
    ip = request.client.host if request.client else ""
    if not await verify(token, remote_ip=ip):
        raise HTTPException(
            status_code=403,
            detail={
                "error":   "BOT_CHECK_FAILED",
                "message": "We couldn't verify you're human. Refresh and try again.",
            },
        )
