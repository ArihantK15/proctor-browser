"""JWT issue and verify helpers."""
from __future__ import annotations
import hashlib
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

from fastapi import Request, HTTPException
import jwt
from jwt.exceptions import InvalidTokenError as JWTError

from ..constants import (
    ADMIN_SIGNING_KEY,
    ADMIN_SIGNING_KEYS,
    STUDENT_SIGNING_KEY,
    STUDENT_SIGNING_KEYS,
    EXAM_TOKEN_SIGNING_KEY,
    EXAM_TOKEN_SIGNING_KEYS,
    EMAIL_VERIFY_SIGNING_KEYS,
    EMAIL_VERIFY_SIGNING_KEY,
    REAUTH_SIGNING_KEYS,
    REAUTH_SIGNING_KEY,
    ALL_SIGNING_KEYS,
    TOKEN_TTL_HOURS,
    ADMIN_TOKEN_TTL_MINUTES,
    STUDENT_AUTH_TTL_MINUTES,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthCtx:
    """Canonical typed container for exam-student auth token claims.

    Replaces the ad-hoc ``claims.get("tid")`` / ``claims.get("eid")`` /
    ``claims.get("roll")`` pattern.  Use :func:`extract_auth` to build
    one from a FastAPI request (requires ``Authorization: Bearer ...``).
    """
    teacher_id: str
    roll_number: str
    exam_id: str | None = None
    email: str | None = None
    org_id: str | None = None
    org_role: str | None = None
    sid: str | None = None  # student_account id (for student-auth tokens)

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> AuthCtx:
        return cls(
            teacher_id=str(claims.get("tid") or ""),
            roll_number=str(claims.get("roll") or ""),
            exam_id=claims.get("eid"),
            email=claims.get("email"),
            org_id=claims.get("org_id"),
            org_role=claims.get("org_role"),
            sid=claims.get("sid"),
        )


def extract_auth(request: Request) -> AuthCtx:
    """Shortcut: decode the Bearer token and return a typed :class:`AuthCtx`."""
    claims = require_auth(request)
    return AuthCtx.from_claims(claims)


# ─── CSRF protection ─────────────────────────────────────────────

_CSRF_TTL_SECONDS = int(os.environ.get("CSRF_TOKEN_TTL_SECONDS", str(max(
    ADMIN_TOKEN_TTL_MINUTES,
    STUDENT_AUTH_TTL_MINUTES,
) * 60)))
def _gen_csrf() -> str:
    """Generate an independent CSRF secret."""
    return secrets.token_urlsafe(32)


def _csrf_stateless_token(claims: dict[str, Any], token: str | None = None) -> str:
    """Create a signed CSRF fallback tied to the current access-token JTI."""
    now = datetime.now(timezone.utc)
    payload = {
        "typ": "csrf",
        "role": claims.get("role"),
        "jti": str(claims.get("jti") or ""),
        "nonce": token or _gen_csrf(),
        "iat": now,
        "exp": now + timedelta(seconds=_CSRF_TTL_SECONDS),
    }
    return jwt.encode(payload, REAUTH_SIGNING_KEY, algorithm="HS256")


def _verify_stateless_csrf(claims: dict[str, Any], header_value: str) -> bool:
    """Validate a signed CSRF fallback token against the access-token JTI."""
    try:
        payload = jwt.decode(header_value, REAUTH_SIGNING_KEY, algorithms=["HS256"])
    except JWTError:
        return False
    if payload.get("typ") != "csrf":
        return False
    if payload.get("role") != claims.get("role"):
        return False
    return secrets.compare_digest(
        str(payload.get("jti") or ""),
        str(claims.get("jti") or ""),
    )


def _csrf_subject(claims: dict[str, Any]) -> str:
    """Stable server-side CSRF storage subject for browser auth tokens."""
    role = claims.get("role")
    jti = str(claims.get("jti") or "")
    if role in {"teacher", "student_account"} and jti:
        return f"{role}:{jti}"
    return ""


def _csrf_key(claims: dict[str, Any]) -> str:
    subject = _csrf_subject(claims)
    if not subject:
        return ""
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return f"csrf:{digest}"


def csrf_required_for_claims(claims: dict[str, Any]) -> bool:
    """CSRF applies to browser account JWTs, not exam-runtime bearer tokens."""
    return claims.get("role") in {"teacher", "student_account"}


def issue_csrf_token(claims: dict[str, Any]) -> str:
    """Issue a server-stored CSRF secret for the current access-token JTI.

    The value is deliberately not embedded in the JWT.  A stolen access token
    alone is therefore insufficient to satisfy browser mutation CSRF checks.
    """
    key = _csrf_key(claims)
    if not key:
        return ""
    token = _gen_csrf()
    try:
        from .. import cache as _cache
        _cache.set(key, token, ttl=_CSRF_TTL_SECONDS)
        if _cache.get(key) != token:
            raise RuntimeError("CSRF token was not persisted")
    except Exception:
        logger.warning("tokens: csrf cache unavailable; issuing signed stateless fallback", exc_info=True)
        return _csrf_stateless_token(claims, token)
    return token


def clear_csrf_token(claims: dict[str, Any]) -> None:
    key = _csrf_key(claims)
    if not key:
        return
    try:
        from .. import cache as _cache
        _cache.delete(key)
    except Exception:
        logger.debug("tokens: csrf cache delete failed", exc_info=True)


def verify_csrf(claims: dict[str, Any], header_value: str) -> bool:
    """Check whether ``header_value`` matches the server-stored CSRF secret.

    Returns False when the header is absent or doesn't match.
    """
    if not header_value:
        return False
    key = _csrf_key(claims)
    if not key:
        return False
    expected = None
    try:
        from .. import cache as _cache
        cached = _cache.get(key)
        if isinstance(cached, str):
            expected = cached
    except Exception:
        pass
    if expected:
        return secrets.compare_digest(header_value, expected)
    return _verify_stateless_csrf(claims, header_value)


def create_token(roll_number: str, teacher_id: Optional[str] = None, exam_id: Optional[str] = None,
                 student_id: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll_number, "jti": str(uuid.uuid4()),
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS), "iat": now,
    }
    if teacher_id:
        payload["tid"] = teacher_id
    if exam_id:
        payload["eid"] = exam_id
    if student_id:
        payload["sid"] = student_id
    return jwt.encode(payload, EXAM_TOKEN_SIGNING_KEY, algorithm="HS256")


def _decode_token(token: str, keys: list[str]) -> dict[str, Any]:
    """Try decoding a JWT with multiple signing keys (ordered by likelihood)."""
    last_err = None
    for key in keys:
        try:
            return jwt.decode(token, key, algorithms=["HS256"])
        except JWTError as e:
            last_err = e
    raise last_err or JWTError("Token could not be decoded with any key")


def require_auth(request: Request, allowed_roles: list[str] | None = None) -> dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token:
        token = request.cookies.get("procta_access") or request.cookies.get("procta_student_access") or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        claims = _decode_token(token, ALL_SIGNING_KEYS)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Reject single-purpose tokens that happen to be signed by a key in
    # ALL_SIGNING_KEYS. The room-cam JWT (scope="room-cam") is signed
    # with a key in ALL_SIGNING_KEYS because admin_media accepts it for
    # image fetches, but it must NOT authenticate against session-bearing
    # endpoints — a stolen QR-code token would otherwise have a 2-hour
    # window to POST events / save-answer for the bound session. Other
    # scoped tokens (csrf, reauth, email_verify, refresh) are already
    # filtered by their keys being absent from ALL_SIGNING_KEYS; this
    # guard adds explicit defence in case the keyring grows later.
    _scope = claims.get("scope")
    if _scope and _scope not in (None, "", "exam"):
        raise HTTPException(status_code=403, detail="Token scope is not valid here")
    if allowed_roles and claims.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions for this endpoint")
    # RLS session context (phase124) — inert unless RLS_SESSION_CONTEXT is on.
    # Exam/teacher JWTs carry role + tid (teacher) + sid (student account).
    from ..db_context import set_context as _set_db_context
    _set_db_context(role=claims.get("role"), teacher_id=claims.get("tid"),
                    account_id=claims.get("sid"))
    return claims


def require_teacher_auth(request: Request) -> dict[str, Any]:
    """Like require_auth but restricted to teacher tokens only.
    Use on exam management endpoints that must not accept student tokens."""
    return require_auth(request, allowed_roles=["teacher"])


def verify_student_token(token: str) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = _decode_token(token, EXAM_TOKEN_SIGNING_KEYS)
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "student_account":
        raise HTTPException(status_code=403, detail="Student access required")
    return payload


def issue_admin_token(teacher: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    payload = {
        "tid": str(teacher["id"]), "email": teacher.get("email", ""),
        "role": "teacher", "jti": jti,
        "iat": now, "exp": now + timedelta(minutes=ADMIN_TOKEN_TTL_MINUTES),
    }
    org_id = teacher.get("org_id")
    if org_id:
        payload["org_id"] = str(org_id)
    org_role = teacher.get("org_role", "teacher")
    payload["org_role"] = org_role
    return jwt.encode(payload, ADMIN_SIGNING_KEY, algorithm="HS256")


def issue_student_auth_token(account: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sid": str(account["id"]), "email": account.get("email", ""),
        "role": "student_account", "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=STUDENT_AUTH_TTL_MINUTES),
    }
    return jwt.encode(payload, STUDENT_SIGNING_KEY, algorithm="HS256")


# ─── Special-purpose token generators ───────────────────────────

def issue_email_verify_token(user_id: str, email: str, kind: str = "teacher") -> str:
    # user_id may arrive as a uuid.UUID (Postgres backend returns native UUIDs);
    # json/jwt can't serialize it, so coerce to str (was a hard 500 on signup).
    return jwt.encode({
        "scope": "email_verify", "uid": str(user_id), "email": email, "kind": kind,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }, EMAIL_VERIFY_SIGNING_KEY, algorithm="HS256")


def issue_reauth_token(user_id: str) -> str:
    return jwt.encode({
        "scope": "reauth", "uid": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }, REAUTH_SIGNING_KEY, algorithm="HS256")


def verify_reauth_token(token: str, user_id: str) -> bool:
    try:
        claims = _decode_token(token, REAUTH_SIGNING_KEYS)
        return claims.get("scope") == "reauth" and str(claims.get("uid")) == str(user_id)
    except Exception:
        return False


def verify_email_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an email_verify token. Returns claims dict or None."""
    try:
        claims = _decode_token(token, EMAIL_VERIFY_SIGNING_KEYS)
        if claims.get("scope") != "email_verify":
            return None
        return claims
    except Exception:
        return None


def _check_session_ownership(claims: dict[str, Any], session_id: str) -> None:
    parts = session_id.rsplit("_", 1)
    session_roll = parts[0].upper() if parts else ""
    # `(claims.get("roll") or "")` not `.get("roll", "")`: dict.get only
    # falls back to the default when the KEY is absent, so a claim that
    # explicitly carries roll=None (some token shapes) used to raise
    # AttributeError on None.upper() and 500 the request instead of
    # returning a clean 403.
    if (claims.get("roll") or "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    if len(parts) > 1 and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', parts[1], re.I):
        session_tid = parts[1]
        claims_tid = str(claims.get("tid") or "")
        if session_tid and claims_tid and session_tid != claims_tid:
            raise HTTPException(status_code=403, detail="Access denied")
