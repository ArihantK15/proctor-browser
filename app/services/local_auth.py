"""Local password auth for the plain-Postgres migration path."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError as JWTError

from ..constants import SECRET_KEY

LOCAL_AUTH_PROVIDER = "local"


def local_auth_enabled() -> bool:
    return os.environ.get("AUTH_PROVIDER", "supabase").strip().lower() == LOCAL_AUTH_PROVIDER


def new_auth_uid() -> str:
    """Generate a UUID for compatibility with existing supabase_uid columns."""
    return str(uuid.uuid4())


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(
        lambda: bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    )


async def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return await asyncio.to_thread(
            lambda: bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        )
    except Exception:
        return False


def issue_refresh_token(user_id: str, kind: str) -> tuple[str, str, datetime]:
    """Mint a 30-day refresh JWT with a UUID jti embedded as a claim.

    Returns (token, jti, expires_at). The caller MUST persist the
    jti + expires_at into the `refresh_tokens` table so the token can
    be revoked server-side. A token whose jti is missing from the table
    or whose row has `revoked_at IS NOT NULL` is rejected at refresh
    time — closes the stateless-JWT leak window.
    """
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=30)
    token = jwt.encode({
        "scope": "refresh",
        "uid": str(user_id),
        "kind": kind,
        "jti": jti,
        "iat": now,
        "exp": exp,
    }, SECRET_KEY, algorithm="HS256")
    return token, jti, exp


def verify_refresh_token(token: str, expected_kind: str) -> tuple[str, str] | None:
    """Verify the JWT signature + claims.

    Returns (user_id, jti) on success, None on failure. The caller MUST
    then check the jti against `refresh_tokens` — JWT verification alone
    is not enough since stateless verification cannot detect revocation.
    """
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("scope") != "refresh" or claims.get("kind") != expected_kind:
        return None
    uid = claims.get("uid")
    jti = claims.get("jti")
    if not uid or not jti:
        # Pre-revocation tokens (no jti) are rejected outright.
        # There are none in the wild yet — local auth has not been
        # flipped on in production — so this is safe to enforce strictly.
        return None
    return str(uid), str(jti)


def issue_password_reset_token(
    user_id: str,
    email: str,
    kind: str,
    password_changed_at: str | None = None,
) -> str:
    """Mint a 30-minute password-reset JWT.

    `password_changed_at` is the user's CURRENT `password_changed_at` from
    the DB (ISO string) — embedded as `pwc` so that AFTER the user uses
    the token to set a new password, the DB column advances and the
    token's embedded value no longer matches. The verify step rejects
    the second use → tokens are effectively single-use, defeating
    replay/intercept attacks.

    Pre-token-binding (legacy) tokens omitted `pwc`; verify treats those
    as legacy-compatible (no single-use enforcement) so any tokens in
    flight at deploy time keep working until they naturally expire.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "scope": "password_reset",
        "uid": str(user_id),
        "email": email,
        "kind": kind,
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }
    if password_changed_at:
        payload["pwc"] = password_changed_at
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_password_reset_token(token: str, expected_kind: str | None = None) -> dict | None:
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("scope") != "password_reset":
        return None
    if expected_kind and claims.get("kind") != expected_kind:
        return None
    return claims
