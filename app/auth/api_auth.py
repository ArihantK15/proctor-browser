"""API key authentication middleware."""
import hashlib
import secrets
from typing import Optional
from fastapi import Request, HTTPException

from ..database import async_table as _atable
import logging
logger = logging.getLogger(__name__)



async def generate_api_key(teacher_id: str, name: str) -> tuple[str, str]:
    """Generate a new API key and store its hash. Returns (key_id, full_key)."""
    raw = "pk_" + secrets.token_urlsafe(32)
    suffix = raw[-8:]
    key_hash = hashlib.sha256(raw.encode()).hexdigest()

    result = await _atable("api_keys").insert({
        "teacher_id": teacher_id,
        "name": name,
        "key_hash": key_hash,
        "key_prefix": f"pk_...{suffix}",
        "is_active": True,
    }).execute()

    key_id = result.data[0]["id"] if result.data else None
    if not key_id:
        raise HTTPException(status_code=500, detail="Failed to create API key")
    return key_id, raw


async def revoke_api_key(key_id: str, teacher_id: str) -> bool:
    """Soft-delete an API key (set is_active=False)."""
    result = await _atable("api_keys").update({"is_active": False})\
        .eq("id", key_id).eq("teacher_id", teacher_id).execute()
    return bool(result.data)


async def list_api_keys(teacher_id: str) -> list[dict]:
    """List all API keys for a teacher (never returns the raw key)."""
    result = await _atable("api_keys").select(
        "id,name,key_prefix,created_at,last_used_at,is_active"
    ).eq("teacher_id", teacher_id).order("created_at", desc=True).execute()
    return result.data or []


async def authenticate_api_key(request: Request) -> str:
    """Validate the X-API-Key header and return the owning teacher_id.

    Raises HTTPException(401) if the key is missing, invalid, or revoked.
    Also updates last_used_at on successful authentication.
    """
    raw_key = (request.headers.get("X-API-Key") or "").strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    result = await _atable("api_keys").select(
        "id,teacher_id,is_active"
    ).eq("key_hash", key_hash).limit(1).execute()

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    row = result.data[0]
    if not row.get("is_active"):
        raise HTTPException(status_code=401, detail="API key has been revoked")

    # Update last_used_at (fire-and-forget)
    try:
        from datetime import datetime, timezone
        await _atable("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", row["id"]).execute()
    except Exception:
        logger.debug("api_auth: last_used_at update failed", exc_info=True)

    return str(row["teacher_id"])
