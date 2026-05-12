"""LTI 1.3 models — registration, context, launch data."""

from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class LtiRegistrationIn(BaseModel):
    """Registration info for an LMS platform (Canvas, Moodle, Google Classroom, etc.)."""
    issuer: str
    client_id: str
    auth_login_url: str
    auth_token_url: str
    key_set_url: str
    deployment_ids: list[str] = []
    platform_name: Optional[str] = None


class LtiRegistrationOut(BaseModel):
    id: str
    issuer: str
    client_id: str
    auth_login_url: str
    auth_token_url: str
    key_set_url: str
    deployment_ids: list[str]
    platform_name: Optional[str] = None
    created_at: str
    updated_at: str
