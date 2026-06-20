"""Teacher reassign integration test — REAL Postgres, no mocks.

Seeds one org, two teachers (A + B), an exam_config / student / violation
owned by teacher A, and an auth_events row for teacher A.  Calls
reassign_teaching_data(A → B) and asserts:
  • MOVE tables now have teacher_id = B.
  • KEEP tables (auth_events) still have the original teacher_id.

Requires DATABASE_URL (a real Postgres).  Skips cleanly otherwise.
"""

import os
import uuid

import pytest
import pytest_asyncio
import asyncpg

pytestmark = pytest.mark.asyncio


def _db_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


# ─── fixtures ───────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Shrink the asyncpg pool so the suite survives a small dev Postgres."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


@pytest_asyncio.fixture
async def seed():
    """Seed one org, two teachers, teaching data for teacher A, and one
    auth_events row.  Returns the dict of IDs for test assertions."""
    if not _db_url():
        pytest.skip("DATABASE_URL not set")

    conn = await asyncpg.connect(_db_url(), statement_cache_size=0)
    try:
        org_id = str(uuid.uuid4())
        teacher_a = str(uuid.uuid4())
        teacher_b = str(uuid.uuid4())
        exam_id = "exam-integration-reassign"
        student_roll = "INTEGRATION-REASSIGN-001"

        await conn.execute("""
            INSERT INTO organizations (id, name, slug, max_students)
            VALUES ($1, 'Reassign Integration Org', 'reassign-int', 100)
        """, org_id)

        await conn.execute("""
            INSERT INTO teachers (id, org_id, org_role, email, full_name)
            VALUES ($1, $3, 'teacher', 'teacher-a@test.com', 'Teacher A'),
                   ($2, $3, 'teacher', 'teacher-b@test.com', 'Teacher B')
        """, teacher_a, teacher_b, org_id)

        await conn.execute("""
            INSERT INTO exam_config (id, teacher_id, exam_id, exam_title, duration_minutes)
            VALUES ($1, $2, $3, 'Integration Reassign Exam', 60)
        """, str(uuid.uuid4()), teacher_a, exam_id)

        await conn.execute("""
            INSERT INTO students (roll_number, teacher_id, org_id)
            VALUES ($1, $2, $3)
        """, student_roll, teacher_a, org_id)

        await conn.execute("""
            INSERT INTO violations (id, teacher_id, session_key, violation_type)
            VALUES ($1, $2, 'int-session', 'integration_test')
        """, str(uuid.uuid4()), teacher_a)

        await conn.execute("""
            INSERT INTO auth_events (id, user_id, user_kind, email, event_type)
            VALUES ($1, $2, 'teacher', 'teacher-a@test.com', 'signup')
        """, str(uuid.uuid4()), teacher_a)

        return {
            "org_id": org_id,
            "from_id": teacher_a,
            "to_id": teacher_b,
            "exam_id": exam_id,
            "student_roll": student_roll,
        }
    finally:
        await conn.close()


# ─── tests ──────────────────────────────────────────────────────────

async def test_reassign_moves_teaching_data(seed):
    """After reassign, MOVE-table rows are owned by teacher B."""
    conn = await asyncpg.connect(_db_url(), statement_cache_size=0)
    try:
        from app.services.teacher_transfer import reassign_teaching_data

        # Run the remap inside a transaction.
        async with conn.transaction():
            counts = await reassign_teaching_data(conn, seed["from_id"], seed["to_id"])

        assert counts["exam_config"] >= 1
        assert counts["students"] >= 1
        assert counts["violations"] >= 1

        # exam_config rows now belong to teacher B.
        rows = await conn.fetch(
            "SELECT teacher_id FROM exam_config WHERE exam_id = $1", seed["exam_id"])
        assert rows, "exam_config row not found after reassign"
        for r in rows:
            assert str(r["teacher_id"]) == seed["to_id"], (
                f"Expected teacher_id={seed['to_id']}, got {r['teacher_id']}")

        # students row now belongs to teacher B.
        rows = await conn.fetch(
            "SELECT teacher_id FROM students WHERE roll_number = $1",
            seed["student_roll"])
        assert rows, "student row not found after reassign"
        for r in rows:
            assert str(r["teacher_id"]) == seed["to_id"]

        # violations row now belongs to teacher B (the one seeded above).
        rows = await conn.fetch(
            "SELECT teacher_id FROM violations WHERE session_key = 'int-session'")
        assert rows, "violations row not found after reassign"
        for r in rows:
            assert str(r["teacher_id"]) == seed["to_id"]
    finally:
        await conn.close()


async def test_reassign_keeps_identity_data(seed):
    """After reassign, auth_events still reference the original teacher."""
    conn = await asyncpg.connect(_db_url(), statement_cache_size=0)
    try:
        from app.services.teacher_transfer import reassign_teaching_data

        async with conn.transaction():
            await reassign_teaching_data(conn, seed["from_id"], seed["to_id"])

        # auth_events user_id must still be the original teacher.
        rows = await conn.fetch(
            "SELECT user_id FROM auth_events WHERE email = 'teacher-a@test.com'")
        assert rows, "auth_events row not found after reassign"
        for r in rows:
            assert str(r["user_id"]) == seed["from_id"], (
                f"auth_events user_id changed: expected {seed['from_id']}, got {r['user_id']}")
    finally:
        await conn.close()
