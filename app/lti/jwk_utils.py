"""JWK (JSON Web Key) helper — converts JWK dicts to cryptography keys.

Replaces jose.jwk.construct() so we can drop python-jose entirely.
"""
from __future__ import annotations

import base64
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


def jwk_to_public_key(jwk_dict: dict):
    """Convert an RSA JWK dictionary to a cryptography public key object.
    
    The JWK dict must have ``kty``, ``n``, and ``e`` fields (standard
    RSA public key JWK format).  Returns a ``cryptography`` public key
    that can be passed to ``jwt.decode(..., key)``.
    """
    if jwk_dict.get("kty") != "RSA":
        raise ValueError(f"Unsupported JWK key type: {jwk_dict.get('kty')}")
    
    def _b64_to_int(val: str) -> int:
        return int.from_bytes(base64.urlsafe_b64decode(val + "=="), "big")
    
    n = _b64_to_int(jwk_dict["n"])
    e = _b64_to_int(jwk_dict["e"])
    return rsa.RSAPublicNumbers(e, n).public_key(default_backend())
