"""Tests for app.services.local_auth — bcrypt + stateless JWTs.

Pure unit tests: no DB, no network. The conftest seeds the env vars
needed for `app.constants` to load (SECRET_KEY etc.).
"""
import asyncio
import time

import jwt
import pytest

from app.constants import SECRET_KEY
from app.services.local_auth import (
    LOCAL_AUTH_PROVIDER,
    hash_password,
    issue_password_reset_token,
    issue_refresh_token,
    local_auth_enabled,
    new_auth_uid,
    verify_password,
    verify_password_reset_token,
    verify_refresh_token,
)


# ─── flag + uid helpers ─────────────────────────────────────────────

def test_local_auth_enabled_defaults_to_false(monkeypatch):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    assert local_auth_enabled() is False


def test_local_auth_enabled_when_explicit(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "local")
    assert local_auth_enabled() is True


def test_local_auth_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "LOCAL")
    assert local_auth_enabled() is True
    monkeypatch.setenv("AUTH_PROVIDER", " Local ")  # tolerates whitespace
    assert local_auth_enabled() is True


def test_new_auth_uid_is_valid_uuid():
    import uuid as _uuid
    uid = new_auth_uid()
    # Round-trips through uuid.UUID parser without error.
    assert _uuid.UUID(uid)
    assert len(uid) == 36


# ─── password hashing ───────────────────────────────────────────────

def test_password_hash_roundtrip():
    h = asyncio.run(hash_password("CorrectHorseBattery!"))
    assert h.startswith("$2")  # bcrypt prefix
    assert asyncio.run(verify_password("CorrectHorseBattery!", h)) is True
    assert asyncio.run(verify_password("wrong", h)) is False


def test_password_verify_rejects_missing_hash():
    """A user with no password (e.g. legacy supabase-only) cannot log in
    via the local path. verify_password must short-circuit to False."""
    assert asyncio.run(verify_password("anything", None)) is False
    assert asyncio.run(verify_password("anything", "")) is False


def test_password_verify_swallows_malformed_hash():
    """Defensive: a corrupted hash in the DB shouldn't crash login —
    just deny access."""
    assert asyncio.run(verify_password("anything", "not-a-bcrypt-hash")) is False


# ─── refresh tokens ─────────────────────────────────────────────────

def test_refresh_token_round_trip():
    token, jti, exp = issue_refresh_token("user-1", "teacher")
    # Every issued token has a UUID jti for server-side revocation.
    assert len(jti) == 36
    # Verify returns (uid, jti) so the caller can look up the DB row.
    verified = verify_refresh_token(token, "teacher")
    assert verified == ("user-1", jti)


def test_refresh_token_wrong_kind_rejected():
    """A teacher refresh token must not validate as a student token —
    prevents accidental cross-role privilege escalation if the same
    SECRET_KEY signs both."""
    token, _, _ = issue_refresh_token("user-1", "teacher")
    assert verify_refresh_token(token, "student") is None


def test_refresh_token_without_jti_rejected():
    """Pre-revocation tokens (no jti claim) must be rejected strictly.

    The pre-rotation code path was deployed under AUTH_PROVIDER=supabase
    (default), so no jti-less refresh tokens exist in the wild. This
    test guards against a future regression where someone removes the
    jti check and reopens the stateless-JWT leak window.
    """
    forged = jwt.encode(
        {"scope": "refresh", "uid": "user-1", "kind": "teacher", "iat": 1, "exp": 9999999999},
        SECRET_KEY,
        algorithm="HS256",
    )
    assert verify_refresh_token(forged, "teacher") is None


def test_refresh_token_wrong_scope_rejected():
    """Reusing an access token as a refresh token must fail."""
    fake = jwt.encode(
        {"scope": "access", "uid": "user-1", "kind": "teacher", "jti": "x", "iat": 1, "exp": 9999999999},
        SECRET_KEY,
        algorithm="HS256",
    )
    assert verify_refresh_token(fake, "teacher") is None


def test_refresh_token_garbage_rejected():
    assert verify_refresh_token("not.a.jwt", "teacher") is None
    assert verify_refresh_token("", "teacher") is None


def test_refresh_token_expired_rejected():
    """JWT lib enforces exp; expired tokens fail signature check."""
    expired = jwt.encode(
        {"scope": "refresh", "uid": "user-1", "kind": "teacher", "jti": "x", "iat": 1, "exp": 2},
        SECRET_KEY,
        algorithm="HS256",
    )
    assert verify_refresh_token(expired, "teacher") is None


# ─── password reset tokens ──────────────────────────────────────────

def test_reset_token_without_pwc_round_trip():
    """Legacy tokens (no pwc) still verify — important for tokens
    minted before single-use binding rolled out."""
    tok = issue_password_reset_token("user-1", "a@b.com", "teacher")
    claims = verify_password_reset_token(tok)
    assert claims is not None
    assert claims["uid"] == "user-1"
    assert claims["kind"] == "teacher"
    assert "pwc" not in claims


def test_reset_token_with_pwc_embeds_timestamp():
    """Single-use binding: the user's current password_changed_at
    travels with the token so confirm() can reject a second use."""
    tok = issue_password_reset_token(
        "user-1", "a@b.com", "teacher",
        password_changed_at="2026-05-15T10:00:00+00:00",
    )
    claims = verify_password_reset_token(tok)
    assert claims["pwc"] == "2026-05-15T10:00:00+00:00"


def test_reset_token_wrong_kind_rejected():
    tok = issue_password_reset_token("user-1", "a@b.com", "teacher")
    assert verify_password_reset_token(tok, expected_kind="student") is None


def test_reset_token_correct_kind_accepted():
    tok = issue_password_reset_token("user-1", "a@b.com", "teacher")
    claims = verify_password_reset_token(tok, expected_kind="teacher")
    assert claims is not None
    assert claims["uid"] == "user-1"


def test_reset_token_wrong_scope_rejected():
    """A refresh token must not unlock the password-reset confirm
    endpoint — different scopes, different secret-protected surface."""
    refresh, _, _ = issue_refresh_token("user-1", "teacher")
    assert verify_password_reset_token(refresh) is None
