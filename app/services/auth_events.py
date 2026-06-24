"""Persistent auth audit log — every auth event recorded in auth_events table."""
import logging
from typing import Optional
from fastapi import Request
from ..database import async_table as _atable

logger = logging.getLogger(__name__)


async def record(
    event_type: str,
    request: Request = None,
    user_kind: str = "",
    user_id: str = "",
    email: str = "",
    meta: Optional[dict] = None,
) -> None:
    """Insert an auth event into the audit log. Best-effort — never raises."""
    try:
        ip = request.client.host if request and request.client else ""
        ua = request.headers.get("user-agent", "") if request else ""
        await _atable("auth_events").insert({
            "user_kind": user_kind,
            # Callers pass teacher["id"]/account["id"] straight through, which is
            # a native UUID off asyncpg — coerce so neither the text column nor a
            # downstream serializer chokes ("UUID is not JSON serializable").
            "user_id": str(user_id) if user_id else "",
            "email": email,
            "event_type": event_type,
            "ip": ip,
            "user_agent": ua,
            "meta": meta or {},
        }).execute()
    except Exception as e:
        logger.warning("[auth_events] failed to record %s: %s", event_type, e)
