"""Invite sending helpers: token generation, daily cap enforcement."""

import asyncio
import os
import secrets
from datetime import date, datetime, timezone

from .database import async_table as _atable
from .constants import INVITE_DAILY_CAP
from .logger import get_logger

_dep_log = get_logger("invites")


def _get_invite_base_url() -> str:
    return os.environ.get("INVITE_BASE_URL", "").rstrip("/") or "https://app.procta.net"


def _new_invite_token() -> str:
    return secrets.token_urlsafe(32)


def _new_access_code(length: int = 6) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _claim_and_bump_cap(teacher_id: str, batch_size: int) -> tuple[bool, int]:
    """Atomic check-and-bump for invite daily cap.

    Calls the `claim_invite_cap` Postgres RPC, which folds the
    check + increment into a single conditional UPDATE under a row
    lock. Two concurrent callers serialise behind the lock and the
    one that would overshoot the cap is denied — no read-then-write
    race window.

    The RPC returns the remaining quota after a successful claim,
    or -1 to signal "denied, would overshoot."

    Falls back to the legacy read-then-write path if the RPC isn't
    deployed yet (PGRST202 / "function does not exist"). The fallback
    is racy by design — that's the bug the migration fixes — so the
    log is loud enough to make the missing migration obvious.
    """
    if batch_size <= 0:
        return (True, INVITE_DAILY_CAP)
    # Skip cap when the emailer backend is noop (RESEND_API_KEY not
    # configured OR EMAIL_PROVIDER=noop). Without this, every dry-run
    # without a real Resend key would consume cap quota and eventually
    # block legitimate sends with a confusing "Daily cap exceeded"
    # while Resend's own dashboard shows 0 — exactly the symptom the
    # user hit during demo prep. Always allow when there's no actual
    # send happening; the cap protects against runaway real sends, not
    # bookkeeping operations.
    try:
        import os as _os
        if (_os.environ.get("EMAIL_PROVIDER", "resend").lower().strip() == "noop"
            or not _os.environ.get("RESEND_API_KEY", "").strip()):
            return (True, INVITE_DAILY_CAP)
    except Exception:
        pass
    from .database import is_postgres_backend
    if is_postgres_backend():
        # Production backend. Use the atomic row-locked claim (NOT the racy
        # legacy read-then-write). We run the SQL inline via asyncpg rather
        # than calling the claim_invite_cap() PG function, because that
        # function declares p_teacher_id as `text` and compares it to the
        # `uuid` teacher_id column (uuid = text has no built-in operator) —
        # which is why this path historically fell back to the racy version.
        # The inline statements cast explicitly with $1::uuid.
        try:
            return await _claim_and_bump_cap_postgres(teacher_id, batch_size)
        except Exception as e:
            _dep_log.error("[invites] atomic postgres cap claim failed — "
                           "falling back to legacy: %s", e)
            return await _claim_and_bump_cap_legacy(teacher_id, batch_size)
    try:
        from .database import supabase
        result = await asyncio.to_thread(
            lambda: supabase.rpc(
                "claim_invite_cap",
                {"p_teacher_id": teacher_id,
                 "p_batch": batch_size,
                 "p_cap": INVITE_DAILY_CAP},
            ).execute()
        )
        data = result.data
        if isinstance(data, list) and data:
            data = data[0].get("claim_invite_cap", data[0]) if isinstance(data[0], dict) else data[0]
        remaining = int(data)
        if remaining < 0:
            try:
                row = (await _atable("invite_send_counters")
                       .select("count").eq("teacher_id", teacher_id)
                       .eq("day", datetime.now(timezone.utc).date().isoformat())
                       .execute()).data
                used = (row[0]["count"] if row else 0)
                return (False, max(INVITE_DAILY_CAP - used, 0))
            except Exception:
                return (False, 0)
        return (True, remaining)
    except Exception as e:
        # Fall back to the legacy read-then-write path on ANY RPC error,
        # not just the "function does not exist" case. The previous
        # narrow check left a silent fail path where any other Postgres
        # error (permissions, type mismatch, transient connection issue)
        # caused the route to return a misleading "Daily cap exceeded.
        # 0 remaining, 1 requested" — the catch-all was returning
        # (False, 0) while the actual table sat at count=0. User
        # screenshotted exactly that: cap-status said "0 / 5000 used"
        # but send said "0 remaining." Always trying the legacy path
        # means a real cap exhaustion still blocks (legacy reads the
        # same counter row), but RPC bugs no longer fake the symptom.
        msg = str(e).lower()
        if "claim_invite_cap" in msg or "pgrst202" in msg or "function" in msg:
            _dep_log.warning("[invites] RPC missing, falling back to RACY check-and-bump. Run migrations/phase15_invite_cap_rpc.sql to fix. (%s)", e)
        else:
            _dep_log.error("[invites] RPC errored — falling back to legacy path: %s", e)
        return await _claim_and_bump_cap_legacy(teacher_id, batch_size)


async def _claim_and_bump_cap_postgres(teacher_id: str, batch_size: int) -> tuple[bool, int]:
    """Atomic daily-cap claim on the Postgres backend.

    Equivalent to the claim_invite_cap() PG function, run inline via asyncpg
    so we control the parameter casts (the deployed function compares a
    ``text`` parameter against the ``uuid`` teacher_id column, which has no
    operator). The single conditional UPDATE — ``count + $batch <= cap`` under
    Postgres's per-row write lock — is the atomic primitive: two concurrent
    callers serialise, and the one that would overshoot matches no row and is
    denied. No read-then-write window.

    Returns ``(allowed, remaining_after_claim)``.
    """
    from .postgres_table import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Ensure today's counter row exists (no-op if a concurrent caller
            # already created it). Separate from the UPDATE so the UPDATE's
            # predicate does the cap enforcement under the row lock.
            await conn.execute(
                "INSERT INTO invite_send_counters (teacher_id, day, count) "
                "VALUES ($1::uuid, current_date, 0) "
                "ON CONFLICT (teacher_id, day) DO NOTHING",
                str(teacher_id),
            )
            new_count = await conn.fetchval(
                "UPDATE invite_send_counters SET count = count + $2 "
                "WHERE teacher_id = $1::uuid AND day = current_date "
                "AND count + $2 <= $3 RETURNING count",
                str(teacher_id), int(batch_size), int(INVITE_DAILY_CAP),
            )
            if new_count is None:
                # Denied — read the live count so the caller can report an
                # accurate "N remaining" instead of a guess.
                used = await conn.fetchval(
                    "SELECT count FROM invite_send_counters "
                    "WHERE teacher_id = $1::uuid AND day = current_date",
                    str(teacher_id),
                )
                return (False, max(INVITE_DAILY_CAP - int(used or 0), 0))
            return (True, max(INVITE_DAILY_CAP - int(new_count), 0))


async def _claim_and_bump_cap_legacy(teacher_id: str, batch_size: int) -> tuple[bool, int]:
    """Pre-RPC fallback. Racy — only used when the migration hasn't run."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        row = (await _atable("invite_send_counters").select("count").eq("teacher_id", teacher_id).eq("day", today).execute()).data
        used = (row[0]["count"] if row else 0)
        remaining = INVITE_DAILY_CAP - used
        if batch_size > remaining:
            return (False, max(remaining, 0))
        if row:
            await _atable("invite_send_counters").update({"count": used + batch_size}).eq("teacher_id", teacher_id).eq("day", today).execute()
        else:
            await _atable("invite_send_counters").insert({"teacher_id": teacher_id, "day": today, "count": batch_size}).execute()
        return (True, max(remaining - batch_size, 0))
    except Exception as e:
        # The cap is abuse protection, not a hard product dependency.
        # If the counter table/RPC migration is missing or temporarily
        # unavailable, failing closed produces the confusing demo-blocker
        # state "0 / 5000 used" + "Daily cap exceeded. 0 remaining".
        # Keep the existing route-level rate limit as the backstop and let
        # the invite send proceed while logging loudly for ops.
        _dep_log.error("[invites] legacy cap check failed; allowing send without counter bump: %s", e)
        return (True, INVITE_DAILY_CAP)
