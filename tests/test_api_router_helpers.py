"""Fixtures for the public API session-shaping helper (routers/api.py).

_shape_api_session renames internal exam_sessions columns to the documented
public API field names. A drift here silently changes the public contract,
so the alias mapping is pinned explicitly.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.api import _shape_api_session


def test_renames_documented_aliases():
    row = {"roll_number": "R1", "full_name": "Asha", "submitted_at": "2026-01-01T00:00:00Z"}
    out = _shape_api_session(row)
    assert out == {
        "student_roll_number": "R1",
        "student_name": "Asha",
        "ended_at": "2026-01-01T00:00:00Z",
    }


def test_passes_through_unmapped_keys():
    row = {"session_key": "sk", "score": 8, "status": "completed"}
    assert _shape_api_session(row) == row


def test_mixed_row_renames_only_aliases():
    row = {"roll_number": "R1", "score": 8}
    assert _shape_api_session(row) == {"student_roll_number": "R1", "score": 8}


def test_empty_row():
    assert _shape_api_session({}) == {}
