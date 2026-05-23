"""Org management router — admin-only org/billing/member routes."""

import hashlib
import logging
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException

from ..auth import require_admin
from ..database import async_table as _atable
from ..limiter import limiter
from ..utils import now_ist, fmt_ist
from ..constants import SUPER_ADMIN_EMAIL
from ..services.sessions import get_org_subscription, PLAN_LIMITS
from ..invites import _get_invite_base_url
from ..models import OrgInviteIn
from ..jobs import enqueue_job, send_org_invite_email_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.get("/api/v1/org")
@limiter.limit("60/minute")
async def get_org(request: Request):
    """Return current org details."""
    teacher = await require_admin(request)
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    result = await _atable("organizations").select("*").eq("id", str(org_id)).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Organization not found")
    org = result.data[0]
    return {
        "id": str(org["id"]),
        "name": org["name"],
        "slug": org["slug"],
        "max_students": org["max_students"],
        "created_at": fmt_ist(org.get("created_at", "")),
    }


@router.get("/api/v1/org/members")
@limiter.limit("60/minute")
async def list_members(request: Request):
    """List all teachers in the org."""
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can view members")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    result = await _atable("teachers").select("id,email,full_name,org_role,created_at").eq("org_id", str(org_id)).execute()
    return {
        "members": [
            {"id": str(m["id"]), "email": m["email"], "full_name": m["full_name"],
             "org_role": m["org_role"], "created_at": fmt_ist(m.get("created_at", ""))}
            for m in (result.data or [])
        ]
    }


@router.post("/api/v1/org/invite")
@limiter.limit("10/hour")
async def invite_member(body: OrgInviteIn, request: Request):
    """Invite a teacher to join the org by email."""
    inviter = await require_admin(request)
    if inviter.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can invite members")
    org_id = inviter.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    # Check if already a member
    existing = await _atable("teachers").select("id").eq("email", email).eq("org_id", str(org_id)).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="This user is already a member of your organization")

    # Check if already invited
    pending = await _atable("org_invites").select("id").eq("email", email).eq("org_id", str(org_id)).eq("status", "pending").execute()
    if pending.data:
        raise HTTPException(status_code=409, detail="An invitation has already been sent to this email")

    token = str(_uuid.uuid4())
    # SHA-256 hex of the token — what we actually look up by, so a
    # read-only DB compromise can't surface usable invite links.
    # See migrations/phase69_invite_token_hash.sql for rationale.
    # The plaintext `token` column is also populated for backward
    # compatibility during the transition (a future migration will
    # drop it once we verify the new path is stable in production).
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    await _atable("org_invites").insert({
        "org_id": str(org_id),
        "token": token,
        "token_hash": token_hash,
        "email": email,
        "full_name": body.full_name,
        "status": "pending",
        "invited_by": str(inviter["id"]),
        "expires_at": expires_at,
    }).execute()

    org_result = await _atable("organizations").select("name").eq("id", str(org_id)).limit(1).execute()
    org_name = org_result.data[0]["name"] if org_result.data else "your organization"
    invite_url = f"{_get_invite_base_url()}/org-invite/{token}"
    inviter_name = inviter.get("full_name", inviter.get("email", "Your admin"))
    enqueue_job(send_org_invite_email_job,
                to_email=email, invite_url=invite_url,
                org_name=org_name, invited_by_name=inviter_name)

    return {"ok": True, "message": f"Invitation sent to {email}"}


@router.delete("/api/v1/org/members/{teacher_id}")
@limiter.limit("10/hour")
async def remove_member(teacher_id: str, request: Request):
    """Remove a teacher from the org."""
    admin = await require_admin(request)
    if admin.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can remove members")
    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    if str(admin["id"]) == teacher_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    target = await _atable("teachers").select("id,org_id,org_role").eq("id", teacher_id).eq("org_id", str(org_id)).limit(1).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found in this org")

    # Remove org association, but don't delete the teacher account
    await _atable("teachers").update({"org_id": None, "org_role": "teacher"}).eq("id", teacher_id).execute()
    # Revoke all active auth sessions and refresh tokens so the removed
    # member cannot continue using previously issued JWTs
    await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_id", teacher_id).eq("user_kind", "teacher").is_("revoked_at", "null").execute()
    await _atable("refresh_tokens").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_id", teacher_id).eq("kind", "teacher").is_("revoked_at", "null").execute()
    return {"ok": True}


@router.patch("/api/v1/org/members/{teacher_id}/role")
@limiter.limit("20/hour")
async def set_member_role(teacher_id: str, body: dict, request: Request):
    """Change a member's org role (admin only)."""
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only org admins can change roles")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    role = (body.get("role") or "").strip().lower()
    valid_roles = {"admin", "teacher", "viewer"}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(sorted(valid_roles))}")

    target = await _atable("teachers").select("id,org_role").eq("id", teacher_id).eq("org_id", str(org_id)).limit(1).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found in your org")

    if str(teacher_id) == str(teacher["id"]):
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    await _atable("teachers").update({"org_role": role}).eq("id", teacher_id).execute()
    return {"ok": True, "role": role}


@router.patch("/api/v1/org")
@limiter.limit("10/hour")
async def update_org(body: dict, request: Request):
    """Update org settings (name)."""
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can update org settings")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")

    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    await _atable("organizations").update({"name": name, "slug": slug}).eq("id", str(org_id)).execute()
    return {"ok": True, "name": name, "slug": slug}


@router.get("/api/v1/org/billing")
@limiter.limit("30/minute")
async def get_billing(request: Request):
    """Return org's subscription and student usage."""
    teacher = await require_admin(request)
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    sub = await get_org_subscription(str(org_id))
    count_result = await _atable("students").select("id", count="exact").eq("org_id", str(org_id)).execute()
    student_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])

    plan_name = (sub or {}).get("plan", "starter")
    max_students = PLAN_LIMITS.get(plan_name, 30)

    return {
        "plan": plan_name,
        "status": (sub or {}).get("status", "unknown"),
        "trial_end": fmt_ist((sub or {}).get("trial_end", "")),
        "current_period_end": fmt_ist((sub or {}).get("current_period_end", "")),
        "student_count": student_count,
        "max_students": max_students,
    }


@router.get("/api/v1/admin/all-orgs")
@limiter.limit("30/minute")
async def list_all_orgs(request: Request):
    """Superadmin: list all organizations."""
    teacher = await require_admin(request)
    if teacher.get("email", "").lower() != SUPER_ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Super admin access required")

    orgs = await _atable("organizations").select("id,name,slug,max_students,created_at").execute()
    rows = orgs.data or []

    result = []
    for org in rows:
        org_id = str(org["id"])
        sub = await get_org_subscription(org_id)
        count_result = await _atable("students").select("id", count="exact").eq("org_id", org_id).execute()
        student_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        teacher_result = await _atable("teachers").select("id").eq("org_id", org_id).execute()
        teacher_count = len(teacher_result.data or [])
        result.append({
            "id": org_id,
            "name": org["name"],
            "slug": org["slug"],
            "max_students": org["max_students"],
            "plan": (sub or {}).get("plan", "starter"),
            "status": (sub or {}).get("status", "unknown"),
            "student_count": student_count,
            "teacher_count": teacher_count,
            "created_at": fmt_ist(org.get("created_at", "")),
        })

    return {"orgs": result}
