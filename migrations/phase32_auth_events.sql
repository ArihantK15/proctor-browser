-- Persistent auth audit log — signup, login, password events, 2FA, sessions.

CREATE TABLE IF NOT EXISTS auth_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_kind     TEXT NOT NULL,        -- 'teacher' | 'student_account'
  user_id       TEXT,                 -- nullable for failed logins
  email         TEXT,                 -- captured for failed-login forensics
  event_type    TEXT NOT NULL,        -- signup, login_success, login_failed,
                                      -- password_reset_requested, password_reset_completed,
                                      -- email_verified, 2fa_enabled, 2fa_used, 2fa_failed,
                                      -- session_revoked, suspicious_login
  ip            TEXT,
  user_agent    TEXT,
  meta          JSONB DEFAULT '{}'::JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_events_user ON auth_events (user_kind, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_failed ON auth_events (email, created_at DESC) WHERE event_type = 'login_failed';
