"""Org management router — admin-only org/billing/member routes."""

import hashlib
import logging
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException

from ..auth import require_admin
from ..constants import SUPER_ADMIN_EMAIL
from ..database import async_table as _atable
from ..limiter import limiter
from ..utils import now_ist, fmt_ist
from ..services.sessions import get_org_subscription, PLAN_LIMITS
from ..invites import _get_invite_base_url
from ..models import OrgInviteIn
from ..models.exam import LIVE_STATUSES as _LIVE_STATUSES
from ..jobs import enqueue_job, send_org_invite_email_job
from ..services.admin_audit import log_admin_action
from ..auth.admin_auth import clear_teacher_cache, require_reauth_or_403

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


async def _count_org_students(org_id: str) -> int:
    """Count an org's students the way the quota trigger does — via the teacher
    join, NOT students.org_id.

    students.org_id is only set by the admin roster-import path; public
    self-registration writes {roll_number, email, teacher_id} with no org_id, so
    counting `students WHERE org_id = X` reports 0 for self-registered students.
    The org's true membership runs through the teacher (phase90 trigger:
    students JOIN teachers ON teachers.id = students.teacher_id WHERE
    teachers.org_id = X), so we replicate that here to stay consistent with what
    the quota actually enforces.
    """
    teacher_rows = (await _atable("teachers").select("id")
                    .eq("org_id", str(org_id)).execute()).data or []
    teacher_ids = [str(t["id"]) for t in teacher_rows if t.get("id")]
    if not teacher_ids:
        return 0
    res = await _atable("students").select("id", count="exact").in_("teacher_id", teacher_ids).execute()
    return res.count if getattr(res, "count", None) is not None else len(res.data or [])


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
            # The platform superadmin is NOT a real org member — it must never
            # surface in the org's member list, the teacher-filter dropdown, or
            # the member count (all fed by this endpoint). Excluded by ROLE
            # (org_role='superadmin' — the source of truth resolve_scope uses)
            # AND by the env-pinned SUPER_ADMIN_EMAIL, so setting either is
            # enough.
            if not (
                (m.get("org_role") or "").strip().lower() == "superadmin"
                or (SUPER_ADMIN_EMAIL and str(m.get("email", "")).strip().lower() == SUPER_ADMIN_EMAIL)
            )
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
    pending = await (
        _atable("org_invites")
        .select("id")
        .eq("email", email)
        .eq("org_id", str(org_id))
        .in_("status", ["pending", "pending_verification"])
        .execute()
    )
    if pending.data:
        raise HTTPException(status_code=409, detail="An invitation has already been sent to this email")

    token = str(_uuid.uuid4())
    # SHA-256 hex of the token — what we actually look up by, so a
    # read-only DB compromise can't surface usable invite links.
    # See migrations/phase69_invite_token_hash.sql for rationale.
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    await _atable("org_invites").insert({
        "org_id": str(org_id),
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
    """Remove a teacher from the org.

    Requires a fresh reauth_token (P1.2 — X-Reauth-Token header) on top
    of the admin/superadmin org_role check. Kicking a colleague out is
    a high-blast-radius action; locking it behind a fresh password
    prompt closes the stolen-session impersonation path.
    """
    from ..auth.admin_auth import clear_teacher_cache, require_reauth_or_403
    admin = await require_admin(request)
    if admin.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can remove members")
    require_reauth_or_403(None, str(admin["id"]), request=request)
    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    if str(admin["id"]) == teacher_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    target = await _atable("teachers").select("id,org_id,org_role").eq("id", teacher_id).eq("org_id", str(org_id)).limit(1).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found in this org")

    before = target.data[0]
    # Atomic update — include org_id in the WHERE so a teacher who got
    # reassigned to another org between the SELECT above and this UPDATE
    # is NOT wiped from their new org by mistake. The session/refresh
    # revokes below stay user-scoped (no org dimension on those rows).
    await _atable("teachers").update({"org_id": None, "org_role": "teacher"})\
        .eq("id", teacher_id).eq("org_id", str(org_id)).execute()
    clear_teacher_cache(teacher_id)
    # Revoke all active auth sessions and refresh tokens so the removed
    # member cannot continue using previously issued JWTs
    await _atable("auth_sessions").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_id", teacher_id).eq("user_kind", "teacher").is_("revoked_at", "null").execute()
    await _atable("refresh_tokens").update({"revoked_at": now_ist().isoformat()})\
        .eq("user_id", teacher_id).eq("kind", "teacher").is_("revoked_at", "null").execute()
    from ..services.admin_audit import log_admin_action
    await log_admin_action(
        teacher_id=str(admin["id"]),
        action="remove_member",
        target_type="teacher",
        target_id=teacher_id,
        before_data=before,
        request=request,
    )
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
    # 'viewer' is intentionally NOT accepted: the teachers_org_role_check DB
    # constraint only permits 'admin'/'teacher', and a viewer role has no
    # defined permissions anywhere (resolve_scope handles teacher/admin/super
    # only). Accepting it would 500 on the constraint. A real read-only viewer
    # role is a feature (enum + constraint migration + scope/UI gating), not a
    # string in this set.
    valid_roles = {"admin", "teacher"}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(sorted(valid_roles))}")
    # P1.2 helper — same fail-closed semantics, accepts body field or
    # X-Reauth-Token header. Role-change endpoints lose data flow on
    # mis-issued tokens, hence the gate.
    from ..auth.admin_auth import clear_teacher_cache, require_reauth_or_403
    require_reauth_or_403(body, str(teacher["id"]), request=request)

    target = await _atable("teachers").select("id,org_role").eq("id", teacher_id).eq("org_id", str(org_id)).limit(1).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found in your org")

    if str(teacher_id) == str(teacher["id"]):
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    before = target.data[0]
    # Atomic update with org_id guard — without this, a teacher who
    # transferred orgs between the existence check and the UPDATE would
    # have their role changed by an admin who no longer manages them.
    await _atable("teachers").update({"org_role": role})\
        .eq("id", teacher_id).eq("org_id", str(org_id)).execute()
    clear_teacher_cache(teacher_id)
    from ..services.admin_audit import log_admin_action
    await log_admin_action(
        teacher_id=str(teacher["id"]),
        action="set_member_role",
        target_type="teacher",
        target_id=teacher_id,
        before_data={"org_role": before.get("org_role")},
        after_data={"org_role": role},
        request=request,
    )
    return {"ok": True, "role": role}


@router.post("/api/v1/admin/teachers/{from_id}/reassign")
@limiter.limit("10/hour")
async def reassign_teacher(from_id: str, body: dict, request: Request):
    """Transfer all teaching data from one teacher to another within the same org.

    Both teachers must belong to the admin's org.  The receiving teacher must
    already exist in the org.  On a ``(teacher_id, exam_id)`` or
    ``(teacher_id, roll_number)`` unique-constraint conflict the transaction
    rolls back and the endpoint returns 409.
    """
    from ..services.teacher_transfer import reassign_teaching_data
    from ..postgres_table import get_pool
    from ..services.admin_audit import log_admin_action
    import uuid as _uuid

    admin = await require_admin(request)
    if admin.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can reassign teachers")

    org_id = admin.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    to_id = (body.get("to_teacher_id") or "").strip()
    if not to_id:
        raise HTTPException(status_code=422, detail="to_teacher_id is required")

    if str(from_id).strip() == to_id:
        raise HTTPException(status_code=400, detail="Cannot transfer data to the same teacher")

    # Load both teachers — both must exist and belong to the caller's org.
    from_rows = (await _atable("teachers").select("id,org_id,email,full_name")
                 .eq("id", str(from_id)).eq("org_id", str(org_id)).limit(1).execute()).data
    if not from_rows:
        raise HTTPException(status_code=404, detail="Source teacher not found in your organization")

    to_rows = (await _atable("teachers").select("id,org_id,email,full_name")
               .eq("id", to_id).eq("org_id", str(org_id)).limit(1).execute()).data
    if not to_rows:
        raise HTTPException(status_code=400,
                            detail="The receiving teacher must already be in your organization")

    from_teacher = from_rows[0]
    to_teacher = to_rows[0]

    from .. import db_context as _dbctx
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # RLS: this raw-tx path bypasses PostgresTable.execute's context
                # application, and it remaps rows the admin doesn't "own" across
                # teacher-owned tables. Without the system principal, procta_app
                # RLS would silently UPDATE 0 rows (the empty-lobby class of bug).
                # Admin + same-org authz is already enforced above; elevate here.
                await _dbctx.apply_request_context(conn, force_system=True)
                counts = await reassign_teaching_data(conn, str(from_id), to_id)
    except Exception as exc:
        # Unique-constraint violations manifest as asyncpg exceptions.
        # Catch them and surface a readable 409 so the admin can resolve
        # the conflict manually.
        err_str = str(exc)
        if "unique" in err_str.lower() or "duplicate" in err_str.lower():
            raise HTTPException(
                status_code=409,
                detail="The receiving teacher already has conflicting exam or student data. "
                       "Resolve the conflict before transferring.",
            )
        raise

    await log_admin_action(
        teacher_id=str(admin["id"]),
        action="reassign_teacher",
        target_type="teacher",
        target_id=str(from_id),
        before_data={
            "from_teacher": {"id": str(from_id), "email": from_teacher.get("email"),
                             "full_name": from_teacher.get("full_name")},
            "to_teacher": {"id": to_id, "email": to_teacher.get("email"),
                           "full_name": to_teacher.get("full_name")},
        },
        after_data={"counts": counts},
        request=request,
    )

    return {"ok": True, "counts": counts}


@router.patch("/api/v1/org")
@limiter.limit("10/hour")
async def update_org(body: dict, request: Request):
    """Update org settings (name, logo).

    `logo_url` (optional): HTTPS URL to the org's logo, max 1024 chars.
    Used by the PDF scorecard generator + branded email templates.
    Setting it flips the customer experience from Procta-branded to
    their-brand-with-"Powered by Procta" footer. Cleared by passing
    an empty string. Hosted by the customer (their own CDN / S3 /
    Cloudinary); Procta never holds the bytes.
    """
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can update org settings")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")

    update_fields: dict = {}
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    update_fields["name"] = name
    update_fields["slug"] = slug

    # logo_url is optional; absent key means "don't touch it".
    if "logo_url" in body:
        raw_logo = (body.get("logo_url") or "").strip()
        if raw_logo:
            # Validate scheme + length here rather than at the DB layer
            # — gives admins a useful error instead of an opaque 400.
            if len(raw_logo) > 1024:
                raise HTTPException(status_code=400, detail="Logo URL must be 1024 characters or fewer")
            if not raw_logo.lower().startswith(("https://", "data:image/")):
                raise HTTPException(status_code=400, detail="Logo URL must be HTTPS (or a data:image/ URI)")
            update_fields["logo_url"] = raw_logo
        else:
            # Explicit empty string clears the logo.
            update_fields["logo_url"] = None

    await _atable("organizations").update(update_fields).eq("id", str(org_id)).execute()
    result: dict = {"ok": True, "name": name, "slug": slug}
    if "logo_url" in update_fields:
        result["logo_url"] = update_fields["logo_url"]
    return result


@router.get("/api/v1/org/billing")
@limiter.limit("30/minute")
async def get_billing(request: Request):
    """Return org's subscription and student usage."""
    teacher = await require_admin(request)
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")

    sub = await get_org_subscription(str(org_id))
    student_count = await _count_org_students(str(org_id))

    plan_name = (sub or {}).get("plan", "starter")
    max_students = PLAN_LIMITS.get(plan_name, 30)

    return {
        "plan": plan_name,
        "status": (sub or {}).get("status", "unknown"),
        "trial_end": fmt_ist((sub or {}).get("trial_end", "")),
        "current_period_end": fmt_ist((sub or {}).get("current_period_end", "")),
        "student_count": student_count,
        "max_students": max_students,
        "scheduled_plan": (sub or {}).get("scheduled_plan"),
        "scheduled_plan_effective_at": fmt_ist((sub or {}).get("scheduled_plan_effective_at", "")),
    }


@router.delete("/api/v1/admin/orgs/{org_id}")
@limiter.limit("10/hour")
async def delete_org(org_id: str, request: Request):
    """Soft-delete / suspend an organization (superadmin only, reversible).

    Sets ``deleted_at`` on the org, suspends all member teachers (which
    triggers immediate 401 on next API call), revokes their auth sessions,
    stops billing, and revokes pending invites.  Fails with 409 if the org
    has active exam sessions unless ``{"force": true}`` is supplied.

    The optional JSON body ({"reason": str, "force": bool}) is parsed
    defensively rather than via a Body(...) param: a declared body field made
    request validation order-sensitive under the randomised test suite (a
    benign body intermittently 422'd). Reading it by hand — like the webhook
    does — keeps the endpoint tolerant of a missing/empty/non-JSON body.
    """
    admin = await require_admin(request)
    if admin.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    require_reauth_or_403(None, str(admin["id"]), request=request)

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    org = (await _atable("organizations").select("id,name,slug,deleted_at")
           .eq("id", str(org_id)).limit(1).execute()).data
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org = org[0]

    if org.get("deleted_at"):
        raise HTTPException(status_code=409, detail="Organization already deleted")

    # Self-protection: refuse to delete an org that contains the platform
    # superadmin (same exclusion logic used by list_members).
    members = (await _atable("teachers").select("id,email,org_role")
               .eq("org_id", str(org_id)).execute()).data or []
    for m in members:
        role = (m.get("org_role") or "").strip().lower()
        email = str(m.get("email", "")).strip().lower()
        if role == "superadmin" or (SUPER_ADMIN_EMAIL and email == SUPER_ADMIN_EMAIL):
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the platform admin org",
            )

    # Active-session guard (mirrors gap #7).
    if not body.get("force"):
        member_ids = [str(m["id"]) for m in members if m.get("id")]
        if member_ids:
            live = (await _atable("exam_sessions")
                    .select("session_key", count="exact")
                    .in_("teacher_id", member_ids)
                    .in_("status", _LIVE_STATUSES)
                    .execute())
            if (live.count or 0) > 0:
                raise HTTPException(
                    status_code=409,
                    detail="Org has active exam sessions; end them first",
                )

    errors: list[str] = []
    now_val = now_ist().isoformat()

    # 1. Soft-delete the org row.
    try:
        await _atable("organizations").update({
            "deleted_at": now_val,
            "deleted_by": str(admin["id"]),
            "delete_reason": (body.get("reason") or "").strip() or None,
        }).eq("id", str(org_id)).execute()
    except Exception as e:
        errors.append(f"organizations.update: {type(e).__name__}")

    # 2. Suspend all active member teachers.  Only ``status='active'`` rows
    # are touched so independently deleted/already-suspended members are never
    # clobbered, and we stamp ``org_suspended_at`` so restore can re-activate
    # ONLY the teachers this org-delete suspended (not someone suspended on
    # their own merits before the delete). See phase108.
    suspended = 0
    for m in members:
        mid = str(m["id"])
        try:
            await _atable("teachers").update({"status": "suspended", "org_suspended_at": now_val}) \
                .eq("id", mid).eq("status", "active").execute()
            suspended += 1
        except Exception as e:
            errors.append(f"teachers.suspend({mid[:8]}): {type(e).__name__}")
            continue
        try:
            clear_teacher_cache(mid)
        except Exception:
            pass
        try:
            await _atable("auth_sessions").update({"revoked_at": now_val}) \
                .eq("user_id", mid).eq("user_kind", "teacher") \
                .is_("revoked_at", "null").execute()
        except Exception as e:
            errors.append(f"auth_sessions({mid[:8]}): {type(e).__name__}")
        try:
            await _atable("refresh_tokens").update({"revoked_at": now_val}) \
                .eq("user_id", mid).eq("kind", "teacher") \
                .is_("revoked_at", "null").execute()
        except Exception as e:
            errors.append(f"refresh_tokens({mid[:8]}): {type(e).__name__}")

    # 5. Stop billing (local only — Razorpay-side cancellation is follow-up).
    try:
        await _atable("subscriptions").update({"status": "cancelled"}) \
            .eq("org_id", str(org_id)).execute()
    except Exception as e:
        errors.append(f"subscriptions.cancel: {type(e).__name__}")

    # 6. Revoke pending invites.
    try:
        await _atable("org_invites").update({"status": "revoked"}) \
            .eq("org_id", str(org_id)) \
            .in_("status", ["pending", "pending_verification"]).execute()
    except Exception as e:
        errors.append(f"org_invites.revoke: {type(e).__name__}")

    # 7. Audit.
    try:
        await log_admin_action(
            teacher_id=str(admin["id"]),
            action="delete_org",
            target_type="organization",
            target_id=str(org_id),
            before_data={"name": org.get("name"), "slug": org.get("slug"),
                         "member_count": len(members)},
            request=request,
        )
    except Exception as e:
        errors.append(f"audit: {type(e).__name__}")

    return {"ok": True, "org_id": str(org_id),
            "members_suspended": suspended, "errors": errors}


@router.post("/api/v1/admin/orgs/{org_id}/restore")
@limiter.limit("10/hour")
async def restore_org(org_id: str, request: Request):
    """Restore a previously soft-deleted org (superadmin only).

    Reverses delete_org: clears ``deleted_at``, sets member teacher
    status back to active, clears caches.  Billing status is NOT
    automatically restored (left for manual re-activation).
    """
    admin = await require_admin(request)
    if admin.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    require_reauth_or_403(None, str(admin["id"]), request=request)

    org = (await _atable("organizations").select("id,name,slug,deleted_at")
           .eq("id", str(org_id)).limit(1).execute()).data
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org = org[0]
    if not org.get("deleted_at"):
        raise HTTPException(status_code=409, detail="Organization is not deleted")

    errors: list[str] = []

    # 1. Clear deleted_at.
    try:
        await _atable("organizations").update({
            "deleted_at": None, "deleted_by": None, "delete_reason": None,
        }).eq("id", str(org_id)).execute()
    except Exception as e:
        errors.append(f"organizations.restore: {type(e).__name__}")

    # 2. Re-activate ONLY the members this org-delete suspended — identified
    # by the org_suspended_at marker (phase108). A teacher suspended on their
    # own merits before the delete (marker NULL) or since moved to 'deleted'
    # is left untouched. Filter in Python so we also clear each member's cache.
    rows = (await _atable("teachers").select("id,status,org_suspended_at")
            .eq("org_id", str(org_id)).execute()).data or []
    for m in rows:
        if (m.get("status") != "suspended") or not m.get("org_suspended_at"):
            continue
        mid = str(m["id"])
        try:
            await _atable("teachers").update({"status": "active", "org_suspended_at": None}) \
                .eq("id", mid).execute()
            clear_teacher_cache(mid)
        except Exception as e:
            errors.append(f"teachers.restore({mid[:8]}): {type(e).__name__}")

    # 3. Audit.
    try:
        await log_admin_action(
            teacher_id=str(admin["id"]),
            action="restore_org",
            target_type="organization",
            target_id=str(org_id),
            before_data={"deleted_at": org.get("deleted_at")},
            request=request,
        )
    except Exception as e:
        errors.append(f"audit: {type(e).__name__}")

    return {"ok": True, "org_id": str(org_id), "errors": errors}


@router.get("/api/v1/admin/all-orgs")
@limiter.limit("30/minute")
async def list_all_orgs(request: Request, include_deleted: bool = False):
    """Superadmin: list all organizations.

    By default excludes soft-deleted orgs.  Pass ``?include_deleted=true``
    to include them (each row will carry a ``deleted_at`` field).
    """
    teacher = await require_admin(request)
    if teacher.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")

    query = _atable("organizations").select("id,name,slug,max_students,"
                                            "max_students_override,billing_credit_inr,"
                                            "created_at,deleted_at,deleted_by,delete_reason")
    if not include_deleted:
        query = query.is_("deleted_at", "null")
    orgs = await query.execute()
    rows = orgs.data or []

    result = []
    for org in rows:
        org_id = str(org["id"])
        sub = await get_org_subscription(org_id)
        student_count = await _count_org_students(org_id)
        teacher_result = await _atable("teachers").select("id,email,org_role").eq("org_id", org_id).execute()
        # Exclude the platform superadmin from the org's teacher headcount —
        # by role (org_role='superadmin') and by env-pinned SUPER_ADMIN_EMAIL.
        teacher_count = sum(
            1 for t in (teacher_result.data or [])
            if not (
                (t.get("org_role") or "").strip().lower() == "superadmin"
                or (SUPER_ADMIN_EMAIL and str(t.get("email", "")).strip().lower() == SUPER_ADMIN_EMAIL)
            )
        )
        result.append({
            "id": org_id,
            "name": org["name"],
            "slug": org["slug"],
            "max_students": org["max_students"],
            "max_students_override": org.get("max_students_override"),
            "billing_credit_inr": org.get("billing_credit_inr", 0),
            "plan": (sub or {}).get("plan", "starter"),
            "status": (sub or {}).get("status", "unknown"),
            "student_count": student_count,
            "teacher_count": teacher_count,
            "created_at": fmt_ist(org.get("created_at", "")),
            "deleted_at": fmt_ist(org.get("deleted_at", "")) or None,
            "delete_reason": org.get("delete_reason"),
        })

    return {"orgs": result}


@router.post("/api/v1/admin/orgs/{org_id}/limit-override")
@limiter.limit("10/hour")
async def set_limit_override(org_id: str, body: dict, request: Request):
    """Set or clear an org's max_students_override (superadmin only).

    The override persists across reconcile cycles because
    ``reconcile_org_entitlement`` reads it.  ``null`` clears the override
    (reverting to the plan-derived cap on the next reconcile).
    Returns the new effective ``max_students``.
    """
    admin = await require_admin(request)
    if admin.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    require_reauth_or_403(body, str(admin["id"]), request=request)

    org = (await _atable("organizations").select("id,name")
           .eq("id", str(org_id)).limit(1).execute()).data
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    raw = body.get("max_students_override")
    if raw is not None:
        try:
            val = int(raw)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="max_students_override must be an integer or null")
        if val < 0 or val > 100000:
            raise HTTPException(status_code=400, detail="max_students_override must be between 0 and 100000")
    else:
        val = None

    await _atable("organizations").update({"max_students_override": val}).eq("id", str(org_id)).execute()

    # Reconcile immediately so the effective max_students reflects the override.
    from ..services.billing import reconcile_org_entitlement
    new_cap = await reconcile_org_entitlement(org_id)

    await log_admin_action(
        teacher_id=str(admin["id"]),
        action="set_limit_override",
        target_type="organization",
        target_id=str(org_id),
        before_data={"name": org[0].get("name")},
        after_data={"max_students_override": val, "effective_max_students": new_cap},
        request=request,
    )

    return {"ok": True, "max_students_override": val, "effective_max_students": new_cap}


@router.post("/api/v1/admin/orgs/{org_id}/credit")
@limiter.limit("10/hour")
async def grant_credit(org_id: str, body: dict, request: Request):
    """Grant or adjust billing credit for an org (superadmin only).

    ``amount_inr`` (int): positive adds to balance, negative subtracts
    (floor at 0).  ``reason`` (str, required): audited description of why.
    Returns the new ``billing_credit_inr`` balance.
    """
    admin = await require_admin(request)
    if admin.get("org_role") != "superadmin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    require_reauth_or_403(body, str(admin["id"]), request=request)

    org = (await _atable("organizations").select("id,name,billing_credit_inr")
           .eq("id", str(org_id)).limit(1).execute()).data
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        delta = int(body.get("amount_inr", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="amount_inr must be an integer")

    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")

    old_balance = int(org[0].get("billing_credit_inr", 0))
    new_balance = max(0, old_balance + delta)

    await _atable("organizations").update({"billing_credit_inr": new_balance}).eq("id", str(org_id)).execute()

    await log_admin_action(
        teacher_id=str(admin["id"]),
        action="grant_credit",
        target_type="organization",
        target_id=str(org_id),
        before_data={"name": org[0].get("name"), "billing_credit_inr": old_balance},
        after_data={"billing_credit_inr": new_balance, "delta": delta, "reason": reason},
        details={"reason": reason, "delta": delta},
        request=request,
    )

    return {"ok": True, "billing_credit_inr": new_balance, "delta": delta}
