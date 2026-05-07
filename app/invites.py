"""Invite sending helpers: token generation, daily cap enforcement."""

import asyncio
import os
import secrets
from datetime import date as _date

from .database import supabase, async_table as _atable
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
    try:
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
                       .eq("day", _date.today().isoformat())
                       .execute()).data
                used = (row[0]["count"] if row else 0)
                return (False, max(INVITE_DAILY_CAP - used, 0))
            except Exception:
                return (False, 0)
        return (True, remaining)
    except Exception as e:
        msg = str(e).lower()
        if "claim_invite_cap" in msg or "pgrst202" in msg or "function" in msg:
            _dep_log.warning("[invites] RPC missing, falling back to RACY check-and-bump. Run migrations/phase15_invite_cap_rpc.sql to fix. (%s)", e)
            return await _claim_and_bump_cap_legacy(teacher_id, batch_size)
        _dep_log.error("[invites] atomic cap claim failed: %s", e)
        return (False, 0)


async def _claim_and_bump_cap_legacy(teacher_id: str, batch_size: int) -> tuple[bool, int]:
    """Pre-RPC fallback. Racy — only used when the migration hasn't run."""
    today = _date.today().isoformat()
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
        _dep_log.warning("[invites] legacy cap check failed: %s", e)
        return (False, 0)
