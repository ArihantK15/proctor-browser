"""Fixtures for the deterministic option/question shuffle in scoring.py.

The shuffle is the riskiest part of scoring: students see options under
relabelled positions, and their pick is translated back to the original
key before ``answers_match`` runs. One inversion error mis-scores every
shuffled exam silently. These tests pin the invariants:

  • determinism — same (session, teacher) seed → identical shuffle, so
    the view shown at exam time can be reconstructed at grade time;
  • text consistency — the text shown under a displayed label equals the
    original option's text that the label_map points back to;
  • round-trip scoring — selecting the displayed label of the correct
    option scores correct after translation;
  • True/False questions are never option-shuffled.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring import (  # noqa: E402
    build_shuffle_view, shuffle_seed, get_shuffle_flags, answers_match,
)


def _q(qid="q1", qtype="mcq_single", opts=None):
    return {"id": qid, "question_type": qtype,
            "options": opts or {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"},
            "correct": "C"}


def test_shuffle_seed_is_deterministic():
    assert shuffle_seed("sess", "t1") == shuffle_seed("sess", "t1")


def test_shuffle_seed_varies_by_session_and_teacher():
    assert shuffle_seed("sess", "t1") != shuffle_seed("sess", "t2")
    assert shuffle_seed("sessA", "t1") != shuffle_seed("sessB", "t1")


def test_build_shuffle_view_is_deterministic():
    a_qs, a_map = build_shuffle_view([_q()], "s", "t1", shuffle_q=False, shuffle_o=True)
    b_qs, b_map = build_shuffle_view([_q()], "s", "t1", shuffle_q=False, shuffle_o=True)
    assert a_map == b_map
    assert a_qs[0]["options"] == b_qs[0]["options"]


def test_displayed_text_matches_label_map_origin():
    """For every displayed label d → original key o in the map, the text
    shown under d must equal the original option text of o."""
    orig = _q()["options"]
    view, lmaps = build_shuffle_view([_q()], "s", "t1", shuffle_q=False, shuffle_o=True)
    shown = view[0]["options"]
    label_map = lmaps["q1"]
    for displayed, origin in label_map.items():
        assert shown[displayed] == orig[origin]


def test_correct_answer_round_trips_to_score():
    """A student who clicks the displayed label showing the correct text
    must score correct once the label is translated back."""
    q = _q()  # correct = "C" (gamma)
    view, lmaps = build_shuffle_view([q], "s", "t1", shuffle_q=False, shuffle_o=True)
    label_map = lmaps["q1"]
    # Find which displayed label is currently showing the correct option.
    displayed_for_correct = next(d for d, o in label_map.items() if o == q["correct"])
    # Translate the student's displayed pick back to the original key.
    translated = label_map[displayed_for_correct]
    assert answers_match(translated, q["correct"]) is True


def test_wrong_answer_round_trips_to_incorrect():
    q = _q()  # correct = "C"
    view, lmaps = build_shuffle_view([q], "s", "t1", shuffle_q=False, shuffle_o=True)
    label_map = lmaps["q1"]
    displayed_for_wrong = next(d for d, o in label_map.items() if o == "A")  # alpha, wrong
    translated = label_map[displayed_for_wrong]
    assert answers_match(translated, q["correct"]) is False


def test_options_not_shuffled_when_flag_off():
    view, lmaps = build_shuffle_view([_q()], "s", "t1", shuffle_q=False, shuffle_o=False)
    assert view[0]["options"] == _q()["options"]
    assert lmaps["q1"] == {"A": "A", "B": "B", "C": "C", "D": "D"}


def test_true_false_never_option_shuffled():
    q = _q(qtype="true_false", opts={"True": "True", "False": "False"})
    view, lmaps = build_shuffle_view([q], "s", "t1", shuffle_q=False, shuffle_o=True)
    assert view[0]["options"] == {"True": "True", "False": "False"}
    assert lmaps["q1"] == {"True": "True", "False": "False"}


def test_tf_value_options_never_shuffled_even_if_type_mislabelled():
    """Options that ARE {True, False} are identity-mapped regardless of the
    declared question_type — guards against a mistyped true/false item."""
    q = _q(qtype="mcq_single", opts={"True": "True", "False": "False"})
    view, lmaps = build_shuffle_view([q], "s", "t1", shuffle_q=False, shuffle_o=True)
    assert lmaps["q1"] == {"True": "True", "False": "False"}


def test_question_order_shuffle_preserves_set():
    qs = [_q("q1"), _q("q2"), _q("q3"), _q("q4")]
    view, _ = build_shuffle_view(qs, "s", "t1", shuffle_q=True, shuffle_o=False)
    assert {v["id"] for v in view} == {"q1", "q2", "q3", "q4"}
    assert len(view) == 4


def test_get_shuffle_flags_defaults_to_true():
    assert get_shuffle_flags({}) == (True, True)
    assert get_shuffle_flags({"shuffle_questions": False, "shuffle_options": False}) == (False, False)
    assert get_shuffle_flags({"shuffle_questions": None}) == (True, True)
