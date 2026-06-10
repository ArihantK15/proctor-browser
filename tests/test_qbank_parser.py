"""Unit tests for the rule-based question parser (app/parsers/question_parser.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.question_parser import parse_questions, BLOCKING_FLAGS  # noqa: E402
from app.parsers.document import DocLine  # noqa: E402

MCQ = """1. What is 2+2?
(a) 3
(b) 4
(c) 5
(d) 6

2. Capital of France?
(a) London
(b) Paris
(c) Rome
(d) Berlin

Answers
1-B 2-B
"""


def test_parses_two_mcqs_with_answers():
    qs = parse_questions(MCQ)
    assert len(qs) == 2
    assert qs[0].question.startswith("What is 2+2")
    assert qs[0].question_type == "mcq_single"
    assert qs[0].options["B"] == "4"
    assert qs[0].correct == "B"
    assert qs[0].flags == []
    assert qs[0].confidence >= 0.8


def test_letter_dot_option_style():
    qs = parse_questions("1. Pick one\nA. red\nB. blue\nAns: A")
    assert qs[0].options == {"A": "red", "B": "blue"}
    assert qs[0].correct == "A"


def test_missing_answer_is_blocking_flag():
    qs = parse_questions("1. No answer here\n(a) x\n(b) y")
    assert "no_answer" in qs[0].flags
    assert qs[0].correct == ""
    assert set(qs[0].flags) & BLOCKING_FLAGS


def test_multi_answer_promotes_type():
    qs = parse_questions("1. Select all\n(a) x\n(b) y\n(c) z\nAnswers\n1-AC")
    assert qs[0].question_type == "mcq_multi"
    assert qs[0].correct == "AC"


def test_true_false_detected():
    qs = parse_questions("1. The sky is blue.\n(a) True\n(b) False\nAns: A")
    assert qs[0].question_type == "true_false"


def test_numeric_no_options():
    qs = parse_questions("1. Compute 6*7.\nAnswers\n1-42")
    assert qs[0].question_type == "numeric"
    assert qs[0].options == {}
    assert qs[0].correct == "42"


def test_few_options_flagged():
    qs = parse_questions("1. Only one option?\n(a) lonely\nAns: A")
    assert "few_options" in qs[0].flags


def test_qnum_markers_q_prefix():
    qs = parse_questions("Q1. first\n(a) x\n(b) y\nQ2. second\n(a) p\n(b) q")
    assert len(qs) == 2


def test_junk_between_questions_ignored():
    qs = parse_questions("Page 1 of 3\n\n1. Real Q\n(a) x\n(b) y\nAns: A\n\n-- footer --")
    assert len(qs) == 1
    assert qs[0].question == "Real Q"


def test_empty_input_returns_empty():
    assert parse_questions("") == []


# ── Task 5: math/visual routing (layout-aware) ──────────────────────

def test_math_font_routes_region():
    text = "1. Solve\n(a) x\n(b) y\nAns: A"
    lines = [
        DocLine(text="1. Solve", page=0, bbox=[10, 10, 200, 24], fonts={"CMMI10"}),
        DocLine(text="(a) x", page=0, bbox=[10, 26, 80, 40], fonts={"Helvetica"}),
        DocLine(text="(b) y", page=0, bbox=[10, 42, 80, 56], fonts={"Helvetica"}),
        DocLine(text="Ans: A", page=0, bbox=[10, 58, 80, 72], fonts={"Helvetica"}),
    ]
    qs = parse_questions(text, lines=lines)
    assert qs[0]._region is not None
    assert "has_image" in qs[0].flags


def test_replacement_chars_route_region():
    text = "1. Bad � glyph\n(a) x\n(b) y\nAns: A"
    lines = [
        DocLine(text="1. Bad � glyph", page=0, bbox=[10, 10, 200, 24], fonts=set()),
        DocLine(text="(a) x", page=0, bbox=[10, 26, 80, 40], fonts=set()),
        DocLine(text="(b) y", page=0, bbox=[10, 42, 80, 56], fonts=set()),
        DocLine(text="Ans: A", page=0, bbox=[10, 58, 80, 72], fonts=set()),
    ]
    qs = parse_questions(text, lines=lines)
    assert "has_image" in qs[0].flags


def test_docx_math_gets_review_flag_not_region():
    # No bbox available (DOCX) → math_review flag, no region.
    text = "1. x² + 1 = 0 �\n(a) p\n(b) q\nAns: A"
    lines = [
        DocLine(text="1. x² + 1 = 0 �", page=0, bbox=None, fonts=set()),
        DocLine(text="(a) p", page=0, bbox=None, fonts=set()),
        DocLine(text="(b) q", page=0, bbox=None, fonts=set()),
        DocLine(text="Ans: A", page=0, bbox=None, fonts=set()),
    ]
    qs = parse_questions(text, lines=lines)
    assert qs[0]._region is None
    assert "math_review" in qs[0].flags
