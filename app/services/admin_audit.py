"""Admin actions audit log — append-only forensic trail.

Sister service to auth_events (which covers login/logout/2fa). This
module covers data-mutation actions: who deleted that student roster,
who force-submitted that session, who bulk-dismissed those violations.

Sensitive admin endpoints call log_admin_action() before or after a
mutation. The function is best-effort: if the audit insert fails (DB
hiccup, transient asyncpg issue), the calling endpoint completes
normally and the audit gap is logged via the standard app logger.
Audit observability shouldn't be load-bearing for the operation
itself — preferable to miss one row than fail the user's action.

Schema lives in migrations/phase92_admin_audit_log.sql.

Pattern for new admin endpoints:

    from ..services.admin_audit import log_admin_action

    @router.delete("/api/v1/admin/<thing>/{id}")
    async def delete_thing(id: str, request: Request, teacher = Depends(require_admin)):
        # 1. Capture pre-state if it's forensically valuable.
        existing = await _atable("things").select("id,name").eq("id", id).single().execute()

        # 2. Perform the mutation.
        await _atable("things").delete().eq("id", id).execute()

        # 3. Log AFTER success. (If the mutation throws, no log row —
        #    correct, we only audit completed actions.)
        await log_admin_action(
            teacher_id=teacher["id"],
            action="delete_thing",
            target_type="thing",
            target_id=id,
            before_data=existing.data,
            request=request,  # captures IP + UA automatically
        )

        return {"status": "ok"}

The action verb_object naming is a soft convention — keep it
consistent with adjacent endpoints (delete_exam, bulk_dismiss,
force_submit) so filtering by action stays predictable.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

logger = logging.getLogger("admin.audit")


async def log_admin_action(
    *,
    teacher_id: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before_data: Any = None,
    after_data: Any = None,
    details: dict | None = None,
    request: Request | None = None,
) -> None:
    """Insert one row into admin_audit_log. Best-effort; never raises.

    Args (keyword-only so call sites stay readable):
        teacher_id:   UUID of the acting admin. Required.
        action:       Verb_object identifier, e.g. 'delete_exam',
                      'bulk_dismiss', 'force_submit'.
        target_type:  Entity-type tag for the target, e.g. 'exam',
                      'students', 'session', 'invite', 'group'.
        target_id:    Specific row/key the action affected. May be None
                      for bulk operations that touch many rows (the row
                      count typically goes in `details` then).
        before_data:  Pre-mutation snapshot. Pass only the forensically-
                      useful fields, not the whole row. None for inserts.
        after_data:   Post-mutation snapshot. None for deletes.
        details:      Free-form extra context: row counts, reason codes,
                      session keys, etc. Don't put PII here that isn't
                      already implied by target_id.
        request:      FastAPI Request to extract IP + User-Agent from.
                      Optional — pass when the call site has one.
    """
    from ..database import async_table as _atable

    ip: str | None = None
    user_agent: str | None = None
    if request is not None:
        # Trust the X-Forwarded-For / CF-Connecting-IP middleware has
        # already populated client.host with the real client IP.
        ip = getattr(request.client, "host", None) if request.client else None
        user_agent = request.headers.get("user-agent")

    row = {
        "teacher_id": teacher_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before_data": before_data,
        "after_data": after_data,
        "details": details or {},
        "ip": ip,
        "user_agent": user_agent,
    }

    try:
        await _atable("admin_audit_log").insert(row).execute()
    except Exception as e:
        # Don't propagate — audit failure must not break the action.
        # Log loudly so an ops dashboard or Sentry picks it up.
        logger.exception(
            "[admin.audit] failed to log action=%s target=%s/%s by teacher=%s: %s",
            action, target_type, target_id, teacher_id, e,
        )
