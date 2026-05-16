-- Refresh-token revocation table for local auth.
--
-- Stateless 30-day JWT refresh tokens are dangerous: a leaked token is
-- valid until natural expiry with no kill switch. This table gives every
-- refresh token a server-side row so we can:
--
--   - Revoke on logout / "sign out other devices"
--   - Detect replay (refresh that's already been rotated is rejected)
--   - Audit which IP/user-agent minted the chain
--
-- Tokens always carry their `jti` as a JWT claim. The refresh endpoint
-- looks up `jti` here, rejects if `revoked_at IS NOT NULL` or
-- `expires_at < now()`, then rotates: marks the old row revoked +
-- `replaced_by_jti = <new>`, inserts a new row for the freshly-minted
-- token.
--
-- Pattern follows the existing `auth_sessions` table (which tracks
-- ACCESS-token jtis). Different lifecycle so kept in a separate table.

CREATE TABLE IF NOT EXISTS refresh_tokens (
  jti              UUID PRIMARY KEY,
  user_id          TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('teacher', 'student')),
  issued_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at       TIMESTAMPTZ NOT NULL,
  revoked_at       TIMESTAMPTZ,
  replaced_by_jti  UUID,
  ip               TEXT,
  user_agent       TEXT
);

-- Hot path: "is this jti still good?" — looked up once per /refresh call.
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_active
  ON refresh_tokens (user_id, kind, revoked_at);

-- For "sign out other devices" bulk revoke + cleanup of expired rows.
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires
  ON refresh_tokens (expires_at)
  WHERE revoked_at IS NULL;
