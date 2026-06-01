from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class RegisterIn(BaseModel):
    model_config = ConfigDict(strict=True)
    full_name:   str
    roll_number: str
    email:       str
    phone:       Optional[str] = None
    teacher_id:  Optional[str] = None
    # Optional exam-scoping: when present, the registration link
    # encoded a specific exam (?t=<tid>&e=<eid>) and the row is
    # stored with exam_id set so the student sees that specific
    # exam in their lobby rather than whatever exam_config happens
    # to be first for the teacher.
    exam_id:     Optional[str] = None


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
