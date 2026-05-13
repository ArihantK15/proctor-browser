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


async def issue(user_kind: str, user_id: str, purpose: str) -> str:
    """Generate a 6-digit OTP, store its hash, return the raw code.

    Caller should send the raw code to the user's email.
    """
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    code_hash = bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)
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
