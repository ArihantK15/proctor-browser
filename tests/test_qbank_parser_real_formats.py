"""Hardening for real Indian-coaching (JEE/NEET, MathonGo-style) papers:
  - single-line numbered options:  (1) a (2) b (3) c (4) d
  - keyworded / numbered answers:  "Answer Key : (3)"  -> option C
  - the furniture stripper must NEVER delete question-marker lines.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.question_parser import parse_questions  # noqa: E402
from app.parsers.document import DocLine  # noqa: E402


# ── single-line numbered options ───────────────────────────────────

def test_single_line_four_options_split():
    qs = parse_questions("1. Compute.\n(1) 0 (2) 1 (3) 2 (4) 3\nAnswer Key : (3)")
    assert qs[0].options == {"A": "0", "B": "1", "C": "2", "D": "3"}
    assert qs[0].correct == "C"          # (3) → 3rd option → C
    assert qs[0].flags == []


def test_single_line_statement_options():
    qs = parse_questions(
        "1. Which is correct?\n(1) only S1 is correct (2) only S2 is correct\nAns: (2)")
    assert qs[0].options == {"A": "only S1 is correct", "B": "only S2 is correct"}
    assert qs[0].correct == "B"


def test_numbered_answer_maps_to_letter():
    qs = parse_questions("1. Q?\n(1) p (2) q (3) r (4) s\nMathonGo Answer Key : (4)")
    assert qs[0].correct == "D"


def test_numeric_answer_key_stays_numeric():
    # No options + bare numeric key → value, NOT a letter mapping.
    qs = parse_questions("1. How many?\nAnswer Key : 91")
    assert qs[0].question_type == "numeric"
    assert qs[0].correct == "91"


def test_answer_keyword_in_stem_does_not_false_match():
    # "Answer carefully" must not be read as an answer (no standalone A-F/digit).
    qs = parse_questions("1. Answer carefully and pick one.\n(a) x\n(b) y\nAns: B")
    assert qs[0].correct == "B"


# ── furniture stripper must not eat question markers ───────────────

def _l(text, page, top):
    return DocLine(text=text, page=page, bbox=[40, top, 400, top + 12], fonts=set())


def test_furniture_strip_preserves_top_of_page_question_markers():
    # Each page has a repeating header (furniture) AND a "Qn." marker near the
    # top. Digit-normalised, "Q#." repeats across pages — but it is CONTENT and
    # must survive; only the running header should be stripped.
    lines = [
        _l("Answer Keys MathonGo", 0, 20),       # repeating header furniture
        _l("Q1.", 0, 40),
        _l("First question?", 0, 60),
        _l("(a) x", 0, 80), _l("(b) y", 0, 100), _l("Ans: A", 0, 120),
        _l("Answer Keys MathonGo", 1, 20),       # repeating header furniture
        _l("Q2.", 1, 40),
        _l("Second question?", 1, 60),
        _l("(a) p", 1, 80), _l("(b) q", 1, 100), _l("Ans: B", 1, 120),
    ]
    qs = parse_questions("x", lines=lines)
    assert len(qs) == 2                          # both markers survived
    assert qs[0].question == "First question?"
    assert qs[1].question == "Second question?"
    # The running header must NOT have leaked into either stem.
    assert all("MathonGo" not in q.question for q in qs)
