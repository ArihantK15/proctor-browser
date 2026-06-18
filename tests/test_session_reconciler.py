"""Fixtures for the session-state reconciler's re-enqueue path.

_enqueue_rescore is the heal action for drifted exam_sessions rows. The
one piece of real logic worth pinning is the roll_number fallback: when a
row has no roll_number, it's derived from the session_key prefix so the
re-scored result still attributes to the right student. Errors must be
swallowed (return False), never crash the reconcile loop.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.jobs as jobs
from app.services import session_reconciler as rec


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def _fake_enqueue(job, **kwargs):
        calls.append(kwargs)
        return "job-id"

    monkeypatch.setattr(jobs, "enqueue_job", _fake_enqueue)
    return calls


def test_uses_explicit_roll_number(captured):
    row = {"session_key": "2102508447_abc123", "teacher_id": "t1",
           "exam_id": "e1", "roll_number": "EXPLICIT99"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == "EXPLICIT99"


def test_derives_roll_from_session_key_when_missing(captured):
    row = {"session_key": "2102508447_abc123", "teacher_id": "t1", "exam_id": "e1"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == "2102508447"


def test_roll_empty_when_session_key_has_no_separator(captured):
    row = {"session_key": "nounderscorehere", "teacher_id": "t1"}
    assert rec._enqueue_rescore(row) is True
    assert captured[0]["roll_number"] == ""


def test_returns_false_and_swallows_enqueue_error(monkeypatch):
    def _boom(job, **kwargs):
        raise RuntimeError("queue down")
    monkeypatch.setattr(jobs, "enqueue_job", _boom)
    row = {"session_key": "r_1", "teacher_id": "t1"}
    assert rec._enqueue_rescore(row) is False  # must not raise


def test_enqueue_targets_scoring_queue(captured):
    row = {"session_key": "r_1", "teacher_id": "t1", "exam_id": "e1"}
    rec._enqueue_rescore(row)
    assert captured[0]["queue_name"] == "scoring"
