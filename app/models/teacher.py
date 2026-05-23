from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class TeacherSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str
    org_name:  str = ""
    # Turnstile CAPTCHA token. Optional in the schema so existing
    # callers (tests, scripted clients) keep working; the handler
    # falls back to sandbox-allow when the server-side secret isn't
    # configured. In production both halves must be present.
    captcha_token: Optional[str] = None


class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str
    captcha_token: Optional[str] = None
    # 2FA code from the email-delivered OTP (see app/services/email_otp.py
    # + app/emailer.py:send_2fa_otp_email). Replaced the previous
    # `totp_code` field when TOTP was retired 2026-05-23.
    email_otp_code: Optional[str] = None


class RefreshIn(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email: str
    captcha_token: Optional[str] = None
