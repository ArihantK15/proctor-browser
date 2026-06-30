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

from ..constants import (
    REFRESH_SIGNING_KEY,
    REFRESH_SIGNING_KEYS,
    RESET_SIGNING_KEY,
    RESET_SIGNING_KEYS,
    UNSUBSCRIBE_SIGNING_KEY,
    UNSUBSCRIBE_SIGNING_KEYS,
)

LOCAL_AUTH_PROVIDER = "local"
HYBRID_AUTH_PROVIDER = "hybrid"


def auth_provider_mode() -> str:
    # Default is `local`: Procta has fully migrated off Supabase Auth, so a
    # deployment with no AUTH_PROVIDER set must fail SAFE to plain-Postgres
    # password auth — never to the decommissioned Supabase path. Set
    # AUTH_PROVIDER=supabase/hybrid explicitly only if running the legacy
    # bridge during a transition.
    return os.environ.get("AUTH_PROVIDER", "local").strip().lower()


def local_auth_enabled() -> bool:
    return auth_provider_mode() == LOCAL_AUTH_PROVIDER


def local_password_auth_enabled() -> bool:
    """True when Procta should accept/set local password hashes.

    `hybrid` is the cutover bridge: new password resets/signups use local
    hashes, while legacy users without a hash can still fall back to Supabase
    auth until they complete a reset.
    """
    return auth_provider_mode() in {LOCAL_AUTH_PROVIDER, HYBRID_AUTH_PROVIDER}


def supabase_auth_fallback_enabled() -> bool:
    """Legacy Supabase-Auth fallback — DECOMMISSIONED (gap #38).

    The bcrypt-bypassing Supabase login path could cause auth divergence, so it
    is permanently disabled regardless of AUTH_PROVIDER. Local password auth is
    the only path; the `supabase`/`hybrid` branches downstream are now dead code
    kept only until a follow-up cleanup removes them. Any genuinely legacy
    no-hash user must complete a password reset to log in.
    """
    return False


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


# Precomputed dummy bcrypt hash used by burn_password_verify() to keep
# login response time constant on the "no account exists" path. Generated
# once at module load (rather than per-call) because gensalt() itself is
# slow; the resulting checkpw against a 6-char password takes the same
# wall time as a real password hash check.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"x" * 32, bcrypt.gensalt()).decode("utf-8")


async def burn_password_verify() -> None:
    """Perform a no-op bcrypt verify to equalise login timing.

    Login endpoints used to skip bcrypt entirely when the account didn't
    exist, which leaked account existence via timing (real verify ≈ 100ms,
    no-account ≈ 1ms). Calling this on the no-account branch erases the
    delta. The actual result is discarded; the only thing that matters is
    that the same CPU work happens either way.
    """
    try:
        await asyncio.to_thread(
            lambda: bcrypt.checkpw(b"x" * 32, _DUMMY_PASSWORD_HASH.encode("utf-8"))
        )
    except Exception:
        pass


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
    }, REFRESH_SIGNING_KEY, algorithm="HS256")
    return token, jti, exp


def verify_refresh_token(token: str, expected_kind: str) -> tuple[str, str] | None:
    """Verify the JWT signature + claims.

    Returns (user_id, jti) on success, None on failure. The caller MUST
    then check the jti against `refresh_tokens` — JWT verification alone
    is not enough since stateless verification cannot detect revocation.
    """
    try:
        claims = _decode_with_keys(token, REFRESH_SIGNING_KEYS)
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
    return jwt.encode(payload, RESET_SIGNING_KEY, algorithm="HS256")


def verify_password_reset_token(token: str, expected_kind: str | None = None) -> dict[str, Any] | None:
    try:
        claims = _decode_with_keys(token, RESET_SIGNING_KEYS)
    except JWTError:
        return None
    if claims.get("scope") != "password_reset":
        return None
    if expected_kind and claims.get("kind") != expected_kind:
        return None
    return claims


def issue_unsubscribe_token(student_id: str, email: str) -> str:
    """Mint a long-lived (1 year) unsubscribe JWT.

    The token is self-contained — no DB persistence needed. Scope and
    student identity are embedded so verify_unsubscribe_token can reject
    tokens minted for other purposes or other accounts.
    """
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "scope": "unsubscribe",
        "uid": str(student_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(days=365),
    }, UNSUBSCRIBE_SIGNING_KEY, algorithm="HS256")


def verify_unsubscribe_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an unsubscribe token.

    Returns the claims dict on success (scope="unsubscribe", valid
    signature, not expired) or None on any failure.
    """
    try:
        claims = _decode_with_keys(token, UNSUBSCRIBE_SIGNING_KEYS)
    except JWTError:
        return None
    if claims.get("scope") != "unsubscribe":
        return None
    return claims


def _decode_with_keys(token: str, keys: list[str]) -> dict[str, Any]:
    last_err: Exception | None = None
    for key in keys:
        try:
            return jwt.decode(token, key, algorithms=["HS256"])
        except JWTError as e:
            last_err = e
    raise last_err or JWTError("Token could not be decoded with any key")
