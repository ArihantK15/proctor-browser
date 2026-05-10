"""Domain enums and Pydantic models.

Extracted from app/dependencies.py to break up the god module.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ─── Domain enums (string-backed for DB compatibility) ────────────

class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SUBMITTED = "submitted"
    FORCE_SUBMITTED = "force_submitted"
    ABANDONED = "abandoned"
    REJECTED = "rejected"


class InviteStatus(StrEnum):
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    ACCEPTED = "accepted"
    BOUNCED = "bounced"
    FAILED = "failed"
    REVOKED = "revoked"
    QUEUED = "queued"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ─── Pydantic models ──────────────────────────────────────────────

class EventIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    event_type: str
    severity:   str
    details:    Optional[str] = None


class RegisterIn(BaseModel):
    model_config = ConfigDict(strict=True)
    full_name:   str
    roll_number: str
    email:       str
    phone:       Optional[str] = None
    teacher_id:  Optional[str] = None


class ValidateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    roll_number: str
    access_code: Optional[str] = None
    exam_id: Optional[str] = None


class ResultIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:      str
    roll_number:     str
    full_name:       str
    email:           str
    time_taken_secs: int
    answers:         dict = {}
    score:           int  = 0
    total:           int  = 0
    violations:      list = []


class AnswerIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:  str
    question_id: str
    answer:      str


class BulkAnswerIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    answers:    dict


class FrameIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    frame:      str
    timestamp:  str
    event_type: Optional[str] = None


class IdVerifyIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:   str
    roll_number:  str
    selfie_frame: str
    id_frame:     str
    full_name:    str = ""
    timestamp:    str = ""


class IdDecisionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    violation_id: int
    session_key:  str
    decision:     str


class TeacherSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str


class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str


class RefreshIn(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str


class StudentSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str


class StudentLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email: str
