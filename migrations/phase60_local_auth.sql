-- Local auth foundation for plain-Postgres migration.
--
-- These columns let Procta authenticate teachers and student dashboard
-- accounts without Supabase Auth. `supabase_uid` stays in place for
-- compatibility during migration; local-auth signups fill it with an
-- app-generated UUID until the column can be renamed/relaxed later.

ALTER TABLE teachers
  ADD COLUMN IF NOT EXISTS password_hash TEXT,
  ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'supabase',
  ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

ALTER TABLE student_accounts
  ADD COLUMN IF NOT EXISTS password_hash TEXT,
  ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'supabase',
  ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_teachers_auth_provider ON teachers(auth_provider);
CREATE INDEX IF NOT EXISTS idx_student_accounts_auth_provider ON student_accounts(auth_provider);
