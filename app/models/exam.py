from __future__ import annotations
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    # PAUSED is non-terminal: the student's UI is locked + their clock
    # is stopped (paused_at set on the row). Resume flips back to
    # IN_PROGRESS. Distinct from IN_PROGRESS so the dashboard live-
    # sessions panel can badge it and the resume endpoint can find
    # paused sessions cheaply via the idx_exam_sessions_currently_paused
    # partial index (phase74).
    PAUSED = "paused"
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


# ─── Live teacher intervention (phase 74) ────────────────────────────
#
# Three escalating teacher actions on a live exam session:
#   Warn     — sends a system_warning chat message (recoverable, no
#              state change on the session)
#   Pause    — locks the student UI and stops their timer (recoverable)
#   Terminate (extend force-submit) — closes the session permanently,
#              scores the student's answers as-is, records the reason

# Allowlist mirrored in renderer/index.html + dashboard-app.js so the
# student sees the same human label the teacher picked. Keep all three
# in sync when adding a code.
SESSION_END_REASON_CODES: tuple[str, ...] = (
    "academic_dishonesty",
    "identity_fraud",
    "environment_issue",
    "repeated_violations",
    "student_request",
    "technical_failure",
    "other",  # requires reason_text
)

# Allowlist for the warn modal's severity chips.
TEACHER_WARN_CHIP_CODES: tuple[str, ...] = (
    "eyes_off_screen",
    "phone_visible",
    "talking_to_someone",
    "multiple_tabs",
    "other",
)


class TeacherWarnIn(BaseModel):
    model_config = ConfigDict(strict=True)
    # Either chip_code or text (or both) must be present — empty warning
    # is rejected at the router. Both capped at sane sizes there too.
    chip_code: Optional[str] = None
    text:      Optional[str] = None


class SessionTerminateIn(BaseModel):
    """Extends the existing admin-submit body. reauth_token stays
    required (handled by require_reauth_or_403). reason_code +
    reason_text are validated against SESSION_END_REASON_CODES at the
    router and persisted on the exam_sessions row."""
    model_config = ConfigDict(strict=True)
    reauth_token: Optional[str] = None
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
    # When True (default), the endpoint ALSO mints invite tokens and
    # enqueues invite emails for every successfully-registered row —
    # closing the "I added them but they have no way to know" gap.
    # Set False to roster silently (e.g. test imports, or when invites
    # will be sent later via the Email Invites tool). Cap-aware:
    # respects the same daily-cap check the /invites/send route uses,
    # so a runaway bulk import can't blow through the limit. Idempotent
    # per (teacher_id, email, exam_id) — re-running an import never
    # double-sends.
    send_invites: bool = True
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
