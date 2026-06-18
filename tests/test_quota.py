"""Fixtures for org-student quota error detection (services/quota.py).

is_quota_error must distinguish the org-student-quota trigger from other
CHECK-constraint failures that share SQLSTATE 23514 (enum checks, appeals
CHECKs). Misclassifying either way is bad: a false negative buckets a
quota overflow as "internal error"; a false positive tells a teacher
they hit their student cap when they didn't.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.quota import is_quota_error, QuotaExceededError


class _FakeAsyncpgError(Exception):
    def __init__(self, msg, sqlstate=None):
        super().__init__(msg)
        if sqlstate is not None:
            self.sqlstate = sqlstate


def test_matches_sqlstate_plus_message():
    e = _FakeAsyncpgError("Student quota exceeded for organization abc", sqlstate="23514")
    assert is_quota_error(e) is True


def test_matches_message_only_when_sqlstate_absent():
    # psycopg/supabase-py may not expose sqlstate cleanly.
    e = Exception("ERROR: Student quota exceeded for organization xyz")
    assert is_quota_error(e) is True


def test_other_check_constraint_same_sqlstate_is_not_quota():
    e = _FakeAsyncpgError("new row violates check constraint appeals_status_chk", sqlstate="23514")
    assert is_quota_error(e) is False


def test_unrelated_error_is_not_quota():
    assert is_quota_error(ValueError("boom")) is False
    assert is_quota_error(Exception("connection refused")) is False


def test_quota_exceeded_error_carries_original():
    orig = _FakeAsyncpgError("Student quota exceeded for organization abc", sqlstate="23514")
    wrapped = QuotaExceededError(orig)
    assert wrapped.original is orig
    assert "quota exceeded" in str(wrapped).lower()
    wrapped2 = QuotaExceededError(orig, "Custom message")
    assert str(wrapped2) == "Custom message"
