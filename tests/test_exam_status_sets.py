"""Fixtures for the session-status classification sets (models/exam.py).

These frozensets are the single source of truth every view + the reconciler
import so status lists can't drift apart (the bug class where a session
showed in Live but vanished from Results). The semantic invariants below
are load-bearing — especially that SUBMITTED is TERMINAL but NOT a RESULT
status, which is exactly why the reconciler re-scores stuck SUBMITTED rows.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.exam import (
    SessionStatus, LIVE_STATUSES, RESULT_STATUSES, TERMINAL_STATUSES,
    RECOVERABLE_STATUSES,
)


def test_all_sets_contain_only_valid_statuses():
    valid = set(SessionStatus)
    for s in (LIVE_STATUSES, RESULT_STATUSES, TERMINAL_STATUSES, RECOVERABLE_STATUSES):
        assert s <= valid


def test_live_and_terminal_are_disjoint():
    assert LIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)


def test_results_are_a_subset_of_terminal():
    assert RESULT_STATUSES <= TERMINAL_STATUSES


def test_recoverable_are_terminal_but_not_results():
    assert RECOVERABLE_STATUSES <= TERMINAL_STATUSES
    assert RECOVERABLE_STATUSES.isdisjoint(RESULT_STATUSES)


def test_submitted_is_terminal_but_not_a_result():
    # The reconciler relies on this: SUBMITTED is an end-of-attempt state
    # (terminal) that still needs scoring, so it must NOT count as a RESULT.
    assert SessionStatus.SUBMITTED in TERMINAL_STATUSES
    assert SessionStatus.SUBMITTED not in RESULT_STATUSES


def test_completed_is_a_result():
    assert SessionStatus.COMPLETED in RESULT_STATUSES
    assert SessionStatus.COMPLETED in TERMINAL_STATUSES


def test_in_progress_and_paused_are_live_not_terminal():
    assert SessionStatus.IN_PROGRESS in LIVE_STATUSES
    assert SessionStatus.PAUSED in LIVE_STATUSES
    assert SessionStatus.PAUSED not in TERMINAL_STATUSES
