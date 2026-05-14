from __future__ import annotations
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, ConfigDict


class InviteStatus(StrEnum):
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    ACCEPTED = "accepted"
    BOUNCED = "bounced"
    FAILED = "failed"
    REVOKED = "revoked"
    QUEUED = "queued"


class InviteRecipient(BaseModel):
    email: str
    full_name: str
    roll_number: str


class SendInvitesBody(BaseModel):
    recipients: list[InviteRecipient]
    exam_id: str
    custom_message: Optional[str] = None
    idempotency_key: Optional[str] = None
