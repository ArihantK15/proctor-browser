"""Unit tests for the answer-key section parser (app/parsers/answer_key.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.answer_key import parse_answer_key, find_answer_key_block  # noqa: E402


def test_inline_dash_pairs():
    assert parse_answer_key("1-C 2-A 3-D 4-B") == {1: "C", 2: "A", 3: "D", 4: "B"}


def test_dot_and_paren_separators():
    assert parse_answer_key("1. C\n2) A\n3 - D") == {1: "C", 2: "A", 3: "D"}


def test_multi_letter_answer_preserved():
    assert parse_answer_key("1-AC 2-B") == {1: "AC", 2: "B"}


def test_numeric_answer():
    assert parse_answer_key("1-42 2-3.14") == {1: "42", 2: "3.14"}


def test_lowercase_normalised_to_upper_letters():
    assert parse_answer_key("1-c 2-a") == {1: "C", 2: "A"}


def test_find_block_detects_answers_heading():
    text = "Q1 ...\n(a) ..\n\nAnswers\n1-C 2-A 3-D"
    body, key = find_answer_key_block(text)
    assert "Answers" not in body
    assert key == {1: "C", 2: "A", 3: "D"}


def test_find_block_returns_empty_when_no_key():
    body, key = find_answer_key_block("Q1 ...\n(a) ..\n(b) ..")
    assert key == {}
    assert body.startswith("Q1")
