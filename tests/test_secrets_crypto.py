"""Tests for app/services/secrets_crypto.py — AES-256-GCM envelope encryption
for secret exam data (coding `expected_output`, MCQ `correct`).

Key handling: CODING_SECRETS_KEY env var holds a base64-encoded 32-byte key.
These tests monkeypatch that env var per-test and must reset the module's
cached key afterwards so tests don't leak state into each other (the module
caches the parsed key in a module-level variable for process lifetime, but
tests need a fresh read each time).
"""
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import secrets_crypto


def _b64_key(seed: bytes = b"\x01") -> str:
    """Build a valid base64-encoded 32-byte key from a 1-byte seed (repeated)."""
    raw = (seed * 32)[:32]
    return base64.b64encode(raw).decode()


KEY_A = _b64_key(b"\x01")
KEY_B = _b64_key(b"\x02")


@pytest.fixture(autouse=True)
def _reset_key_cache():
    """Ensure each test starts with a clean key cache so monkeypatch.setenv
    changes actually take effect (the module caches the parsed key)."""
    secrets_crypto.reset_key_cache()
    yield
    secrets_crypto.reset_key_cache()


class TestRoundTrip:
    def test_encrypt_then_decrypt_returns_original(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        plaintext = "42"
        token = secrets_crypto.encrypt(plaintext)
        assert token != plaintext
        assert secrets_crypto.is_encrypted(token)
        assert secrets_crypto.decrypt(token) == plaintext


class TestTamper:
    def test_tampered_ciphertext_raises(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        token = secrets_crypto.encrypt("the-secret-answer")
        prefix, version, payload_b64 = token.split(":", 2)
        raw = bytearray(base64.b64decode(payload_b64))
        # Flip a byte in the ciphertext region (after the 12-byte nonce).
        raw[20] ^= 0xFF
        tampered = f"{prefix}:{version}:{base64.b64encode(bytes(raw)).decode()}"
        with pytest.raises(Exception):
            secrets_crypto.decrypt(tampered)


class TestLegacyPlaintextPassthrough:
    def test_decrypt_plain_string_without_prefix_unchanged_with_key(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        assert secrets_crypto.decrypt("legacy-plaintext-answer") == "legacy-plaintext-answer"

    def test_decrypt_plain_string_without_prefix_unchanged_without_key(self, monkeypatch):
        monkeypatch.delenv("CODING_SECRETS_KEY", raising=False)
        secrets_crypto.reset_key_cache()
        assert secrets_crypto.decrypt("legacy-plaintext-answer") == "legacy-plaintext-answer"


class TestNoKeyNoOp:
    def test_encrypt_without_key_returns_plaintext_unchanged(self, monkeypatch):
        monkeypatch.delenv("CODING_SECRETS_KEY", raising=False)
        secrets_crypto.reset_key_cache()
        plaintext = "no-key-configured-value"
        result = secrets_crypto.encrypt(plaintext)
        assert result == plaintext
        assert not secrets_crypto.is_encrypted(result)

    def test_encrypt_without_key_warns_once(self, monkeypatch, caplog):
        monkeypatch.delenv("CODING_SECRETS_KEY", raising=False)
        secrets_crypto.reset_key_cache()
        import logging
        with caplog.at_level(logging.WARNING):
            secrets_crypto.encrypt("a")
            secrets_crypto.encrypt("b")
            secrets_crypto.encrypt("c")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1


class TestIdempotency:
    def test_encrypting_already_encrypted_token_is_noop(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        token = secrets_crypto.encrypt("some-secret")
        token_again = secrets_crypto.encrypt(token)
        assert token_again == token


class TestWrongKeyFailure:
    def test_decrypt_with_different_key_raises(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        token = secrets_crypto.encrypt("top-secret-expected-output")

        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_B)
        secrets_crypto.reset_key_cache()
        with pytest.raises(Exception):
            secrets_crypto.decrypt(token)


class TestIsEncrypted:
    def test_is_encrypted_true_for_token(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        token = secrets_crypto.encrypt("x")
        assert secrets_crypto.is_encrypted(token) is True

    def test_is_encrypted_false_for_plain_string(self):
        assert secrets_crypto.is_encrypted("plain") is False

    def test_is_encrypted_false_for_non_string(self):
        assert secrets_crypto.is_encrypted(None) is False
        assert secrets_crypto.is_encrypted(123) is False


class TestDecryptEdgeCases:
    def test_decrypt_none_returns_none(self):
        assert secrets_crypto.decrypt(None) is None

    def test_decrypt_empty_string_returns_empty_string(self):
        assert secrets_crypto.decrypt("") == ""

    def test_decrypt_encrypted_token_without_key_raises(self, monkeypatch):
        monkeypatch.setenv("CODING_SECRETS_KEY", KEY_A)
        secrets_crypto.reset_key_cache()
        token = secrets_crypto.encrypt("secret")

        monkeypatch.delenv("CODING_SECRETS_KEY", raising=False)
        secrets_crypto.reset_key_cache()
        with pytest.raises(Exception):
            secrets_crypto.decrypt(token)
