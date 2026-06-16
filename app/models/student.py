from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    # max_length on every field: this is an UNAUTHENTICATED public endpoint, so
    # without caps a caller can persist arbitrarily large strings (storage abuse
    # / oversized rows / DoS). Limits are generous but bounded.
    model_config = ConfigDict(strict=True)
    full_name:   str = Field(max_length=100)
    roll_number: str = Field(max_length=64)
    email:       str = Field(max_length=254)   # RFC 5321 max address length
    phone:       Optional[str] = Field(default=None, max_length=32)
    teacher_id:  Optional[str] = Field(default=None, max_length=64)
    # Optional exam-scoping: when present, the registration link
    # encoded a specific exam (?t=<tid>&e=<eid>) and the row is
    # stored with exam_id set so the student sees that specific
    # exam in their lobby rather than whatever exam_config happens
    # to be first for the teacher.
    exam_id:     Optional[str] = Field(default=None, max_length=64)
    # Cohort/batch (gap #59): when the link is a cohort-enrollment link
    # (?t=<tid>&b=<batch>) the registrant is stamped with this batch, giving
    # standing access to any exam later assigned to that cohort.
    batch:       Optional[str] = Field(default=None, max_length=120)
    # Date of birth for minor consent gate (GDPR Art 8 / COPPA).
    # When age < 18 the system requires guardian_email and auto-sends
    # a consent request to the parent/guardian.
    date_of_birth: Optional[str] = Field(default=None, max_length=10)  # YYYY-MM-DD
    guardian_email: Optional[str] = Field(default=None, max_length=254)


class ValidateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    roll_number: str
    access_code: Optional[str] = None
    exam_id: Optional[str] = None
    teacher_id: Optional[str] = None


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


class StudentSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str
    # P2.8: backend now gates student signup behind Turnstile too
    # (matches teacher signup + every other auth endpoint). Optional
    # so existing clients that don't yet send a token continue to work
    # in sandbox; production with TURNSTILE_SECRET_KEY set rejects on
    # verify_or_403 when missing.
    captcha_token: Optional[str] = None


class StudentLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str
    captcha_token: Optional[str] = None


class BulkStudentIn(BaseModel):
    model_config = ConfigDict(strict=True)
    students: list[dict[str, str]]
