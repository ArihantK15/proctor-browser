"""Shared encryption/decryption using Fernet (symmetric AES).

Uses the same TOTP_ENCRYPTION_KEY env var for key material so there is
only one secret to manage in production.
"""

import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY = os.environ.get("TOTP_ENCRYPTION_KEY", "")
_FERNET: Fernet | None = None


def _get_fernet() -> Fernet:
    global _FERNET
    if _FERNET is None:
        if not _ENCRYPTION_KEY:
            raise RuntimeError(
                "TOTP_ENCRYPTION_KEY not set. Generate one and add it to .env."
            )
        _FERNET = Fernet(_ENCRYPTION_KEY.encode())
    return _FERNET


def encrypt_token(plaintext: str) -> str:
    """Encrypt a string using Fernet. Returns base64 ciphertext string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted string. Returns plaintext or empty on failure."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception as e:
        logger.warning("[crypto] decryption failed: %s", e)
        return ""
