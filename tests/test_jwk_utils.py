"""Fixtures for JWK→public-key conversion (lti/jwk_utils.py).

Used to verify LTI platform JWTs, so a conversion bug = accepting/rejecting
launches wrongly. Pin: a real RSA JWK round-trips to the same public
numbers, base64url values without padding decode correctly, and a non-RSA
key type is rejected (not silently mishandled).
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.lti.jwk_utils import jwk_to_public_key


def _b64url(i: int) -> str:
    raw = i.to_bytes((i.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_rsa_jwk_round_trips():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_nums = priv.public_key().public_numbers()
    jwk = {"kty": "RSA", "n": _b64url(pub_nums.n), "e": _b64url(pub_nums.e)}

    key = jwk_to_public_key(jwk)
    got = key.public_numbers()
    assert got.n == pub_nums.n
    assert got.e == pub_nums.e


def test_unpadded_base64url_decodes():
    # e=65537 is "AQAB" in JWK — a classic value with no padding.
    jwk = {"kty": "RSA", "n": _b64url(rsa.generate_private_key(
        public_exponent=65537, key_size=2048).public_key().public_numbers().n),
        "e": "AQAB"}
    key = jwk_to_public_key(jwk)
    assert key.public_numbers().e == 65537


def test_non_rsa_key_type_rejected():
    with pytest.raises(ValueError, match="Unsupported JWK key type"):
        jwk_to_public_key({"kty": "EC", "crv": "P-256", "x": "...", "y": "..."})


def test_missing_kty_rejected():
    with pytest.raises(ValueError):
        jwk_to_public_key({"n": "abc", "e": "AQAB"})
