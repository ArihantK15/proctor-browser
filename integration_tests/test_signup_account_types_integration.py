"""Solo vs org signup shapes — against REAL Postgres.

Exercises the production signup transaction (_create_teacher_signup_postgres_tx)
end-to-end against a live DB, asserting the role/owner/subscription wiring that
a MagicMock can't model:

  • solo → teachers.org_role='teacher' AND organizations.owner_teacher_id = teacher.id
  • org  → teachers.org_role='admin'   AND organizations.owner_teacher_id = teacher.id
  • both → exactly one subscriptions row for the org

The owner_teacher_id column comes from phase135 (already baked into
integration_tests/schema.sql, Task 1), so this validates the real follow-up
UPDATE inside the same transaction.

Requires DATABASE_URL (a real Postgres). Skips cleanly otherwise — see
integration_tests/conftest.py.
"""
import uuid

import pytest
import pytest_asyncio

from app.database import async_table
from app.routers import auth

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Shrink the asyncpg pool so the suite survives a small dev Postgres."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _signup(account_type: str):
    """Drive the real transactional signup path and return (teacher, org_id)."""
    uniq = uuid.uuid4().hex[:8]
    teacher, org_id, _exam = await auth._create_teacher_signup_postgres_tx(
        email=f"{uniq}@x.test",
        name="Sign Up",
        org_name=f"Org {uniq}",
        slug=f"org-{uniq}",
        supabase_uid=str(uuid.uuid4()),
        password_hash="hash",
        account_type=account_type,
    )
    return teacher, org_id


async def _org_owner(org_id) -> str | None:
    rows = (await async_table("organizations").select("owner_teacher_id")
            .eq("id", str(org_id)).limit(1).execute()).data or []
    owner = rows[0]["owner_teacher_id"] if rows else None
    return str(owner) if owner else None


async def _sub_count(org_id) -> int:
    rows = (await async_table("subscriptions").select("id")
            .eq("org_id", str(org_id)).execute()).data or []
    return len(rows)


async def test_solo_signup_shape():
    teacher, org_id = await _signup("solo")
    assert teacher["org_role"] == "teacher"
    assert await _org_owner(org_id) == str(teacher["id"])
    assert await _sub_count(org_id) == 1


async def test_org_signup_shape():
    teacher, org_id = await _signup("org")
    assert teacher["org_role"] == "admin"
    assert await _org_owner(org_id) == str(teacher["id"])
    assert await _sub_count(org_id) == 1


async def test_signup_status_is_pending_verification():
    """Regression guard: status has no DB default, so every insert path
    must set it explicitly or new accounts silently skip email
    verification on first login (see auth.py teacher_login's
    legacy-account auto-verify branch)."""
    teacher, _org_id = await _signup("solo")
    assert teacher["status"] == "pending_verification"


async def test_default_account_type_is_solo():
    """Omitting account_type yields the solo shape (back-compat)."""
    uniq = uuid.uuid4().hex[:8]
    teacher, org_id, _ = await auth._create_teacher_signup_postgres_tx(
        email=f"{uniq}@x.test", name="D", org_name=f"Org {uniq}",
        slug=f"org-{uniq}", supabase_uid=str(uuid.uuid4()), password_hash="h",
    )
    assert teacher["org_role"] == "teacher"
    assert await _org_owner(org_id) == str(teacher["id"])
