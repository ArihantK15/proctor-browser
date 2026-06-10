"""Tenant-isolation scope spine — against REAL Postgres.

The tenancy refactor (scope_to_teacher_ids / assert_session_accessible) is a
security boundary; this proves cross-tenant rows are actually filtered out by
real SQL, not just by a mock returning whatever the test fed it.
"""
import pytest
from fastapi import HTTPException

from app.database import async_table
from app.auth.scope import scope_to_teacher_ids, assert_session_accessible

pytestmark = pytest.mark.asyncio


async def _org_with_teachers(n: int) -> tuple[str, list[str]]:
    oid = str((await async_table("organizations").insert({"name": "Org"}).execute()).data[0]["id"])
    tids = []
    for i in range(n):
        t = (await async_table("teachers").insert({
            "org_id": oid,
            "org_role": "admin" if i == 0 else "teacher",
            "email": f"t{i}-{oid[:6]}@x", "full_name": f"T{i}",
        }).execute()).data[0]
        tids.append(str(t["id"]))
    return oid, tids


async def test_scope_to_teacher_ids_is_org_wide_and_isolated():
    oid_a, tids_a = await _org_with_teachers(3)
    oid_b, tids_b = await _org_with_teachers(2)
    got = await scope_to_teacher_ids({"role": "admin", "teacher_id": None, "org_id": oid_a})
    assert set(got) == set(tids_a)                 # every teacher in the org
    assert not (set(got) & set(tids_b))            # and none from the other org


async def test_scope_single_teacher_locked_to_self():
    got = await scope_to_teacher_ids({"role": "teacher", "teacher_id": "teacher-1", "org_id": None})
    assert got == ["teacher-1"]


async def test_scope_superadmin_unfiltered():
    assert await scope_to_teacher_ids({"role": "superadmin", "teacher_id": None, "org_id": None}) is None


async def test_assert_session_accessible_admin_in_org_vs_cross_tenant():
    oid_a, tids_a = await _org_with_teachers(2)   # tids_a[0]=admin, tids_a[1]=teacher
    oid_b, _ = await _org_with_teachers(1)
    await async_table("exam_sessions").insert({
        "session_key": "ROLL01_sess", "teacher_id": tids_a[1], "status": "in_progress",
        "roll_number": "ROLL01", "full_name": "Stu",
    }).execute()

    # Admin of the owning org reaches a co-teacher's session.
    sess = await assert_session_accessible(
        "ROLL01_sess", {"role": "admin", "teacher_id": None, "org_id": oid_a})
    assert str(sess["teacher_id"]) == tids_a[1]

    # Admin of a different org gets 404 (existence not leaked).
    with pytest.raises(HTTPException) as ei:
        await assert_session_accessible(
            "ROLL01_sess", {"role": "admin", "teacher_id": None, "org_id": oid_b})
    assert ei.value.status_code == 404


async def test_assert_session_accessible_teacher_locked_to_own():
    oid_a, tids_a = await _org_with_teachers(2)
    await async_table("exam_sessions").insert({
        "session_key": "ROLL02_sess", "teacher_id": tids_a[1], "status": "in_progress",
        "roll_number": "ROLL02", "full_name": "Stu",
    }).execute()
    # Owner reaches it.
    sess = await assert_session_accessible(
        "ROLL02_sess", {"role": "teacher", "teacher_id": tids_a[1], "org_id": oid_a})
    assert sess["session_key"] == "ROLL02_sess"
    # A different teacher (even same org) is locked out → 404.
    with pytest.raises(HTTPException) as ei:
        await assert_session_accessible(
            "ROLL02_sess", {"role": "teacher", "teacher_id": tids_a[0], "org_id": oid_a})
    assert ei.value.status_code == 404
