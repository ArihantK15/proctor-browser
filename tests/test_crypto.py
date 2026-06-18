"""Fixtures for Fernet token encryption (services/crypto.py).

Contract: encrypt→decrypt round-trips; identical plaintext yields
distinct ciphertexts (Fernet's random IV) yet both decrypt; tampered or
garbage ciphertext decrypts to "" rather than raising (callers treat ""
as failure); and a missing key fails loudly at use, never silently
encrypting with an empty secret.

conftest sets a valid TOTP_ENCRYPTION_KEY before app import, so the
module-level Fernet is usable here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import crypto


def test_round_trip():
    ct = crypto.encrypt_token("hunter2")
    assert ct != "hunter2"
    assert crypto.decrypt_token(ct) == "hunter2"


def test_round_trip_unicode_and_empty():
    assert crypto.decrypt_token(crypto.encrypt_token("")) == ""
    assert crypto.decrypt_token(crypto.encrypt_token("naïve café 🔐")) == "naïve café 🔐"


def test_same_plaintext_distinct_ciphertext():
    a = crypto.encrypt_token("secret")
    b = crypto.encrypt_token("secret")
    assert a != b  # random IV per encryption
    assert crypto.decrypt_token(a) == crypto.decrypt_token(b) == "secret"


def test_decrypt_garbage_returns_empty():
    assert crypto.decrypt_token("not-a-valid-token") == ""
    assert crypto.decrypt_token("") == ""


def test_decrypt_tampered_returns_empty():
    ct = crypto.encrypt_token("secret")
    tampered = ct[:-4] + ("AAAA" if not ct.endswith("AAAA") else "BBBB")
    assert crypto.decrypt_token(tampered) == ""


def test_missing_key_raises(monkeypatch):
    """No key configured must raise at use — never encrypt with an empty
    secret."""
    monkeypatch.setattr(crypto, "_FERNET", None)
    monkeypatch.setattr(crypto, "_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="TOTP_ENCRYPTION_KEY"):
        crypto.encrypt_token("x")
    # restore the cached instance for any later test in this process
    monkeypatch.undo()
    crypto._get_fernet()
