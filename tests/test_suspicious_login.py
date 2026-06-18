"""Fixtures for new-device detection (services/suspicious_login.py).

The notify decision must fire ONLY when both the /24 subnet and the
user-agent are unseen in recent history — a stricter AND so a Chrome
update on the home network or roaming on the same laptop doesn't spam
"new sign-in" emails. These tests pin the subnet reducer and the
both-must-differ rule, including the self-row skip.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.suspicious_login import _ip_to_subnet, _is_new_device


def test_ipv4_reduced_to_24():
    assert _ip_to_subnet("192.168.1.42") == "192.168.1"


def test_ipv6_reduced_to_first_four_groups():
    assert _ip_to_subnet("2001:db8:85a3:1:2:3:4:5") == "2001:db8:85a3:1"


def test_malformed_or_empty_ip_returns_empty():
    assert _ip_to_subnet("") == ""
    assert _ip_to_subnet("10.0.0") == ""        # only 3 octets
    assert _ip_to_subnet("garbage") == ""


def _ev(ip, ua):
    return {"ip": ip, "user_agent": ua}


def test_new_device_when_both_subnet_and_ua_unseen():
    events = [
        _ev("203.0.113.7", "NewBrowser/1"),   # current (self)
        _ev("198.51.100.9", "OldBrowser/9"),  # prior, fully different
    ]
    assert _is_new_device(events, "203.0.113", "NewBrowser/1") is True


def test_same_subnet_different_ua_is_not_new():
    """Browser upgrade on the home network must not flag."""
    events = [
        _ev("192.168.1.5", "Chrome/120"),  # current
        _ev("192.168.1.9", "Chrome/119"),  # prior, same /24
    ]
    assert _is_new_device(events, "192.168.1", "Chrome/120") is False


def test_same_ua_different_subnet_is_not_new():
    """Roaming with the same laptop must not flag."""
    events = [
        _ev("203.0.113.7", "Chrome/120"),   # current
        _ev("198.51.100.9", "Chrome/120"),  # prior, same UA
    ]
    assert _is_new_device(events, "203.0.113", "Chrome/120") is False


def test_self_row_skipped_only_once():
    """A single matching event is the current login's own row and must be
    skipped; with no other prior match it's a new device."""
    events = [_ev("203.0.113.7", "NewBrowser/1")]  # only the self row
    assert _is_new_device(events, "203.0.113", "NewBrowser/1") is True


def test_duplicate_prior_same_device_is_not_new():
    """Two identical rows → one is self, the other is genuine history."""
    events = [
        _ev("203.0.113.7", "Chrome/120"),
        _ev("203.0.113.8", "Chrome/120"),  # prior login, same device
    ]
    assert _is_new_device(events, "203.0.113", "Chrome/120") is False


def test_empty_current_subnet_or_ua_never_flags():
    events = [_ev("198.51.100.9", "OldBrowser/9")]
    assert _is_new_device(events, "", "Chrome/120") is False
    assert _is_new_device(events, "203.0.113", "") is False
