"""Email OTP service — 6-digit codes for 2FA fallback, step-up, recovery."""
import logging
import secrets
from datetime import datetime, timezone, timedelta

import bcrypt

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 3


class OtpRateLimitError(RuntimeError):
    """Raised by issue() when a (user, purpose) has exceeded MAX_CODES_PER_HOUR.

    Callers should surface this as a 429 to the user without leaking
    whether the rate-limit gate fired vs the upstream send pipeline.
    """


async def issue(user_kind: str, user_id: str, purpose: str) -> str:
    """Generate a 6-digit OTP, store its hash, return the raw code.

    Caller should send the raw code to the user's email.

    Enforces MAX_CODES_PER_HOUR per (user_kind, user_id, purpose) so a
    spammer can't trigger unlimited OTP emails to a victim address.
    Earlier this constant was declared but never read, leaving the
    issuance path with no per-user cap (only the outer endpoint's
    request-level rate-limit, which is per-IP and bypassable).

    Also invalidates any prior unused codes for the same (user, purpose)
    before issuing the new one. Without this, every fresh issue() call
    left old codes valid in parallel — verify() iterates the last 5
    unused codes, so an attacker could brute-force against N codes at
    once, widening the keyspace hit rate.
    """
    # email_otps is a SYSTEM-managed, pre-auth table — issue()/verify() run
    # before the user has any session identity, so there is no app.teacher_id() /
    # app.account_id() to scope to. Run under system_context so the
    # app.is_privileged() RLS policy grants access under the restricted
    # procta_app role; without it every email_otps query denies-all once RLS is
    # live (it was the one table missed by every policy phase).
    from ..db_context import system_context
    with system_context():
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        recent = await _atable("email_otps").select("id")\
            .eq("user_kind", user_kind).eq("user_id", user_id)\
            .eq("purpose", purpose)\
            .gte("created_at", one_hour_ago.isoformat())\
            .execute()
        if len(recent.data or []) >= MAX_CODES_PER_HOUR:
            logger.warning(
                "[email_otp] rate limit exceeded user_kind=%s user_id=%s purpose=%s count=%d",
                user_kind, user_id, purpose, len(recent.data or []),
            )
            raise OtpRateLimitError(
                f"Too many codes requested for {purpose}. Please wait before trying again."
            )

        # Invalidate any still-valid unused codes so only the newest one can
        # verify. Race-tolerant: a concurrent verify() may complete against
        # an in-flight code; in that case the UPDATE no-ops and the user
        # got their answer either way.
        await _atable("email_otps").update({"used_at": now.isoformat()})\
            .eq("user_kind", user_kind).eq("user_id", user_id)\
            .eq("purpose", purpose).is_("used_at", "null").execute()

        code = "".join(secrets.choice("0123456789") for _ in range(6))
        code_hash = bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()
        expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
        await _atable("email_otps").insert({
            "user_kind": user_kind,
            "user_id": user_id,
            "purpose": purpose,
            "code_hash": code_hash,
            "expires_at": expires_at.isoformat(),
        }).execute()
        return code


async def verify(user_kind: str, user_id: str, purpose: str, code: str) -> bool:
    """Verify a 6-digit OTP. Returns True on success, False on failure.
    Invalidates used OTPs. Tracks attempts and expires old codes."""
    # Pre-auth / system-managed — see issue(). Run under system_context so the
    # app.is_privileged() policy grants email_otps access under procta_app.
    from ..db_context import system_context
    with system_context():
        rows = await _atable("email_otps").select("*")\
            .eq("user_kind", user_kind).eq("user_id", user_id)\
            .eq("purpose", purpose).is_("used_at", "null")\
            .order("created_at", desc=True).limit(5).execute()
        for row in (rows.data or []):
            if row["attempts"] >= MAX_ATTEMPTS:
                continue
            expires = row.get("expires_at")
            if expires and datetime.fromisoformat(str(expires).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                continue
            if bcrypt.checkpw(code.encode(), row["code_hash"].encode()):
                await _atable("email_otps").update({
                    "used_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", row["id"]).execute()
                return True
            await _atable("email_otps").update({
                "attempts": row["attempts"] + 1,
            }).eq("id", row["id"]).execute()
        return False
