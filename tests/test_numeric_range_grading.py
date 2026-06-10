"""Tests for numeric-range grading in answers_match (scoring.py).

A `correct` value of the form `range:MIN:MAX` grades a numeric student answer
as correct when MIN <= x <= MAX (inclusive). Decimals supported (tolerance
bands); MIN == MAX is an exact value. Non-range correct values keep the legacy
string-set equality behaviour untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring import answers_match  # noqa: E402


def test_within_range_is_correct():
    assert answers_match("42", "range:40:45") is True


def test_below_range_is_wrong():
    assert answers_match("39", "range:40:45") is False


def test_above_range_is_wrong():
    assert answers_match("46", "range:40:45") is False


def test_boundaries_inclusive():
    assert answers_match("40", "range:40:45") is True
    assert answers_match("45", "range:40:45") is True


def test_decimal_tolerance_band():
    assert answers_match("9.8", "range:9.75:9.85") is True
    assert answers_match("9.7", "range:9.75:9.85") is False


def test_exact_value_when_min_equals_max():
    assert answers_match("7", "range:7:7") is True
    assert answers_match("8", "range:7:7") is False


def test_reversed_bounds_are_tolerated():
    # Defensive: a teacher who enters max first still gets a sane band.
    assert answers_match("42", "range:45:40") is True


def test_non_numeric_student_answer_is_wrong():
    assert answers_match("", "range:40:45") is False
    assert answers_match("forty", "range:40:45") is False


def test_malformed_range_is_wrong_not_crash():
    assert answers_match("42", "range:abc:def") is False
    assert answers_match("42", "range:40") is False


def test_legacy_mcq_unaffected():
    assert answers_match("A", "A") is True
    assert answers_match("a", "A") is True
    assert answers_match("A", "B") is False


def test_legacy_multi_set_unaffected():
    assert answers_match("A,C", "C,A") is True
    assert answers_match("A", "A,C") is False
