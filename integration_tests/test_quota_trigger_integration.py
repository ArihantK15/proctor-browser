"""Per-org student-quota trigger — against REAL Postgres.

This is the DB-level belt-and-suspenders that enforces the paid seat limit
(Starter 30 / Growth 150 / Pro 500 / Enterprise unlimited): a BEFORE INSERT
trigger on `students` that rejects a row which would push the org past
organizations.max_students. phase90 created it; phase91 made it race-free with a
per-org advisory lock.

Nobody had tested it. It is impossible to test without a real Postgres — the
whole point is the trigger DDL and the advisory-lock serialization, neither of
which a MagicMock has. The harness applies the actual phase90+phase91 migrations
(see conftest.py), so this validates the production trigger, not a copy.
"""
import asyncio
import uuid

import pytest
import pytest_asyncio

from app.database import async_table

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Tiny asyncpg pool so the concurrency test can't exhaust a dev Postgres."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _org(cap: int) -> str:
    oid = str(uuid.uuid4())
    await async_table("organizations").insert({
        "id": oid, "name": f"Org {oid[:8]}", "max_students": cap,
    }).execute()
    return oid


async def _teacher(org_id: str | None) -> str:
    tid = str(uuid.uuid4())
    row = {"id": tid, "org_role": "admin", "email": f"{tid[:8]}@x.test"}
    if org_id is not None:
        row["org_id"] = org_id
    await async_table("teachers").insert(row).execute()
    return tid


async def _add_student(teacher_id: str, roll: str) -> None:
    await async_table("students").insert({
        "roll_number": roll, "full_name": roll, "teacher_id": teacher_id,
        "email": f"{roll}@x.test",
    }).execute()


async def _count(org_id: str) -> int:
    # Count via the same join the trigger uses.
    from app.postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM students s JOIN teachers t ON t.id = s.teacher_id "
            "WHERE t.org_id = $1::uuid", org_id)


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "quota exceeded" in msg or getattr(exc, "sqlstate", "") == "23514"


async def test_inserts_up_to_cap_then_reject():
    org = await _org(3)
    teacher = await _teacher(org)
    for i in range(3):
        await _add_student(teacher, f"R{i}")
    assert await _count(org) == 3

    # The 4th student would exceed cap=3 → trigger rejects it.
    with pytest.raises(Exception) as exc:
        await _add_student(teacher, "R3")
    assert _is_quota_error(exc.value)
    assert await _count(org) == 3  # row was not written


async def test_org_quota_is_shared_across_teachers():
    # The cap is per-ORG, counted across every teacher in the org.
    org = await _org(3)
    t_a = await _teacher(org)
    t_b = await _teacher(org)
    await _add_student(t_a, "A1")
    await _add_student(t_a, "A2")
    await _add_student(t_b, "B1")          # org now at 3
    assert await _count(org) == 3

    with pytest.raises(Exception) as exc:   # teacher B's 4th org-wide student
        await _add_student(t_b, "B2")
    assert _is_quota_error(exc.value)


async def test_concurrent_inserts_never_exceed_cap():
    # The phase91 fix: an advisory lock per org serializes the count+insert so
    # concurrent admins can't both slip past the boundary. Fire more inserts
    # than the cap, all at once; exactly `cap` must win and the org must land
    # at exactly `cap` — never cap+1.
    cap = 5
    org = await _org(cap)
    teacher = await _teacher(org)

    sem = asyncio.Semaphore(6)

    async def _try(i):
        async with sem:
            try:
                await _add_student(teacher, f"C{i}")
                return True
            except Exception as e:
                assert _is_quota_error(e)   # the only acceptable failure
                return False

    results = await asyncio.gather(*[_try(i) for i in range(cap + 10)])
    granted = sum(results)
    assert granted == cap
    assert await _count(org) == cap


async def test_null_org_teacher_skips_quota():
    # A teacher not assigned to an org has no cap to enforce — inserts succeed
    # past any number (the trigger returns NEW early).
    teacher = await _teacher(None)
    for i in range(40):
        await _add_student(teacher, f"N{i}")
    from app.postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM students WHERE teacher_id = $1::uuid", teacher)
    assert n == 40


async def test_enterprise_cap_is_effectively_unlimited():
    org = await _org(999999)          # enterprise sentinel
    teacher = await _teacher(org)
    for i in range(50):
        await _add_student(teacher, f"E{i}")
    assert await _count(org) == 50
