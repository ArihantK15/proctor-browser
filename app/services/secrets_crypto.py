"""Application-level envelope encryption (AES-256-GCM) for secret exam data.

What this protects: coding `coding_test_cases.expected_output` and MCQ
`questions.correct`. The symmetric key lives ONLY in the app process
environment (env var CODING_SECRETS_KEY, base64-encoded 32 random bytes) and
is never stored in the database — so a stolen `pg_dump` or a compromised DB
role sees ciphertext, not answer keys.

Token format: "enc:v1:<base64(nonce || ciphertext_and_tag)>"
  - nonce: 12 random bytes (os.urandom), fresh per encrypt() call.
  - ciphertext_and_tag: AESGCM output (ciphertext with the 16-byte GCM tag
    appended — that's how the `cryptography` AESGCM API returns it).

Backward compatibility (critical): rows written before this feature existed
are plain strings with no "enc:v1:" prefix. decrypt() passes those through
unchanged — no key needed, no error. This lets the app run correctly against
a DB with a mix of legacy-plaintext and newly-encrypted rows for as long as
the transition period lasts (see migrations/phase145_*.sql for the optional
backfill that converts legacy rows to ciphertext at rest).

Dev/CI posture: if CODING_SECRETS_KEY is unset, encrypt() is a no-op (returns
the plaintext unchanged) so the app and test suite run without provisioning a
key. A one-time warning is logged (no payload, no key material) so the gap
is visible in production logs without spamming them per-call.
"""

import base64
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_ENV_VAR = "CODING_SECRETS_KEY"
_PREFIX = "enc:v1:"
_NONCE_LEN = 12  # bytes; AES-GCM standard nonce size

# Module-level cache so the env var is parsed once per process. Tests reset
# this via reset_key_cache() so monkeypatch.setenv() takes effect immediately
# instead of reusing a key parsed from a previous test's env.
_cached_key: bytes | None = None
_key_cache_populated = False
_warned_no_key = False


class SecretsCryptoError(Exception):
    """Raised when a secret value cannot be decrypted (no key configured, or
    the underlying AES-GCM decryption failed — wrong key or tampered data)."""


def reset_key_cache() -> None:
    """Clear the cached key (and the one-time-warning flag) so the next
    encrypt()/decrypt() call re-reads CODING_SECRETS_KEY from the environment.
    Intended for tests (monkeypatch.setenv) — production code never needs to
    call this since the env var doesn't change mid-process."""
    global _cached_key, _key_cache_populated, _warned_no_key
    _cached_key = None
    _key_cache_populated = False
    _warned_no_key = False


def _get_key() -> bytes | None:
    """Read+cache the 32-byte key from CODING_SECRETS_KEY. Returns None if the
    env var is unset/empty (caller decides how to handle "no key")."""
    global _cached_key, _key_cache_populated
    if _key_cache_populated:
        return _cached_key
    raw = os.environ.get(_ENV_VAR, "")
    if not raw:
        _cached_key = None
        _key_cache_populated = True
        return None
    try:
        key = base64.b64decode(raw)
    except Exception as e:
        raise SecretsCryptoError(
            f"{_ENV_VAR} is set but is not valid base64: {e}"
        ) from e
    if len(key) != 32:
        raise SecretsCryptoError(
            f"{_ENV_VAR} must decode to exactly 32 bytes (256 bits), got {len(key)}"
        )
    _cached_key = key
    _key_cache_populated = True
    return _cached_key


def is_encrypted(value) -> bool:
    """True iff *value* is a string already wrapped in our enc:v1: envelope."""
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* into a self-describing "enc:v1:..." token.

    No-op (returns input unchanged) when:
      - CODING_SECRETS_KEY is not configured (dev/CI posture; warns once).
      - the input is already an enc:v1: token (idempotent — never double-
        encrypts, even when a key IS configured).
    """
    if is_encrypted(plaintext):
        return plaintext

    key = _get_key()
    if key is None:
        global _warned_no_key
        if not _warned_no_key:
            logger.warning(
                "[secrets_crypto] %s is not configured — secret exam values "
                "are being stored UNENCRYPTED. Set %s in production.",
                _ENV_VAR, _ENV_VAR,
            )
            _warned_no_key = True
        return plaintext

    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = base64.b64encode(nonce + ciphertext).decode("ascii")
    return f"{_PREFIX}{payload}"


def decrypt(token) -> str:
    """Decrypt an "enc:v1:..." token, or pass through a legacy plaintext value
    unchanged (no "enc:v1:" prefix → assumed to be a pre-encryption row).

    Raises SecretsCryptoError if the value IS an enc:v1: token but no key is
    configured, or InvalidTag if decryption fails (tampered ciphertext or the
    wrong key)."""
    if token is None or token == "":
        return token
    if not isinstance(token, str) or not is_encrypted(token):
        # Legacy plaintext row (or already-plaintext value) — pass through.
        return token

    key = _get_key()
    if key is None:
        raise SecretsCryptoError(
            f"Cannot decrypt: value is an enc:v1: token but {_ENV_VAR} is not "
            f"configured in this process."
        )

    payload_b64 = token[len(_PREFIX):]
    raw = base64.b64decode(payload_b64)
    nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)  # raises InvalidTag on failure
    return plaintext.decode("utf-8")
