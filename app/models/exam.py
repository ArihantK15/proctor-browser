from __future__ import annotations
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SUBMITTED = "submitted"
    FORCE_SUBMITTED = "force_submitted"
    ABANDONED = "abandoned"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EventIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    event_type: str
    severity:   str
    details:    Optional[str] = None


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


class CreateExamIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_title: str = "Exam"
    duration_minutes: int = 60


class UploadQuestionImageIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question_id: str = ""
    data_url: str = ""


class SaveTemplateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    template_name: str
    include_questions: bool = True
