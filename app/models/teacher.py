from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict


class TeacherSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str
    org_name:  str = ""
    # Account type chosen at signup (see docs/superpowers/specs/
    # 2026-06-20-account-types-solo-vs-org-design.md):
    #   'solo' → 1-person org, org_role='teacher', owns billing.
    #   'org'  → manager-only org_role='admin', owns billing, no exam authoring.
    # Defaults to 'solo' so existing/scripted callers keep the prior behaviour.
    account_type: Literal["solo", "org"] = "solo"
    # Turnstile CAPTCHA token. Optional in the schema so existing
    # callers (tests, scripted clients) keep working; the handler
    # falls back to sandbox-allow when the server-side secret isn't
    # configured. In production both halves must be present.
    captcha_token: str | None = None


class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str
    captcha_token: str | None = None
    # 2FA code from the email-delivered OTP (see app/services/email_otp.py
    # + app/emailer.py:send_2fa_otp_email). Replaced the previous
    # `totp_code` field when TOTP was retired 2026-05-23.
    email_otp_code: str | None = None


class RefreshIn(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str = ""


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email: str
    captcha_token: str | None = None
