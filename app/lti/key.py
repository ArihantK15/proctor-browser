"""RSA key pair management + JWKS generation for LTI 1.3.

Supports three modes (in priority order):
  1. LTI_PRIVATE_KEY env var (base64-encoded PEM)
  2. LTI_KEY_FILE env var (path to PEM file)
  3. Auto-generate in-memory (dev/test only — keys reset on restart)

The public key is exposed via JWKS at /lti/jwks.
"""

import base64
import json
import logging
import os
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

_private_key_pem: str | None = None
_public_key_pem: str | None = None
_kid: str = "lti-key-1"
_jwks_cache: dict | None = None


def _load_or_generate_key_pair() -> tuple[str, str]:
    priv = os.environ.get("LTI_PRIVATE_KEY", "")
    if priv:
        try:
            decoded = base64.b64decode(priv).decode("utf-8")
            key = serialization.load_pem_private_key(
                decoded.encode(), password=None, backend=default_backend()
            )
            pub = key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()
            return decoded, pub
        except Exception as e:
            logger.warning("Failed to load LTI_PRIVATE_KEY env var: %s", e)

    key_file = os.environ.get("LTI_KEY_FILE", "")
    if key_file and os.path.isfile(key_file):
        try:
            with open(key_file) as f:
                pem = f.read()
            key = serialization.load_pem_private_key(
                pem.encode(), password=None, backend=default_backend()
            )
            pub = key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()
            return pem, pub
        except Exception as e:
            logger.warning("Failed to load LTI_KEY_FILE %s: %s", key_file, e)

    logger.info("No LTI key configured — generating ephemeral key pair (dev mode)")
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


def get_key_pair() -> tuple[str, str]:
    global _private_key_pem, _public_key_pem
    if _private_key_pem is None:
        _private_key_pem, _public_key_pem = _load_or_generate_key_pair()
    return _private_key_pem, _public_key_pem


def get_private_key():
    pem, _ = get_key_pair()
    return serialization.load_pem_private_key(
        pem.encode(), password=None, backend=default_backend()
    )


def generate_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    _, pub_pem = get_key_pair()
    pub_key = serialization.load_pem_public_key(
        pub_pem.encode(), backend=default_backend()
    )
    pub_numbers = pub_key.public_numbers()

    n = base64.urlsafe_b64encode(
        pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, byteorder="big")
    ).rstrip(b"=").decode()
    e = base64.urlsafe_b64encode(
        pub_numbers.e.to_bytes(3, byteorder="big")
    ).rstrip(b"=").decode()

    _jwks_cache = {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": _kid,
                "n": n,
                "e": e,
            }
        ]
    }
    return _jwks_cache


def get_kid() -> str:
    return _kid


def sign_jwt_payload(payload: dict) -> str:
    """Sign a JWT payload with our private key using RS256.

    Returns the full JWT string (header.payload.signature).
    Used for AGS grade passback and other tool-initiated requests.
    """
    import time
    from jose import jwt as jose_jwt

    key_pem, _ = get_key_pair()
    headers = {"kid": _kid, "typ": "JWT"}
    return jose_jwt.encode(
        payload,
        key_pem,
        algorithm="RS256",
        headers=headers,
    )
