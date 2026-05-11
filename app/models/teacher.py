from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class TeacherSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str
    org_name:  str = ""


class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str


class RefreshIn(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email: str
