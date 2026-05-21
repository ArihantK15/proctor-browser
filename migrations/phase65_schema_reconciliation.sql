-- Schema reconciliation: add every column the load-test code path expects
-- but might be missing on a plain-Postgres deployment.
--
-- BACKGROUND: This codebase was originally built against Supabase. Many
-- columns were added to tables via the Supabase dashboard rather than as
-- repo migrations, so they exist on the live Supabase schema but not on
-- fresh plain-Postgres deployments. asyncpg is strict about column
-- existence (PostgREST silently tolerated mismatches), so each missing
-- column produces a 500 the first time its code path runs.
--
-- Instead of iterating one column at a time (phase62..phase64 added one
-- each: teachers.status, violations.created_at, exam_config.created_at),
-- this migration is a single sweep of every column the
-- /api/v1/event, /api/v1/heartbeat, /api/v1/submit-exam, and async
-- scoring paths read or write.
--
-- All ADD COLUMN are IF NOT EXISTS so the migration is idempotent and
-- safe to re-run. Already-present columns are no-ops.
--
-- After this migration, the load-test sequence below should succeed
-- end-to-end on the postgres backend:
--   exam_started event  -> exam_sessions upsert
--   bulk_save loop      -> answers insert
--   heartbeat loop      -> exam_sessions update last_heartbeat
--   submit-exam         -> exam_config select, exam_sessions update,
--                          violations insert, risk_score compute

-- ── exam_sessions ────────────────────────────────────────────────
ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS status         TEXT,
  ADD COLUMN IF NOT EXISTS started_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS submitted_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS score          INT,
  ADD COLUMN IF NOT EXISTS total          INT,
  ADD COLUMN IF NOT EXISTS percentage     NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS risk_score     INT,
  ADD COLUMN IF NOT EXISTS time_taken_secs INT,
  ADD COLUMN IF NOT EXISTS full_name      TEXT,
  ADD COLUMN IF NOT EXISTS email          TEXT,
  ADD COLUMN IF NOT EXISTS roll_number    TEXT,
  ADD COLUMN IF NOT EXISTS teacher_id     TEXT,
  ADD COLUMN IF NOT EXISTS exam_id        TEXT,
  ADD COLUMN IF NOT EXISTS student_id     TEXT,
  ADD COLUMN IF NOT EXISTS created_at     TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at     TIMESTAMPTZ DEFAULT now();

-- ── exam_config ──────────────────────────────────────────────────
-- created_at was added in phase64; restate here for completeness so a
-- fresh deployment can apply phase65 alone and get a working schema.
ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS exam_title             TEXT DEFAULT 'Exam',
  ADD COLUMN IF NOT EXISTS duration_minutes       INT  DEFAULT 60,
  ADD COLUMN IF NOT EXISTS access_code            TEXT,
  ADD COLUMN IF NOT EXISTS starts_at              TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ends_at                TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS shuffle_questions      BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS shuffle_options        BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS phone_camera_enabled   BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS proctoring_sensitivity TEXT    DEFAULT 'balanced',
  ADD COLUMN IF NOT EXISTS created_at             TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at             TIMESTAMPTZ DEFAULT now();

-- ── violations ───────────────────────────────────────────────────
-- created_at was added in phase63; same completeness restatement.
ALTER TABLE violations
  ADD COLUMN IF NOT EXISTS session_key        TEXT,
  ADD COLUMN IF NOT EXISTS violation_type     TEXT,
  ADD COLUMN IF NOT EXISTS severity           TEXT,
  ADD COLUMN IF NOT EXISTS details            TEXT,
  ADD COLUMN IF NOT EXISTS teacher_id         TEXT,
  ADD COLUMN IF NOT EXISTS detection_confidence NUMERIC(4,3),
  ADD COLUMN IF NOT EXISTS created_at         TIMESTAMPTZ DEFAULT now();

-- Backfill any null created_at on the three tables so order-by queries
-- don't sort NULLs to the top.
UPDATE exam_sessions SET created_at = COALESCE(created_at, now()) WHERE created_at IS NULL;
UPDATE exam_config   SET created_at = COALESCE(created_at, updated_at, now()) WHERE created_at IS NULL;
UPDATE violations    SET created_at = COALESCE(created_at, now()) WHERE created_at IS NULL;
