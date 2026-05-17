"""RSA key pair management + JWKS generation for LTI 1.3.

Supports multiple KIDs for key rotation (C19):
  1. LTI_PRIVATE_KEY (base64-encoded PEM) + LTI_KID (default "lti-key-1")
  2. LTI_NEXT_PRIVATE_KEY + LTI_NEXT_KID — secondary key exposed in JWKS
  3. LTI_KEY_FILE (path to PEM file — single key)
  4. Auto-generate in-memory (dev/test only — keys reset on restart)

The JWKS endpoint exposes all configured keys so the LMS can validate
tokens signed with any active KID.
"""
import base64
import json
import logging
import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

_keys: dict[str, str] = {}     # kid → private key PEM
_public_keys: dict[str, str] = {}  # kid → public key PEM
_jwks_cache: dict | None = None


def _load_key_from_pem(pem: str) -> tuple[str, str]:
    key = serialization.load_pem_private_key(
        pem.encode(), password=None, backend=default_backend()
    )
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem, pub


def _generate_ephemeral() -> tuple[str, str]:
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def _load_keys() -> dict[str, tuple[str, str]]:
    """Load all configured key pairs. Returns {kid: (priv_pem, pub_pem)}."""
    result: dict[str, tuple[str, str]] = {}

    # Primary key from LTI_PRIVATE_KEY env var
    priv_b64 = os.environ.get("LTI_PRIVATE_KEY", "")
    if priv_b64:
        try:
            decoded = base64.b64decode(priv_b64).decode("utf-8")
            kid = os.environ.get("LTI_KID", "lti-key-1")
            result[kid] = _load_key_from_pem(decoded)
        except Exception as e:
            logger.warning("Failed to load LTI_PRIVATE_KEY: %s", e)

    # Primary key from LTI_KEY_FILE
    key_file = os.environ.get("LTI_KEY_FILE", "")
    if not result and key_file and os.path.isfile(key_file):
        try:
            with open(key_file) as f:
                pem = f.read()
            kid = os.environ.get("LTI_KID", "lti-key-1")
            result[kid] = _load_key_from_pem(pem)
        except Exception as e:
            logger.warning("Failed to load LTI_KEY_FILE %s: %s", key_file, e)

    # Rotation key (LTI_NEXT_PRIVATE_KEY + LTI_NEXT_KID)
    next_priv_b64 = os.environ.get("LTI_NEXT_PRIVATE_KEY", "")
    if next_priv_b64:
        try:
            decoded = base64.b64decode(next_priv_b64).decode("utf-8")
            next_kid = os.environ.get("LTI_NEXT_KID", "lti-key-2")
            result[next_kid] = _load_key_from_pem(decoded)
        except Exception as e:
            logger.warning("Failed to load LTI_NEXT_PRIVATE_KEY: %s", e)

    # Fallback: ephemeral key for dev/testing
    if not result:
        logger.info("No LTI key configured — generating ephemeral key pair (dev mode)")
        kid = os.environ.get("LTI_KID", "lti-key-dev")
        result[kid] = _generate_ephemeral()

    return result


def get_private_key(kid: str | None = None):
    """Load and return the private key object for a given KID (or default)."""
    if not _keys:
        _init_keys()
    kid = kid or next(iter(_keys))
    pem = _keys.get(kid)
    if not pem:
        raise ValueError(f"Unknown KID: {kid}")
    return serialization.load_pem_private_key(
        pem.encode(), password=None, backend=default_backend()
    )


def get_public_key(kid: str | None = None):
    """Load and return the public key object for a given KID (or default)."""
    if not _public_keys:
        _init_keys()
    kid = kid or next(iter(_public_keys))
    pem = _public_keys.get(kid)
    if not pem:
        raise ValueError(f"Unknown KID: {kid}")
    return serialization.load_pem_public_key(
        pem.encode(), backend=default_backend()
    )


def get_kid() -> str:
    """Return the default (primary) KID for signing new tokens."""
    if not _keys:
        _init_keys()
    return next(iter(_keys))


def get_all_kids() -> list[str]:
    """Return all configured KIDs (for JWKS generation)."""
    if not _keys:
        _init_keys()
    return list(_keys.keys())


def generate_jwks() -> dict:
    """Generate JWKS with all configured keys."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    if not _public_keys:
        _init_keys()

    keys_list = []
    for kid in get_all_kids():
        pub_key = get_public_key(kid)
        pub_numbers = pub_key.public_numbers()
        n = base64.urlsafe_b64encode(
            pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, byteorder="big")
        ).rstrip(b"=").decode()
        e = base64.urlsafe_b64encode(
            pub_numbers.e.to_bytes(3, byteorder="big")
        ).rstrip(b"=").decode()
        keys_list.append({
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": kid,
            "n": n,
            "e": e,
        })

    _jwks_cache = {"keys": keys_list}
    return _jwks_cache


def sign_jwt_payload(payload: dict, kid: str | None = None) -> str:
    """Sign a JWT payload with a private key using RS256.

    Falls back to the default KID if none specified.
    Used for AGS grade passback and other tool-initiated requests.
    """
    import time
    import jwt as _jwt

    if not _keys:
        _init_keys()
    kid = kid or get_kid()
    pem = _keys.get(kid)
    if not pem:
        raise ValueError(f"Unknown KID: {kid}")
    headers = {"kid": kid, "typ": "JWT"}
    return _jwt.encode(
        payload, pem, algorithm="RS256", headers=headers,
    )


def _init_keys():
    global _keys, _public_keys, _jwks_cache
    _keys = {}
    _public_keys = {}
    _jwks_cache = None
    loaded = _load_keys()
    for kid, (priv, pub) in loaded.items():
        _keys[kid] = priv
        _public_keys[kid] = pub


def get_key_pair() -> tuple[str, str]:
    """Return (private_key_pem, public_key_pem) for the default KID.
    Kept for backward compatibility."""
    if not _keys:
        _init_keys()
    kid = get_kid()
    return _keys[kid], _public_keys[kid]
