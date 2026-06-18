"""Fixtures for practice-mode detection + canned data (services/practice.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.constants import PRACTICE_PREFIX
from app.services.practice import (
    is_practice, PRACTICE_QUESTIONS, _practice_validate_response,
)


def test_is_practice_matches_prefix():
    assert is_practice(PRACTICE_PREFIX + "anything") is True


def test_is_practice_false_for_normal_and_empty():
    assert is_practice("2102508447") is False
    assert is_practice("") is False
    assert is_practice(None) is False


def test_practice_questions_are_well_formed():
    assert len(PRACTICE_QUESTIONS) == 3
    for q in PRACTICE_QUESTIONS:
        assert set(q["options"]) == {"A", "B", "C", "D"}
        assert q["correct"] in q["options"]
        assert q["question_type"] == "mcq_single"


def test_practice_validate_response_shape():
    r = _practice_validate_response("PRACTICE_xyz")
    assert r["valid"] is True
    assert r["practice"] is True
    assert r["roll_number"] == "PRACTICE_xyz"
    assert r["full_name"] == "Practice Student"
