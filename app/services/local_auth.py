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


def issue_refresh_token(user_id: str, kind: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "scope": "refresh",
        "uid": str(user_id),
        "kind": kind,
        "iat": now,
        "exp": now + timedelta(days=30),
    }, SECRET_KEY, algorithm="HS256")


def verify_refresh_token(token: str, expected_kind: str) -> str | None:
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("scope") != "refresh" or claims.get("kind") != expected_kind:
        return None
    uid = claims.get("uid")
    return str(uid) if uid else None


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
