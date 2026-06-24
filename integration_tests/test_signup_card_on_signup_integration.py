"""Card-on-signup status path — against REAL Postgres (regression guard).

This is the test that would have caught the 4-day signup outage. The
card-on-signup unit tests use a MagicMock DB, so they never executed the real
INSERT against the real subscriptions status CHECK constraint: prod had the old
`subscriptions_status_check` (which rejected 'created') still in place, so every
teacher signup 500'd while CI stayed green.

Here we run the production signup transaction against a live DB whose fixture now
carries the SAME consolidated constraint as prod (phase144, applied in conftest),
and assert:

  • CARD_ON_SIGNUP_ENFORCED=on  → subscription persists with status='created'
  • CARD_ON_SIGNUP_ENFORCED=off → subscription persists with status='trialing'
  • the constraint ACCEPTS every status the app/Razorpay code can write
  • the constraint REJECTS an unknown status (proves it is actually enforced —
    otherwise the positive assertions would be vacuous and we'd be blind again)

Requires DATABASE_URL (a real Postgres). Skips cleanly otherwise — see
integration_tests/conftest.py.
"""
import uuid

import pytest
import pytest_asyncio

from app.database import async_table
from app.routers import auth

pytestmark = pytest.mark.asyncio

# Every status the application + Razorpay webhooks can write — must stay in sync
# with migrations/phase144_subscriptions_status_fix.sql. If code starts writing a
# new status, add it here AND to phase144, or prod signup/billing will 500.
_CODE_WRITABLE_STATUSES = [
    "created", "authenticated", "active", "trialing", "pending",
    "past_due", "grace", "halted", "paused",
    "cancelling", "cancelled", "completed", "expired",
]


@pytest_asyncio.fixture(autouse=True)
async def _small_pool(monkeypatch):
    """Shrink the asyncpg pool so the suite survives a small dev Postgres."""
    from app.postgres_table import close_pool
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "10")
    await close_pool()
    yield
    await close_pool()


async def _signup():
    """Drive the real transactional signup path; return (teacher, org_id)."""
    uniq = uuid.uuid4().hex[:8]
    teacher, org_id, _exam = await auth._create_teacher_signup_postgres_tx(
        email=f"{uniq}@x.test",
        name="Card Signup",
        org_name=f"Org {uniq}",
        slug=f"org-{uniq}",
        supabase_uid=str(uuid.uuid4()),
        password_hash="hash",
    )
    return teacher, org_id


async def _sub_status(org_id) -> str | None:
    rows = (await async_table("subscriptions").select("status")
            .eq("org_id", str(org_id)).limit(1).execute()).data or []
    return rows[0]["status"] if rows else None


async def test_card_on_signup_persists_created_status(monkeypatch):
    """The exact path that broke prod: flag ON inserts status='created'."""
    monkeypatch.setattr("app.constants.CARD_ON_SIGNUP_ENFORCED", True)
    _teacher, org_id = await _signup()
    assert await _sub_status(org_id) == "created"


async def test_flag_off_persists_trialing_status(monkeypatch):
    """Flag OFF keeps the legacy free-trial status."""
    monkeypatch.setattr("app.constants.CARD_ON_SIGNUP_ENFORCED", False)
    _teacher, org_id = await _signup()
    assert await _sub_status(org_id) == "trialing"


@pytest.mark.parametrize("status", _CODE_WRITABLE_STATUSES)
async def test_constraint_accepts_every_code_writable_status(monkeypatch, status):
    """Every status the code can emit must be accepted by the live constraint.

    Drives a real signup (creates the org+subscription), then transitions the
    subscription through `status`. A CHECK that rejects any of these is the
    outage class — this fails loudly if the constraint and the code drift apart.
    """
    monkeypatch.setattr("app.constants.CARD_ON_SIGNUP_ENFORCED", False)
    _teacher, org_id = await _signup()
    await async_table("subscriptions").update({"status": status}).eq(
        "org_id", str(org_id)).execute()
    assert await _sub_status(org_id) == status


async def test_constraint_rejects_unknown_status(monkeypatch):
    """Sanity: the constraint is actually ENFORCED in the fixture.

    Without this, if phase144 silently failed to apply the positive tests would
    pass vacuously (no constraint = everything accepted) and the suite would be
    blind again — the precise failure mode that shipped the outage.
    """
    monkeypatch.setattr("app.constants.CARD_ON_SIGNUP_ENFORCED", False)
    _teacher, org_id = await _signup()
    with pytest.raises(Exception) as ei:
        await async_table("subscriptions").update(
            {"status": "definitely-not-a-real-status"}).eq(
            "org_id", str(org_id)).execute()
    assert "subscriptions_status_check" in str(ei.value) or "check constraint" in str(ei.value).lower()
