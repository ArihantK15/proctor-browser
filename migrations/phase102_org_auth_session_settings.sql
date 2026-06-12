-- Phase 102: per-org auth session settings.
--
-- Two optional org-level knobs for auth session management:
--
--   auth_session_timeout_minutes
--     Idle timeout for auth sessions. When set (non-null), any auth
--     session whose last_seen_at is older than this many minutes is
--     automatically revoked. NULL = disabled (default). Recommended
--     value when enabled: 30 minutes.
--
--   max_concurrent_auth_sessions
--     Maximum number of active (non-revoked) auth sessions a single
--     user may have at once. When exceeded at login time, the oldest
--     sessions are evicted. NULL = disabled (default). Recommended
--     value when enabled: 5.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS auth_session_timeout_minutes INTEGER,
  ADD COLUMN IF NOT EXISTS max_concurrent_auth_sessions INTEGER;
