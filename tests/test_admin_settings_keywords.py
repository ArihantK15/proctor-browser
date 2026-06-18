"""Fixtures for audio-keyword normalisation (routers/admin_settings.py).

_normalise_keywords gates teacher-supplied custom audio-flag keywords:
strip, case-insensitive dedupe, length bounds, and a hard cap — rejecting
bad entries with a 400 rather than silently truncating. A regression that
weakens this lets unbounded/garbage keywords reach the audio matcher.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from app.routers.admin_settings import (
    _normalise_keywords, _MAX_KEYWORDS, _MAX_KEYWORD_LEN, _MIN_KEYWORD_LEN,
)


def test_empty_inputs_return_empty_list():
    assert _normalise_keywords(None) == []
    assert _normalise_keywords([]) == []


def test_non_list_rejected():
    with pytest.raises(HTTPException) as ei:
        _normalise_keywords("notalist")
    assert ei.value.status_code == 400


def test_non_string_entry_rejected():
    with pytest.raises(HTTPException) as ei:
        _normalise_keywords(["ok", 123])
    assert ei.value.status_code == 400


def test_strips_and_drops_blanks():
    assert _normalise_keywords(["  hello  ", "   "]) == ["hello"]


def test_case_insensitive_dedupe_preserves_first_casing_and_order():
    assert _normalise_keywords(["Answer", "answer", "Cheat"]) == ["Answer", "Cheat"]


def test_too_short_rejected():
    with pytest.raises(HTTPException) as ei:
        _normalise_keywords(["a"])  # below _MIN_KEYWORD_LEN
    assert ei.value.status_code == 400
    # boundary: exactly the minimum length is accepted
    assert _normalise_keywords(["a" * _MIN_KEYWORD_LEN]) == ["a" * _MIN_KEYWORD_LEN]


def test_too_long_rejected():
    with pytest.raises(HTTPException) as ei:
        _normalise_keywords(["x" * (_MAX_KEYWORD_LEN + 1)])
    assert ei.value.status_code == 400


def test_cap_enforced():
    # exactly _MAX_KEYWORDS unique entries is allowed
    ok = [f"kw{i:03d}" for i in range(_MAX_KEYWORDS)]
    assert len(_normalise_keywords(ok)) == _MAX_KEYWORDS
    # one more pushes over the cap → 400
    with pytest.raises(HTTPException) as ei:
        _normalise_keywords([f"kw{i:03d}" for i in range(_MAX_KEYWORDS + 1)])
    assert ei.value.status_code == 400
