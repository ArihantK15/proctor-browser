"""Hardening tests for the question parser against real-paper failure modes:
asterisk-marked answers, space-separated/table answer keys, and repeating
page header/footer furniture that bleeds into question stems.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.answer_key import parse_answer_key, find_answer_key_block  # noqa: E402
from app.parsers.question_parser import parse_questions  # noqa: E402
from app.parsers.document import DocLine  # noqa: E402


# ── Asterisk / star-marked correct option ──────────────────────────

def test_asterisk_marks_correct_and_is_stripped():
    qs = parse_questions("1. Capital of France?\n(a) London\n(b) Paris*\n(c) Rome\n(d) Berlin")
    assert qs[0].correct == "B"
    assert qs[0].options["B"] == "Paris"      # marker stripped from the text
    assert qs[0].flags == []


def test_leading_asterisk_also_works():
    qs = parse_questions("1. Pick\n(a) *Right\n(b) Wrong")
    assert qs[0].correct == "A"
    assert qs[0].options["A"] == "Right"


def test_multiple_stars_promote_to_multi():
    qs = parse_questions("1. Select all\n(a) x*\n(b) y\n(c) z*")
    assert qs[0].question_type == "mcq_multi"
    assert qs[0].correct == "AC"


# ── Space-separated / table answer keys ────────────────────────────

def test_space_separated_key_pairs():
    assert parse_answer_key("1 A 2 B 3 C") == {1: "A", 2: "B", 3: "C"}


def test_table_answer_key_block():
    text = ("1. Q one?\n(a) x\n(b) y\n2. Q two?\n(a) p\n(b) q\n"
            "Answer Key\nQ.No Ans\n1 A\n2 B")
    body, key = find_answer_key_block(text)
    assert key == {1: "A", 2: "B"}


def test_space_key_does_not_override_dash_key():
    # Dash form (strict) wins where both could match.
    assert parse_answer_key("1-AC 2 B") == {1: "AC", 2: "B"}


def test_table_key_grades_through_full_parse():
    text = ("1. Q one?\n(a) x\n(b) y\n2. Q two?\n(a) p\n(b) q\n"
            "Answers\n1 A\n2 B")
    qs = parse_questions(text)
    assert qs[0].correct == "A"
    assert qs[1].correct == "B"
    assert all(q.flags == [] for q in qs)


# ── Repeating header/footer furniture (multi-page) ─────────────────

def _line(text, page, top, bottom=None):
    return DocLine(text=text, page=page, bbox=[40, top, 400, bottom if bottom is not None else top + 12], fonts=set())


def test_repeated_footer_across_pages_is_stripped():
    # Footer at the bottom of both pages (high 'top' value) must not leak.
    lines = [
        _line("1. SI unit of force?", 0, 60),
        _line("(a) Joule", 0, 80),
        _line("(b) Newton", 0, 100),
        _line("Ans: B", 0, 120),
        _line("ALLEN PHYSICS - Page 1", 0, 760),     # footer p1
        _line("2. How many planets?", 1, 60),
        _line("ALLEN PHYSICS - Page 2", 1, 760),     # footer p2 (same, digit-normalised)
        _line("Answers", 1, 300),
        _line("2-8", 1, 320),
    ]
    qs = parse_questions("ignored when lines given", lines=lines)
    q2 = next(q for q in qs if q.question_type == "numeric")
    assert "ALLEN PHYSICS" not in q2.question
    assert q2.question.strip() == "How many planets?"


def test_repeated_midpage_option_is_not_stripped():
    # A common option repeated across pages but in the BODY zone must survive.
    lines = [
        _line("1. Q one?", 0, 200),
        _line("(a) None of the above", 0, 220),
        _line("(b) Something", 0, 240),
        _line("Ans: A", 0, 260),
        _line("2. Q two?", 1, 200),
        _line("(a) None of the above", 1, 220),      # repeated, but mid-page
        _line("(b) Other", 1, 240),
        _line("Ans: A", 1, 260),
    ]
    qs = parse_questions("x", lines=lines)
    assert qs[0].options["A"] == "None of the above"
    assert qs[1].options["A"] == "None of the above"


def test_standalone_page_marker_stripped_single_page():
    lines = [
        _line("1. Q one?", 0, 60),
        _line("(a) x", 0, 80),
        _line("(b) y", 0, 100),
        _line("Ans: A", 0, 120),
        _line("Page 1 of 1", 0, 140),
    ]
    qs = parse_questions("x", lines=lines)
    assert len(qs) == 1
    assert "Page 1" not in qs[0].question
