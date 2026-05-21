-- Add `created_at` column to exam_config table.
--
-- app/repositories/questions.py:_EXAM_CONFIG_COLUMNS selects
-- `created_at` as part of the exam_config row fetch. The deployed
-- Supabase schema has it (asyncpg's HINT explicitly mentions
-- `exam_config.updated_at` exists but not created_at, suggesting only
-- updated_at was ever added to the postgres-backed deployment).
--
-- Symptom: every /api/v1/submit-exam and /api/v1/event call that
-- triggers load_exam_config() returns 500 with:
--
--   asyncpg.exceptions.UndefinedColumnError: column "created_at" does not exist
--   HINT: Perhaps you meant to reference the column "exam_config.updated_at".
--
-- This was the bug behind the previous load test's heartbeat / exam_start /
-- submit failures (which all triggered load_exam_config indirectly).
--
-- Idempotent (IF NOT EXISTS) so safe to re-run. Backfill any pre-existing
-- rows so order-by-created_at queries don't sort NULLs to the top.

ALTER TABLE exam_config
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

UPDATE exam_config SET created_at = COALESCE(updated_at, now())
  WHERE created_at IS NULL;
