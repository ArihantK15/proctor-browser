from __future__ import annotations
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel


class OrgRole(StrEnum):
    ADMIN = "admin"
    TEACHER = "teacher"
    SUPERADMIN = "superadmin"


class OrgInviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OrgInviteIn(BaseModel):
    email: str
    full_name: str = ""


class OrgOut(BaseModel):
    id: str
    name: str
    slug: str
    max_students: int
    created_at: str


class OrgMemberOut(BaseModel):
    id: str
    email: str
    full_name: str
    org_role: str
    created_at: str | None = None


class OrgInviteOut(BaseModel):
    id: str
    org_id: str
    email: str
    full_name: str
    status: str
    created_at: str
    expires_at: str | None = None


class OrgBillingOut(BaseModel):
    plan: str
    status: str
    trial_end: str | None = None
    current_period_end: str | None = None
    student_count: int = 0
    max_students: int = 30
