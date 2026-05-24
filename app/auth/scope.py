"""Tenancy scope helpers — translate JWT role + optional ?teacher_id=
filter into a uniform "scope" that admin endpoints can apply when
filtering queries by `teacher_id` / `org_id`.

Why a separate module? `admin.py` and several sub-routers
(`admin_sessions.py`, `admin_exams.py`, etc.) all face the same
question: "which sessions/results/violations is this caller allowed
to see, and which subset did they ask for?" Keeping the logic here
means a single audit point for tenancy enforcement.

Three caller categories:

  • teacher    → locked to own teacher_id. Any ?teacher_id= filter
                 in the URL is ignored.
  • admin      → org-scoped. Can pass ?teacher_id=<uuid> to narrow
                 to one teacher; the param is silently dropped if
                 the requested teacher isn't in their org.
  • superadmin → unrestricted. Can pass ?teacher_id= to focus on
                 one teacher anywhere.

All endpoints accepting the filter MUST pipe the returned scope dict
through `scope_to_teacher_ids()` before applying it to a query, so
the cross-tenant guards run uniformly.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from ..database import async_table as _atable


async def resolve_scope(teacher: dict, request: Request) -> dict:
    """Build the scope dict from the authenticated teacher's role and
    any ?teacher_id= query parameter.

    Returned shape:
      {
        "role": "teacher" | "admin" | "superadmin",
        "teacher_id": str | None,   # specific teacher filter, or None
        "org_id":     str | None,   # admin-scoped to this org, or None
      }
    """
    role = (teacher.get("org_role") or "teacher").lower()
    org_id = teacher.get("org_id")
    requested = (request.query_params.get("teacher_id", "") or "").strip() or None

    if role == "superadmin":
        return {"role": "superadmin", "teacher_id": requested, "org_id": None}

    if role == "admin" and org_id:
        if requested and not await _verify_teacher_in_org(requested, str(org_id)):
            requested = None  # silently drop cross-tenant attempts
        return {"role": "admin", "teacher_id": requested, "org_id": str(org_id)}

    # Plain teacher (or admin without an org_id, which shouldn't happen
    # but stays safe): locked to own teacher_id, ignore any URL filter.
    return {
        "role": "teacher",
        "teacher_id": str(teacher["id"]),
        "org_id": str(org_id) if org_id else None,
    }


async def _verify_teacher_in_org(teacher_id: str, org_id: str) -> bool:
    """Single-row check that teacher_id belongs to org_id."""
    rows = (
        await _atable("teachers")
        .select("id")
        .eq("id", str(teacher_id))
        .eq("org_id", str(org_id))
        .limit(1)
        .execute()
    ).data
    return bool(rows)


async def scope_to_teacher_ids(scope: dict) -> Optional[list[str]]:
    """Materialise the scope into a list of teacher IDs that downstream
    queries can `.in_("teacher_id", ...)` filter by.

    Returns None to mean "no filter" (superadmin viewing everything).
    """
    if scope.get("teacher_id"):
        return [scope["teacher_id"]]
    if scope.get("org_id"):
        rows = (
            await _atable("teachers")
            .select("id")
            .eq("org_id", scope["org_id"])
            .execute()
        ).data or []
        return [str(r["id"]) for r in rows]
    return None  # superadmin, no filter


async def assert_session_accessible(session_id: str, scope: dict) -> dict:
    """Single-session access check for endpoints that operate on a
    specific session_key (timeline, risk-score, terminate, etc.).

    Returns the session row on success. Raises 404 if missing, 403 if
    out of scope. Replacement for the old `_assert_session_owned()`
    which only knew about single-teacher ownership.
    """
    rows = (
        await _atable("exam_sessions")
        .select("*")
        .eq("session_key", session_id)
        .limit(1)
        .execute()
    ).data
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")
    sess = rows[0]
    sess_tid = str(sess.get("teacher_id", ""))

    if scope["role"] == "superadmin":
        return sess
    if scope["role"] == "admin":
        if not await _verify_teacher_in_org(sess_tid, scope["org_id"]):
            raise HTTPException(status_code=403, detail="Session not in your organization")
        return sess
    # teacher
    if sess_tid != scope["teacher_id"]:
        raise HTTPException(status_code=403, detail="You don't own this session")
    return sess
