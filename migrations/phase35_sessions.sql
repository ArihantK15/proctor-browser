-- Session revocation — JWT whitelist/revoke tracking.

CREATE TABLE IF NOT EXISTS auth_sessions (
  jti          UUID PRIMARY KEY,
  user_kind    TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip           TEXT,
  user_agent   TEXT,
  revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_kind, user_id, revoked_at, last_seen_at DESC);
