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


def compute_is_solo(org_role: str | None, org_id, member_count: int) -> bool:
    """Two-mode dashboard signal.

    Solo = a non-superadmin caller who is effectively alone, so the legacy
    dashboard should show a pure-teacher view with zero org/admin chrome:
      • superadmin → never solo (cross-org tooling; usually no org_id);
      • no org_id  → solo (a lone teacher account);
      • org with ≤1 member → solo;
      • otherwise → not solo (institute account).
    """
    # Normalise case to match resolve_scope()/require_admin(), which both
    # .lower() the role — so a non-canonical "Superadmin" can't slip through
    # and be mis-classified as a solo teacher.
    if (org_role or "").lower() == "superadmin":
        return False
    if not org_id:
        return True
    return member_count <= 1


async def org_is_solo(teacher: dict) -> bool:
    """Resolve is_solo for a teacher dict (as returned by require_admin).

    Short-circuits for superadmin and org-less accounts so we only hit the
    DB for the genuine institute-vs-solo distinction. Counts org members
    by selecting their ids — the org member set is tiny (seat-limited), so
    a count-by-fetch is cheap and avoids a separate COUNT round-trip.
    """
    org_role = teacher.get("org_role", "teacher")
    org_id = teacher.get("org_id")
    if org_role == "superadmin" or not org_id:
        return compute_is_solo(org_role, org_id, 0)
    rows = (await _atable("teachers").select("id")
            .eq("org_id", str(org_id)).execute()).data or []
    return compute_is_solo(org_role, org_id, len(rows))


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

    Always returns 404 for missing-or-out-of-scope sessions so we don't
    leak existence (mirrors the data-leak guard the old
    `assert_session_owned` enforced — see test_cross_tenant_access_is_denied).

    Preserves the original "no exam_sessions row but violations exist
    for me" fallback path: students who submitted ID photos but never
    started the exam have only violations, no session row.
    """
    # For plain teachers, delegate to the legacy ownership helper so the
    # full fallback chain (orphan row, violation-only synthesis) keeps
    # working. The helper already 404s cross-tenant.
    if scope["role"] == "teacher":
        from ..repositories.sessions import assert_session_owned
        return await assert_session_owned(session_id, scope["teacher_id"])

    # Admin / superadmin path. Try direct row lookup first.
    rows = (
        await _atable("exam_sessions")
        .select("*")
        .eq("session_key", session_id)
        .limit(1)
        .execute()
    ).data
    if rows:
        sess = rows[0]
        sess_tid = str(sess.get("teacher_id", ""))
        if scope["role"] == "superadmin":
            return sess
        # admin — must be in their org
        if sess_tid and await _verify_teacher_in_org(sess_tid, scope["org_id"]):
            return sess
        # Orphan row with no teacher_id — only allow if no other teacher's
        # violations claim it.
        if not sess_tid:
            v_other = (await _atable("violations")
                       .select("teacher_id")
                       .eq("session_key", session_id)
                       .limit(1).execute()).data
            if v_other:
                v_tid = str(v_other[0].get("teacher_id") or "")
                if v_tid and not await _verify_teacher_in_org(v_tid, scope["org_id"]):
                    raise HTTPException(status_code=404, detail="Session not found")
            return sess
        raise HTTPException(status_code=404, detail="Session not found")

    # No row — synthesise from violations only if at least one violation's
    # teacher_id is in scope.
    v_rows = (await _atable("violations")
              .select("teacher_id")
              .eq("session_key", session_id)
              .limit(5).execute()).data or []
    in_scope_tid: str | None = None
    for v in v_rows:
        v_tid = str(v.get("teacher_id") or "")
        if not v_tid:
            continue
        if scope["role"] == "superadmin":
            in_scope_tid = v_tid
            break
        if scope["role"] == "admin" and await _verify_teacher_in_org(v_tid, scope["org_id"]):
            in_scope_tid = v_tid
            break
    if in_scope_tid:
        # Synthetic placeholder — matches the shape returned by
        # repositories/sessions.py:assert_session_owned for the same
        # "violations exist but no session row" case.
        from ..models import SessionStatus
        return {
            "session_key": session_id,
            "teacher_id":  in_scope_tid,
            "roll_number": (session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]),
            "full_name":   "",
            "status":      SessionStatus.IN_PROGRESS,
            "started_at":  "",
            "submitted_at": "",
            "score": None, "total": None, "risk_score": None, "exam_id": None,
        }
    raise HTTPException(status_code=404, detail="Session not found")
