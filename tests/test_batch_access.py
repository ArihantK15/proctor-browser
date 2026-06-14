"""Exam access gate (gap #59) — check_group_access: group OR batch, fail-closed.

The endpoints only ever mock this gate, so the actual access logic (who may
enter a restricted exam) was untested. It's security-critical, so cover it
directly: default-open, group axis, batch axis, both axes, and default-deny.
"""
from unittest.mock import MagicMock, patch

import pytest


def _chain(data):
    m = MagicMock()
    for attr in ("select", "eq", "in_", "limit"):
        getattr(m, attr).return_value = m

    async def _execute():
        r = MagicMock()
        r.data = data
        return r

    m.execute = _execute
    return m


def _atable_side_effect(data_map):
    def se(table):
        return _chain(data_map.get(table, []))
    return se


async def _gate(data_map, roll="R1", tid="t1", exam="e1"):
    with patch("app.repositories.sessions._atable", side_effect=_atable_side_effect(data_map)):
        from app.repositories.sessions import check_group_access
        return await check_group_access(roll, tid, exam)


@pytest.mark.asyncio
async def test_open_when_no_assignments():
    assert await _gate({"exam_group_assignments": [], "exam_batch_assignments": []}) is True


@pytest.mark.asyncio
async def test_group_member_allowed():
    assert await _gate({
        "exam_group_assignments": [{"group_id": "g1"}],
        "exam_batch_assignments": [],
        "student_group_members": [{"id": "m1"}],
    }) is True


@pytest.mark.asyncio
async def test_group_non_member_denied():
    assert await _gate({
        "exam_group_assignments": [{"group_id": "g1"}],
        "exam_batch_assignments": [],
        "student_group_members": [],
    }) is False


@pytest.mark.asyncio
async def test_batch_match_allowed():
    assert await _gate({
        "exam_group_assignments": [],
        "exam_batch_assignments": [{"batch": "2024"}],
        "students": [{"batch": "2024"}],
    }) is True


@pytest.mark.asyncio
async def test_batch_mismatch_denied():
    assert await _gate({
        "exam_group_assignments": [],
        "exam_batch_assignments": [{"batch": "2024"}],
        "students": [{"batch": "2025"}],
    }) is False


@pytest.mark.asyncio
async def test_no_student_batch_denied():
    assert await _gate({
        "exam_group_assignments": [],
        "exam_batch_assignments": [{"batch": "2024"}],
        "students": [{"batch": None}],
    }) is False


@pytest.mark.asyncio
async def test_both_axes_group_member_wins():
    # member of an assigned group even though batch doesn't match → allowed
    assert await _gate({
        "exam_group_assignments": [{"group_id": "g1"}],
        "exam_batch_assignments": [{"batch": "2024"}],
        "student_group_members": [{"id": "m1"}],
        "students": [{"batch": "2025"}],
    }) is True


@pytest.mark.asyncio
async def test_both_axes_batch_match_wins():
    # not a group member, but batch matches → allowed
    assert await _gate({
        "exam_group_assignments": [{"group_id": "g1"}],
        "exam_batch_assignments": [{"batch": "2024"}],
        "student_group_members": [],
        "students": [{"batch": "2024"}],
    }) is True


@pytest.mark.asyncio
async def test_batch_match_is_case_and_whitespace_insensitive():
    # assignment "CS-2024" vs student "cs-2024 " must still grant access.
    assert await _gate({
        "exam_group_assignments": [],
        "exam_batch_assignments": [{"batch": "CS-2024"}],
        "students": [{"batch": "cs-2024 "}],
    }) is True


@pytest.mark.asyncio
async def test_both_axes_neither_denied():
    assert await _gate({
        "exam_group_assignments": [{"group_id": "g1"}],
        "exam_batch_assignments": [{"batch": "2024"}],
        "student_group_members": [],
        "students": [{"batch": "2025"}],
    }) is False
