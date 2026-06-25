"""Integration test: phase146 widens questions.question_id  integer → text.

Reproduces the production outage (Sentry PYTHON-1J / 1K / 1M / 1N / 1P):
prod baseline declared `questions.question_id INTEGER`, but coding authoring
mints string labels (`coding-<uuid>`) and the asyncpg backend will not coerce
str→int — so EVERY coding-question write 500'd. The mocked unit suite never
caught it, and the integration fixture's schema.sql had already drifted to
TEXT, so even the e2e coding test passed against a schema prod didn't have.

Against a REAL Postgres this asserts:
  1. an INTEGER question_id column REJECTS a coding label (the prod failure),
  2. the phase146 ALTER converts integer → text losslessly,
  3. post-migration BOTH numeric MCQ ordinals and coding labels insert/select,
  4. the real migration FILE runs cleanly (idempotent) on a text column.

Lives outside tests/ (see integration_tests/conftest.py) — needs a real
Postgres; skipped when DATABASE_URL is unset.
"""
import os
import pathlib

import asyncpg
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

_HERE = pathlib.Path(__file__).parent
_PHASE146 = _HERE.parent / "migrations" / "phase146_questions_question_id_text.sql"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


@pytest.fixture(autouse=True)
def _skip_without_db():
    if not _database_url():
        pytest.skip("integration tests require DATABASE_URL (a real Postgres)")


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(_database_url(), statement_cache_size=0)
    # Isolated scratch table mirroring the PRE-phase146 prod shape.
    await c.execute("DROP TABLE IF EXISTS phase146_qtest")
    await c.execute(
        """
        CREATE TABLE phase146_qtest (
            id          bigserial PRIMARY KEY,
            teacher_id  uuid,
            exam_id     uuid,
            question_id integer NOT NULL,
            UNIQUE (teacher_id, exam_id, question_id)
        )
        """
    )
    yield c
    await c.execute("DROP TABLE IF EXISTS phase146_qtest")
    await c.close()


_TID = "00000000-0000-0000-0000-0000000000aa"
_EID = "00000000-0000-0000-0000-0000000000bb"


async def test_integer_column_rejects_coding_label(conn):
    """The prod failure, reproduced: a string label into an int column errors."""
    # A numeric ordinal binds fine (this is why MCQ always worked)...
    await conn.execute(
        "INSERT INTO phase146_qtest (teacher_id, exam_id, question_id) VALUES ($1,$2,$3)",
        _TID, _EID, 1,
    )
    # ...but the coding label cannot — exactly the asyncpg DataError prod hit.
    with pytest.raises(asyncpg.exceptions.DataError):
        await conn.execute(
            "INSERT INTO phase146_qtest (teacher_id, exam_id, question_id) VALUES ($1,$2,$3)",
            _TID, _EID, "coding-7bcd2224d259",
        )


async def test_phase146_converts_and_accepts_both(conn):
    """ALTER int→text is lossless and then accepts ordinals AND coding labels."""
    await conn.execute(
        "INSERT INTO phase146_qtest (teacher_id, exam_id, question_id) VALUES ($1,$2,$3)",
        _TID, _EID, 7,
    )

    # The exact DDL phase146 ships, retargeted to the scratch table.
    await conn.execute(
        "ALTER TABLE phase146_qtest "
        "ALTER COLUMN question_id TYPE text USING question_id::text"
    )

    coltype = await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'phase146_qtest' AND column_name = 'question_id'"
    )
    assert coltype == "text"

    # Existing ordinal preserved as its text form.
    assert await conn.fetchval(
        "SELECT question_id FROM phase146_qtest WHERE id = (SELECT min(id) FROM phase146_qtest)"
    ) == "7"

    # Both an MCQ ordinal and a coding label now insert (different exam_id to
    # avoid the unique-constraint clash with the seeded "7").
    await conn.execute(
        "INSERT INTO phase146_qtest (teacher_id, exam_id, question_id) VALUES ($1,$2,$3)",
        _TID, _EID, "8",
    )
    await conn.execute(
        "INSERT INTO phase146_qtest (teacher_id, exam_id, question_id) VALUES ($1,$2,$3)",
        _TID, _EID, "coding-7bcd2224d259",
    )
    labels = await conn.fetch("SELECT question_id FROM phase146_qtest ORDER BY id")
    assert {r["question_id"] for r in labels} == {"7", "8", "coding-7bcd2224d259"}


async def test_real_migration_file_runs_idempotently(conn):
    """The shipped phase146 file targets public.questions; running it against a
    text column (the post-cutover state) must be a clean no-op, proving the SQL
    is valid and re-runnable."""
    await conn.execute(
        "ALTER TABLE phase146_qtest "
        "ALTER COLUMN question_id TYPE text USING question_id::text"
    )
    sql = _PHASE146.read_text()
    # Retarget the one DDL statement to the scratch table and run it twice.
    scoped = sql.replace("public.questions", "phase146_qtest")
    await conn.execute(scoped)
    await conn.execute(scoped)  # idempotent: text→text via ::text cast
    coltype = await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'phase146_qtest' AND column_name = 'question_id'"
    )
    assert coltype == "text"
