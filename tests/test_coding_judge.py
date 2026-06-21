"""Unit tests for the Edge Compiler output judge (spec Approach A).

No code execution here — the judge only compares the client's outputs against the
secret expected outputs the server holds. Normalization is conservative.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.coding_judge import judge_outputs, normalize_output  # noqa: E402


def test_trailing_whitespace_and_newlines_ignored():
    r = judge_outputs(actual=["5 \n", "10\n\n"], expected=["5", "10"], tolerances=[None, None])
    assert r["passed"] == 2 and r["total"] == 2
    assert r["per_case"] == [True, True]


def test_float_tolerance():
    r = judge_outputs(actual=["0.30000000004"], expected=["0.3"], tolerances=[1e-6])
    assert r["passed"] == 1


def test_float_outside_tolerance_fails():
    r = judge_outputs(actual=["0.5"], expected=["0.3"], tolerances=[1e-6])
    assert r["passed"] == 0


def test_mismatch_counts_per_case():
    r = judge_outputs(actual=["1", "WRONG", "3"], expected=["1", "2", "3"], tolerances=[None, None, None])
    assert r["passed"] == 2 and r["total"] == 3 and r["per_case"] == [True, False, True]


def test_length_mismatch_is_safe():
    # fewer actual than expected → the missing one fails, never throws.
    r = judge_outputs(actual=["1"], expected=["1", "2"], tolerances=[None, None])
    assert r["passed"] == 1 and r["total"] == 2 and r["per_case"] == [True, False]


def test_none_actual_fails_not_throws():
    r = judge_outputs(actual=[None], expected=["1"], tolerances=[None])
    assert r["passed"] == 0


def test_crlf_normalized():
    r = judge_outputs(actual=["a\r\nb\r\n"], expected=["a\nb"], tolerances=[None])
    assert r["passed"] == 1


def test_interior_spacing_is_significant():
    # We only strip TRAILING whitespace per line; interior spacing stays significant.
    r = judge_outputs(actual=["1  2"], expected=["1 2"], tolerances=[None])
    assert r["passed"] == 0


def test_float_multi_value_line():
    r = judge_outputs(actual=["1.0 2.0 3.0"], expected=["1 2 3"], tolerances=[1e-9])
    assert r["passed"] == 1


def test_float_value_count_mismatch_fails():
    r = judge_outputs(actual=["1.0 2.0"], expected=["1 2 3"], tolerances=[1e-9])
    assert r["passed"] == 0


def test_normalize_output_helper():
    assert normalize_output("a \nb\n\n\n") == "a\nb"
    assert normalize_output("") == ""
    assert normalize_output(None) == ""
