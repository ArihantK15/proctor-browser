"""Superadmin breach-incident lifecycle endpoints.

Per Art 33(5) GDPR / §8(6) DPDP Act every personal-data breach is
recorded in `breach_incidents` regardless of whether it's reportable.
This router lets a superadmin:

  1. Log a new breach (discovery)
  2. Update its status through the lifecycle (contained -> notified -> closed)
  3. Dispatch email notifications (controller, data subjects)
  4. List/review all incidents

Authorization: _require_superadmin on every endpoint.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from ..auth import require_admin
from ..constants import SUPER_ADMIN_EMAIL
from ..database import async_table as _atable
from ..limiter import limiter
from ..jobs import enqueue_job
from ..services.admin_audit import log_admin_action

_log = logging.getLogger("admin.breach")

router = APIRouter()


# -- helpers ------------------------------------------------------------------

async def _authenticate(request: Request) -> dict[str, Any]:
    """Authenticate and return the user dict; sets request.state.user."""
    user = await require_admin(request)
    request.state.user = user
    return user


def _require_superadmin(request: Request) -> str:
    """Raise 403 unless the caller is the env-pinned superadmin."""
    user = getattr(request.state, "user", {})
    email = (user.get("email") or "").lower().strip()
    if email != SUPER_ADMIN_EMAIL.lower().strip():
        raise HTTPException(status_code=403, detail="superadmin required")
    return email


STATUS_LIFECYCLE: dict[str, list[str]] = {
    "open":       ["contained", "notified"],
    "contained":  ["notified", "closed"],
    "notified":   ["closed"],
}
RISK_LEVELS = {"low", "medium", "high", "unknown"}
ROLES = {"processor", "controller", "both"}


def _validate_transition(current: str, next_status: str) -> None:
    allowed = STATUS_LIFECYCLE.get(current, [])
    if next_status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"cannot transition from '{current}' to '{next_status}' "
                   f"(allowed: {allowed or 'none'})",
        )


async def _fetch_breach(breach_id: str) -> dict[str, Any]:
    """Return the breach row or raise 404."""
    rows = await _atable("breach_incidents").select("*").eq("id", breach_id).limit(1).execute()
    if not rows.data:
        raise HTTPException(status_code=404, detail="breach incident not found")
    return rows.data[0]


async def _write_audit_log(
    *, request: Request, breach_id: str, action: str, detail: str,
) -> None:
    """Record the action in admin_audit_log (best-effort; never raises).

    Delegates to the canonical helper so column names + JSONB serialisation
    match the real table — a hand-rolled insert here drifts and 500s.
    """
    user = getattr(request.state, "user", {}) or {}
    await log_admin_action(
        teacher_id=str(user.get("id") or ""),
        action=f"breach.{action}",
        target_type="breach_incident",
        target_id=breach_id,
        details={"note": detail},
        request=request,
    )


def _parse_ts(value: Any) -> datetime | None:
    """Coerce a TIMESTAMPTZ (datetime from asyncpg, or ISO str) to aware UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _with_deadline(row: dict[str, Any]) -> dict[str, Any]:
    """Annotate a breach row with the 72h regulatory deadline + time left.

    The clock starts at `discovered_at` (when we became aware), per the
    GDPR Art 33 / DPDP 72-hour rule — see docs/INCIDENT_RESPONSE.md §4.
    """
    out = dict(row)
    discovered = _parse_ts(out.get("discovered_at"))
    if discovered is not None:
        deadline = discovered + timedelta(hours=72)
        out["deadline_at"] = deadline.isoformat()
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds() / 3600
        out["hours_remaining"] = round(remaining, 1)
    return out


# -- schemas ------------------------------------------------------------------

class BreachCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(..., min_length=10, max_length=5000)
    data_categories: str | None = Field(None, max_length=2000)
    risk_level: str = "unknown"
    role: str = "processor"
    affected_scope: dict[str, Any] = Field(default_factory=dict)


class BreachUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    description: str | None = None
    data_categories: str | None = None
    risk_level: str | None = None
    affected_scope: dict[str, Any] | None = None
    # Manual record of the regulator/authority notice (DPDP Board / EU DPA).
    # That filing is out-of-band (a human files it); set this true to stamp
    # authority_notified_at. The controller/subject notify endpoints stamp
    # their own timestamps when they actually send.
    authority_notified: bool | None = None


class BreachNotifyControllerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_name: str = Field(..., min_length=1, max_length=500)


class BreachNotifySubjectsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_emails: list[str] = Field(..., min_length=1, max_length=100)


# -- endpoints ----------------------------------------------------------------

@router.post("/api/v1/admin/breach")
@limiter.limit("10/minute")
async def create_breach(request: Request, body: BreachCreateIn):
    """Log a new personal-data breach incident."""
    user = await _authenticate(request)
    _require_superadmin(request)

    risk = body.risk_level.lower()
    if risk not in RISK_LEVELS:
        raise HTTPException(status_code=422, detail=f"invalid risk_level: {risk}")

    role = body.role.lower()
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"invalid role: {role}")

    now = datetime.now(timezone.utc).isoformat()
    row = await _atable("breach_incidents").insert({
        "discovered_at": now,
        "description": body.description.strip(),
        "data_categories": body.data_categories.strip() if body.data_categories else None,
        "affected_scope": json.dumps(body.affected_scope) if body.affected_scope else "{}",
        "risk_level": risk,
        "role": role,
        "status": "open",
        "created_by": user.get("id"),
        "created_at": now,
    }).execute()

    breach_id = str(row.data[0]["id"])
    await _write_audit_log(
        request=request, breach_id=breach_id,
        action="create", detail=f"risk={risk}, role={role}",
    )

    row.data[0]["id"] = breach_id
    return _with_deadline(row.data[0])


@router.get("/api/v1/admin/breach")
@limiter.limit("30/minute")
async def list_breaches(request: Request, limit: int = 50, offset: int = 0):
    """List breach incidents, newest first."""
    await _authenticate(request)
    _require_superadmin(request)
    rows = await (
        _atable("breach_incidents")
        .select("*")
        .order("discovered_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    data = [_with_deadline({**r, "id": str(r["id"])}) for r in rows.data]
    return {"data": data, "total": len(data)}


@router.get("/api/v1/admin/breach/{breach_id}")
@limiter.limit("30/minute")
async def get_breach(request: Request, breach_id: str):
    """Fetch a single breach incident."""
    await _authenticate(request)
    _require_superadmin(request)
    row = await _fetch_breach(breach_id)
    row["id"] = str(row["id"])
    return _with_deadline(row)


@router.patch("/api/v1/admin/breach/{breach_id}")
@limiter.limit("10/minute")
async def update_breach(request: Request, breach_id: str, body: BreachUpdateIn):
    """Transition breach status or update metadata."""
    await _authenticate(request)
    _require_superadmin(request)
    current = await _fetch_breach(breach_id)

    _validate_transition(current["status"], body.status)

    updates: dict[str, Any] = {"status": body.status}

    # Regulator notice is filed manually out-of-band; record it explicitly.
    # (Controller/subject notifications are stamped by the notify endpoints
    # when the email actually goes out — don't double-write or invert here.)
    if body.authority_notified:
        updates["authority_notified_at"] = datetime.now(timezone.utc).isoformat()

    if body.description is not None:
        updates["description"] = body.description.strip()
    if body.data_categories is not None:
        updates["data_categories"] = body.data_categories.strip()
    if body.risk_level is not None:
        rl = body.risk_level.lower()
        if rl not in RISK_LEVELS:
            raise HTTPException(status_code=422, detail=f"invalid risk_level: {rl}")
        updates["risk_level"] = rl
    if body.affected_scope is not None:
        updates["affected_scope"] = json.dumps(body.affected_scope)

    await _atable("breach_incidents").update(updates).eq("id", breach_id).execute()

    await _write_audit_log(
        request=request, breach_id=breach_id,
        action="update", detail=f"status -> {body.status}",
    )

    row = await _fetch_breach(breach_id)
    row["id"] = str(row["id"])
    return _with_deadline(row)


@router.post("/api/v1/admin/breach/{breach_id}/notify-controller")
@limiter.limit("5/minute")
async def notify_controller(request: Request, breach_id: str, body: BreachNotifyControllerIn):
    """Dispatch processor->controller breach notification email."""
    await _authenticate(request)
    _require_superadmin(request)
    breach = await _fetch_breach(breach_id)

    org_name = body.org_name.strip()
    orgs = await (
        _atable("organizations")
        .select("id, billing_email")
        .eq("name", org_name)
        .limit(1)
        .execute()
    )
    if not orgs.data:
        raise HTTPException(status_code=404, detail=f"organization '{org_name}' not found")
    org = orgs.data[0]
    org_id = str(org["id"])

    to_email: str | None = org.get("billing_email") or None
    to_name: str = ""

    if not to_email:
        admins = await (
            _atable("teachers")
            .select("email, full_name")
            .eq("org_id", org_id)
            .in_("org_role", ["admin", "superadmin"])
            .limit(1)
            .execute()
        )
        if not admins.data:
            raise HTTPException(
                status_code=404,
                detail=f"no billing_email or admin teachers found for org '{org_name}'",
            )
        target = admins.data[0]
        to_email = target["email"]
        to_name = target.get("full_name") or ""

    enqueue_job(
        "send_controller_breach_notification_job",
        to_email=to_email,
        to_name=to_name,
        org_name=org_name,
        description=breach["description"],
        data_categories=breach.get("data_categories") or "",
        discovered_at=breach["discovered_at"],
    )

    now = datetime.now(timezone.utc).isoformat()
    await _atable("breach_incidents").update({
        "controllers_notified_at": now,
    }).eq("id", breach_id).execute()

    await _write_audit_log(
        request=request, breach_id=breach_id,
        action="notify-controller",
        detail=f"org={org_name}, to={to_email}",
    )

    return {"ok": True, "to": to_email, "org_name": org_name}


@router.post("/api/v1/admin/breach/{breach_id}/notify-subjects")
@limiter.limit("3/minute")
async def notify_subjects(request: Request, breach_id: str, body: BreachNotifySubjectsIn):
    """Dispatch controller->data-subject breach notification emails (high-risk)."""
    await _authenticate(request)
    _require_superadmin(request)
    breach = await _fetch_breach(breach_id)

    sent_to: list[str] = []
    for email in body.to_emails:
        enqueue_job(
            "send_data_subject_breach_notification_job",
            to_email=email.strip().lower(),
            to_name="",
            description=breach["description"],
            data_categories=breach.get("data_categories") or "",
        )
        sent_to.append(email.strip().lower())

    now = datetime.now(timezone.utc).isoformat()
    await _atable("breach_incidents").update({
        "subjects_notified_at": now,
    }).eq("id", breach_id).execute()

    await _write_audit_log(
        request=request, breach_id=breach_id,
        action="notify-subjects",
        detail=f"sent_to_count={len(sent_to)}",
    )

    return {"ok": True, "sent_to_count": len(sent_to), "sent_to": sent_to}
