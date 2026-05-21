-- Add `created_at` column to violations table.
--
-- Multiple code paths order by violations.created_at and several
-- indexes reference it (phase13_indexes_constraints.sql line 12,
-- phase55_dashboard_reporting_indexes.sql line 22 and 28). The column
-- was assumed to exist on the deployed Supabase schema (probably
-- auto-added when the table was first created via Supabase dashboard)
-- but no migration declares it, so the plain-Postgres backend rejects
-- every SELECT/INSERT that touches the column with:
--
--   asyncpg.exceptions.UndefinedColumnError: column "created_at" does not exist
--
-- Symptom in the field: heartbeat / exam_started / submit-exam all
-- 500 on the postgres backend whenever the handler eventually queries
-- violations (e.g. risk score, submission audit, time-exceeded check).
-- bulk_save kept working because it only touches the answers table.
--
-- This migration is idempotent (IF NOT EXISTS) so it's safe to re-run.
-- Existing rows get NULL → backfilled to NOW() so risk-score ordering
-- still works for legacy data.

ALTER TABLE violations
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- Backfill any pre-existing rows that landed with NULL (only possible
-- if the column was previously added without a default).
UPDATE violations SET created_at = now() WHERE created_at IS NULL;
