"""Teacher-reported issue inbox.

Teachers can file bugs / feature requests / session-specific flags from the
dashboard. Super admins triage the global inbox. Org admins are intentionally
not given the global endpoint in this phase.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from ..auth import require_admin
from ..constants import ISSUE_CATEGORIES, ISSUE_SEVERITIES, ISSUE_STATUSES
from ..database import async_table as _atable
from ..limiter import limiter
from ..utils import fmt_ist, now_ist

router = APIRouter(prefix="")


def _role(teacher: dict) -> str:
    return (teacher.get("org_role") or "teacher").lower()


def _clean_choice(value: str | None, allowed: set[str], default: str | None = None) -> str:
    v = (value or default or "").strip().lower()
    if v not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid value: {value}")
    return v


def _serialize_issue(row: dict, org_map: dict[str, str] | None = None,
                     teacher_map: dict[str, dict] | None = None) -> dict:
    org_id = str(row.get("org_id") or "")
    teacher_id = str(row.get("teacher_id") or "")
    t = (teacher_map or {}).get(teacher_id, {})
    return {
        "id": str(row["id"]),
        "org_id": org_id,
        "org_name": (org_map or {}).get(org_id, ""),
        "teacher_id": teacher_id,
        "teacher_name": t.get("full_name", ""),
        "teacher_email": t.get("email", ""),
        "session_id": row.get("session_id") or "",
        "exam_id": str(row.get("exam_id") or ""),
        "category": row.get("category", "other"),
        "severity": row.get("severity", "normal"),
        "description": row.get("description", ""),
        "status": row.get("status", "open"),
        "superadmin_note": row.get("superadmin_note") or "",
        "created_at": fmt_ist(row.get("created_at", "")),
        "resolved_at": fmt_ist(row.get("resolved_at", "")) if row.get("resolved_at") else "",
    }


@router.post("/api/v1/issues")
@limiter.limit("20/hour")
async def create_issue(request: Request, body: dict = Body(...)):
    teacher = await require_admin(request)
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    category = _clean_choice(body.get("category"), ISSUE_CATEGORIES, "other")
    severity = _clean_choice(body.get("severity"), ISSUE_SEVERITIES, "normal")
    description = (body.get("description") or "").strip()
    if len(description) < 20:
        raise HTTPException(status_code=400, detail="Description must be at least 20 characters")

    payload = {
        "org_id": str(org_id),
        "teacher_id": str(teacher["id"]),
        "session_id": (body.get("session_id") or "").strip() or None,
        "exam_id": (body.get("exam_id") or "").strip() or None,
        "category": category,
        "severity": severity,
        "description": description,
        "status": "open",
    }
    rows = (await _atable("issues").insert(payload).execute()).data or []
    if not rows:
        raise HTTPException(status_code=500, detail="Issue was not created")
    return {"issue": _serialize_issue(rows[0])}


@router.get("/api/v1/issues/mine")
@limiter.limit("30/minute")
async def list_my_issues(request: Request):
    teacher = await require_admin(request)
    rows = (
        await _atable("issues")
        .select("*")
        .eq("teacher_id", str(teacher["id"]))
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    ).data or []
    return {"issues": [_serialize_issue(r) for r in rows]}


@router.get("/api/v1/admin/issues")
@limiter.limit("30/minute")
async def list_admin_issues(request: Request):
    teacher = await require_admin(request)
    if _role(teacher) != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    status = (request.query_params.get("status") or "").strip().lower()
    org_id = (request.query_params.get("org_id") or "").strip()
    category = (request.query_params.get("category") or "").strip().lower()
    q = _atable("issues").select("*").order("created_at", desc=True)
    if status and status != "all":
        if status not in ISSUE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        q = q.eq("status", status)
    if org_id:
        q = q.eq("org_id", org_id)
    if category and category != "all":
        if category not in ISSUE_CATEGORIES:
            raise HTTPException(status_code=400, detail="Invalid category")
        q = q.eq("category", category)

    rows = (await q.limit(250).execute()).data or []
    org_ids = list({str(r.get("org_id")) for r in rows if r.get("org_id")})
    teacher_ids = list({str(r.get("teacher_id")) for r in rows if r.get("teacher_id")})
    org_map: dict[str, str] = {}
    teacher_map: dict[str, dict] = {}
    if org_ids:
        orgs = (await _atable("organizations").select("id,name").in_("id", org_ids).execute()).data or []
        org_map = {str(o["id"]): o.get("name", "") for o in orgs}
    if teacher_ids:
        teachers = (await _atable("teachers").select("id,full_name,email").in_("id", teacher_ids).execute()).data or []
        teacher_map = {str(t["id"]): t for t in teachers}

    serialized = [_serialize_issue(r, org_map=org_map, teacher_map=teacher_map) for r in rows]
    open_count = sum(1 for r in serialized if r["status"] == "open")
    return {"issues": serialized, "open_count": open_count}


@router.patch("/api/v1/admin/issues/{issue_id}")
@limiter.limit("60/minute")
async def update_admin_issue(issue_id: str, request: Request, body: dict = Body(...)):
    teacher = await require_admin(request)
    if _role(teacher) != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    patch: dict = {}
    if "status" in body:
        status = _clean_choice(body.get("status"), ISSUE_STATUSES, "open")
        patch["status"] = status
        patch["resolved_at"] = now_ist().isoformat() if status == "resolved" else None
    if "superadmin_note" in body:
        patch["superadmin_note"] = (body.get("superadmin_note") or "").strip()[:4000]
    if not patch:
        raise HTTPException(status_code=400, detail="No changes supplied")

    rows = (await _atable("issues").update(patch).eq("id", issue_id).execute()).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {"issue": _serialize_issue(rows[0])}
