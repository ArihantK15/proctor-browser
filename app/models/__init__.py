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


# ─── Admin-facing models (extracted from app/routers/admin.py) ─────

class ClearSessionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    step: str = ""
    include_active: bool = False
    include_completed: bool = False
    exam_id: str = ""
    token: str = ""
    ack: str = ""


class EmailScorecardsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    resend_all: bool = False
    custom_message: str = ""


class ScheduleIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    starts_at: str | None = None
    ends_at: str | None = None


class ShuffleIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    shuffle_questions: bool | None = None
    shuffle_options: bool | None = None


class AccessCodeIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str | None = None
    access_code: str = ""


class BulkRegisterIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str | None = None
    students: list[dict[str, str]]


class BulkStudentIn(BaseModel):
    model_config = ConfigDict(strict=True)
    students: list[dict[str, str]]


class CreateExamIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_title: str = "Exam"
    duration_minutes: int = 60


class CreateGroupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_name: str


class RenameGroupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_name: str


class GroupMembersIn(BaseModel):
    model_config = ConfigDict(strict=True)
    roll_numbers: list[str]


class ExamGroupAssignIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_ids: list[str]


class UploadQuestionImageIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question_id: str = ""
    data_url: str = ""


class InviteRecipient(BaseModel):
    email: str
    full_name: str
    roll_number: str


class SendInvitesBody(BaseModel):
    recipients: list[InviteRecipient]
    exam_id: str
    custom_message: Optional[str] = None


class SaveTemplateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    template_name: str
    include_questions: bool = True
