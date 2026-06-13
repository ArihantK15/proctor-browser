-- Phase 111: per-org MFA enforcement.
--
--   require_2fa
--     When TRUE, every teacher/admin in the org must pass an email-OTP
--     2FA challenge at login, regardless of whether they individually
--     enabled 2FA (`teachers.email_2fa_enabled_at`). Enforced in the
--     teacher login flow (app/routers/auth.py). FALSE = opt-in per user
--     (default, unchanged behaviour). No per-user secret is needed
--     because Procta's 2FA is email-OTP, so org-wide enforcement just
--     means "always run the OTP step".

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS require_2fa BOOLEAN NOT NULL DEFAULT FALSE;
