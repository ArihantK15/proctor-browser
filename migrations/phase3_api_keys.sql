-- Create api_keys table for programmatic REST API access.
-- Each teacher can create multiple keys, identified by hash (never stored raw).

CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id  UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,                          -- human label (e.g. "CI pipeline")
    key_hash    TEXT NOT NULL UNIQUE,                   -- SHA-256 of the raw key
    key_prefix  TEXT NOT NULL,                          -- "pk_...abc12345"
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    CONSTRAINT  uq_teacher_keyname UNIQUE (teacher_id, name)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_teacher ON api_keys (teacher_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash    ON api_keys (key_hash);
