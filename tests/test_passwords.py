"""Fixtures for password complexity, breach, and signup validation.

These are security gates: a regression that weakens them (e.g. dropping
the symbol rule or letting a breached password through) is a real
vulnerability, so the rules are pinned explicitly. The breached/
disposable sets and the HIBP network call are stubbed for determinism
and offline runs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services import passwords as pw
from app.services.passwords import PasswordError


@pytest.fixture(autouse=True)
def _stub_lists_and_hibp(monkeypatch):
    """Deterministic, offline: known breached/disposable sets and HIBP off."""
    monkeypatch.setattr(pw, "_BREACHED", {"password123!", "letmein!1aa"})
    monkeypatch.setattr(pw, "_DISPOSABLE", {"mailinator.com", "tempmail.io"})
    monkeypatch.setattr(pw, "_appears_in_hibp", lambda password: False)
    yield


VALID = "Str0ng!Pass"  # 11 chars, upper/lower/digit/symbol


def test_valid_password_passes():
    pw.validate_password(VALID)  # must not raise


@pytest.mark.parametrize("bad,msg", [
    ("Sh0rt!", "at least"),                 # too short
    ("alllower1!aa", "uppercase"),          # no upper
    ("ALLUPPER1!AA", "lowercase"),          # no lower
    ("NoDigitsHere!", "digit"),             # no digit
    ("NoSymbol1Here", "symbol"),            # no symbol
])
def test_complexity_rules_enforced(bad, msg):
    with pytest.raises(PasswordError, match=msg):
        pw.validate_password(bad)


def test_breached_password_rejected():
    with pytest.raises(PasswordError, match="data breach"):
        pw.validate_password("Password123!")  # lower() in breached set


def test_hibp_hit_rejected(monkeypatch):
    monkeypatch.setattr(pw, "_appears_in_hibp", lambda password: True)
    with pytest.raises(PasswordError, match="data breach"):
        pw.validate_password(VALID)


def test_hibp_disabled_makes_no_network_call(monkeypatch):
    monkeypatch.setattr(pw, "_HIBP_CHECK_ENABLED", False)
    # _appears_in_hibp returns False immediately when disabled — no urlopen.
    assert pw._appears_in_hibp(VALID) is False


@pytest.mark.asyncio
async def test_async_validator_mirrors_sync():
    await pw.validate_password_async(VALID)  # passes
    with pytest.raises(PasswordError, match="symbol"):
        await pw.validate_password_async("NoSymbol1Here")


def test_is_disposable_email():
    assert pw.is_disposable_email("a@mailinator.com") is True
    assert pw.is_disposable_email("A@TempMail.IO") is True  # case-insensitive
    assert pw.is_disposable_email("a@gmail.com") is False
    assert pw.is_disposable_email("not-an-email") is False


def test_validate_signup_rejects_bad_inputs():
    with pytest.raises(PasswordError, match="valid email"):
        pw.validate_signup("noat", VALID, "Name")
    with pytest.raises(PasswordError, match="Full name"):
        pw.validate_signup("a@gmail.com", VALID, "   ")
    with pytest.raises(PasswordError, match="Disposable"):
        pw.validate_signup("a@mailinator.com", VALID, "Name")


def test_validate_signup_accepts_good_input():
    pw.validate_signup("teacher@school.edu", VALID, "Real Name")  # must not raise
