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
    detection_confidence: Optional[float] = None


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


# Allowed reason codes for retake / reject. Mirrored verbatim in
# renderer/index.html#_idReasonLabel and dashboard-app.js#_REASON_LABELS
# so the student sees the same label the teacher picked. Keep all three
# in sync when adding a new code.
ID_REJECT_REASON_CODES: tuple[str, ...] = (
    # retake-flavoured (recoverable — student fixes and resubmits)
    "selfie_blurry",
    "id_not_visible",
    "lighting_dark",
    "wrong_angle",
    # reject-flavoured (closes the session)
    "face_mismatch",
    "id_fake_or_edited",
    "wrong_person",
    "other",
)


class IdDecisionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    violation_id: int
    session_key:  str
    decision:     str
    # Optional structured reason. reason_code is one of
    # ID_REJECT_REASON_CODES (validated in the router); reason_text is
    # free-text from the teacher, capped at 500 chars. Both stored in
    # violations.details JSON next to decided_by/decided_at.
    reason_code:  Optional[str] = None
    reason_text:  Optional[str] = None


class ClearSessionsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    step: str = ""
    include_active: bool = False
    include_completed: bool = False
    exam_id: str = ""
    token: str = ""
    ack: str = ""
    reauth_token: str = ""


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
    # When True the endpoint validates + classifies rows but does NOT
    # write to the DB. Returns the same shape as a real run plus a
    # `format_counts` map and `dominant_format` key from
    # services/roll_formats so the UI can show a confirm-step preview
    # ("we detected 297 CBSE board rolls and 3 typos — fix typos and
    # re-submit, or proceed and skip them"). The wizard UI on
    # /admin/students/import calls dry_run=True first, then dry_run=False
    # once the teacher confirms.
    dry_run: bool = False


class CreateExamIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_title: str = "Exam"
    duration_minutes: int = 60
    phone_camera: bool = False


class UploadQuestionImageIn(BaseModel):
    model_config = ConfigDict(strict=True)
    question_id: str = ""
    data_url: str = ""


class SaveTemplateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    template_name: str
    include_questions: bool = True


class DuplicateExamIn(BaseModel):
    model_config = ConfigDict(strict=True)
    new_title: str = ""
