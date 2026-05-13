"""TOTP 2FA — enrollment, verification, backup codes, recovery, grace period."""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from cryptography.fernet import Fernet
import pyotp

from ..constants import TOTP_ENCRYPTION_KEY, TOTP_GRACE_DAYS
from ..database import async_table as _atable

logger = logging.getLogger(__name__)

BACKUP_CODE_COUNT = 10
ISSUER_NAME = "Procta"

# Cache Fernet key at module level so enc/dec are consistent within a process
_FERNET_KEY = (TOTP_ENCRYPTION_KEY.encode()
               if TOTP_ENCRYPTION_KEY
               else Fernet.generate_key())
if not TOTP_ENCRYPTION_KEY:
    logger.warning(
        "[totp] TOTP_ENCRYPTION_KEY not set — using ephemeral key. "
        "TOTP secrets will be lost on server restart. "
        "Set TOTP_ENCRYPTION_KEY in .env for production."
    )


def _get_fernet() -> Fernet:
    return Fernet(_FERNET_KEY)
    return Fernet(key)


def _encrypt_secret(secret: str) -> str:
    return _get_fernet().encrypt(secret.encode()).decode()


def _decrypt_secret(encrypted: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.warning("[totp] decryption failed: %s", e)
        return ""


def _generate_backup_codes() -> list[str]:
    import secrets
    return [secrets.token_hex(6).upper() for _ in range(BACKUP_CODE_COUNT)]


async def generate_secret(user_kind: str, user_id: str, email: str) -> dict:
    """Generate a TOTP secret, encrypt it, store it, and return provisioning info.
    
    Returns: {secret, otpauth_url, qr_data_url} (or None if already enabled).
    """
    # Check if already enabled
    table = "teachers" if user_kind == "teacher" else "student_accounts"
    row = await _atable(table).select("totp_enabled_at,totp_secret").eq("id", user_id).limit(1).execute()
    if not row.data:
        return {"error": "User not found"}
    user = row.data[0]
    if user.get("totp_enabled_at"):
        return {"error": "2FA already enabled"}

    secret = pyotp.random_base32()
    encrypted = _encrypt_secret(secret)
    totp = pyotp.TOTP(secret, issuer=ISSUER_NAME)
    otpauth_url = totp.provisioning_uri(name=email, issuer_name=ISSUER_NAME)

    await _atable(table).update({"totp_secret": encrypted}).eq("id", user_id).execute()

    return {
        "secret": secret,
        "otpauth_url": otpauth_url,
        "email": email,
    }


async def confirm_enrollment(user_kind: str, user_id: str, code: str) -> dict:
    """Confirm TOTP enrollment by verifying a 6-digit code.
    On success, generates backup codes and enables 2FA.
    """
    table = "teachers" if user_kind == "teacher" else "student_accounts"
    row = await _atable(table).select("totp_secret").eq("id", user_id).limit(1).execute()
    if not row.data or not row.data[0].get("totp_secret"):
        return {"error": "No pending enrollment"}
    secret = _decrypt_secret(row.data[0]["totp_secret"])
    if not secret:
        return {"error": "Invalid secret"}

    totp = pyotp.TOTP(secret, issuer=ISSUER_NAME)
    if not totp.verify(code, valid_window=1):
        return {"error": "Invalid code. Try again."}

    # Generate and store backup codes
    codes = _generate_backup_codes()
    import bcrypt
    hashed = [bcrypt.hashpw(c.encode(), bcrypt.gensalt()).decode() for c in codes]

    await _atable(table).update({
        "totp_enabled_at": datetime.now(timezone.utc).isoformat(),
        "backup_codes_hash": json.dumps(hashed),
    }).eq("id", user_id).execute()

    return {
        "ok": True,
        "backup_codes": codes,  # Shown ONCE to user
        "message": "Two-factor authentication enabled.",
    }


async def verify_code(user_kind: str, user_id: str, code: str) -> bool:
    """Verify a TOTP code. Returns True on success."""
    table = "teachers" if user_kind == "teacher" else "student_accounts"
    row = await _atable(table).select("totp_secret,totp_enabled_at").eq("id", user_id).limit(1).execute()
    if not row.data or not row.data[0].get("totp_enabled_at"):
        return False
    secret = _decrypt_secret(row.data[0]["totp_secret"])
    if not secret:
        return False
    totp = pyotp.TOTP(secret, issuer=ISSUER_NAME)
    return totp.verify(code, valid_window=1)


async def verify_backup_code(user_kind: str, user_id: str, code: str) -> bool:
    """Verify and invalidate a backup code. Returns True on success."""
    table = "teachers" if user_kind == "teacher" else "student_accounts"
    row = await _atable(table).select("backup_codes_hash").eq("id", user_id).limit(1).execute()
    if not row.data:
        return False
    codes_hash = json.loads(row.data[0].get("backup_codes_hash", "[]"))
    import bcrypt
    remaining = []
    found = False
    for h in codes_hash:
        if not found and bcrypt.checkpw(code.upper().encode(), h.encode()):
            found = True
        else:
            remaining.append(h)
    if found:
        await _atable(table).update({
            "backup_codes_hash": json.dumps(remaining),
        }).eq("id", user_id).execute()
        return True
    return False


async def check_grace_expired(user_kind: str, user_id: str) -> bool:
    """Check if the 30-day 2FA grace period has expired."""
    table = "teachers" if user_kind == "teacher" else "student_accounts"
    row = await _atable(table).select("totp_enabled_at,totp_grace_started_at").eq("id", user_id).limit(1).execute()
    if not row.data:
        return False
    user = row.data[0]
    if user.get("totp_enabled_at"):
        return False  # Already enrolled
    grace_start = user.get("totp_grace_started_at")
    if not grace_start:
        return False
    if isinstance(grace_start, str):
        grace_start = datetime.fromisoformat(grace_start.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - grace_start).days > TOTP_GRACE_DAYS
