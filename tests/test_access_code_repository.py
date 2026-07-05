"""Tests for app/repositories/questions.py's access-code resolution.

get_access_code() has two failure-mode branches that must fail CLOSED
(raise) rather than fabricate a code that was never actually persisted —
otherwise a caller (teacher dashboard display, or _validate_access_code
comparing a student's typed code) ends up trusting a value that will
never match what's really on file. See commit bcc07a80 for the read-
failure branch's own reasoning; the persist-failure branch below fixes
the same class of bug one step further in.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories import questions as repo

pytestmark = pytest.mark.asyncio


async def test_no_code_on_file_generates_and_persists():
    """Happy path: no access_code in exam_config yet -> a fresh code is
    generated AND actually persisted before being returned."""
    with patch.object(repo, "load_exam_config", new=AsyncMock(return_value={"access_code": ""})), \
         patch.object(repo, "set_access_code", new=AsyncMock()) as mock_set:
        code = await repo.get_access_code(teacher_id="t1", exam_id="e1")

    assert code and len(code) == repo._ACCESS_CODE_LENGTH
    mock_set.assert_awaited_once()
    # The persisted code must be the SAME one returned to the caller.
    assert mock_set.call_args.args[0] == code


async def test_existing_code_returned_without_persisting_again():
    with patch.object(repo, "load_exam_config", new=AsyncMock(return_value={"access_code": "AB12CD"})), \
         patch.object(repo, "set_access_code", new=AsyncMock()) as mock_set:
        code = await repo.get_access_code(teacher_id="t1", exam_id="e1")

    assert code == "AB12CD"
    mock_set.assert_not_awaited()


async def test_read_failure_raises_instead_of_fabricating():
    """Already-fixed behavior (bcc07a80) — locking this in as a regression
    guard alongside the persist-failure test below."""
    with patch.object(repo, "load_exam_config", new=AsyncMock(side_effect=RuntimeError("db down"))):
        with pytest.raises(RuntimeError):
            await repo.get_access_code(teacher_id="t1", exam_id="e1")


async def test_persist_failure_raises_instead_of_returning_unpersisted_code():
    """The real bug this pass found and fixed: when no code exists yet and
    the auto-generated one fails to PERSIST, get_access_code() must raise —
    not silently return the fabricated code as if it were saved. Returning
    it would mean _validate_access_code (exam.py), which re-calls this
    function fresh on every single student validation attempt, generates a
    NEW throwaway code every time and never matches anything a student
    could possibly type — permanently locking out every student for this
    exam until the underlying DB issue clears, with no visible error."""
    with patch.object(repo, "load_exam_config", new=AsyncMock(return_value={"access_code": ""})), \
         patch.object(repo, "set_access_code", new=AsyncMock(side_effect=RuntimeError("write failed"))):
        with pytest.raises(RuntimeError):
            await repo.get_access_code(teacher_id="t1", exam_id="e1")


async def test_env_var_fallback_when_no_teacher_or_exam_id(monkeypatch):
    """Single-tenant fallback mode has no key to persist against — best-
    effort only, never raises."""
    monkeypatch.setenv("EXAM_ACCESS_CODE", "")
    code = await repo.get_access_code(teacher_id=None, exam_id=None)
    assert code and len(code) == repo._ACCESS_CODE_LENGTH
