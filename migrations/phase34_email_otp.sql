-- Email OTP service — one-time 6-digit codes for 2FA fallback, step-up, recovery.

CREATE TABLE IF NOT EXISTS email_otps (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_kind    TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  purpose      TEXT NOT NULL,         -- '2fa_fallback' | 'step_up' | 'recovery'
  code_hash    TEXT NOT NULL,         -- bcrypt of 6-digit code
  expires_at   TIMESTAMPTZ NOT NULL,
  attempts     INT DEFAULT 0,
  used_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_email_otps_lookup ON email_otps (user_kind, user_id, purpose, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_otps_expires ON email_otps (expires_at) WHERE used_at IS NULL;
