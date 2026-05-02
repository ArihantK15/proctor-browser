-- Phase 11: ensure ALL expected columns exist on `questions`
--
-- Various code paths (saveQuestions, bank-to-exam, _load_questions)
-- expect columns that older Supabase deployments may not have. The
-- legacy schema only had id/teacher_id/question/options/correct/
-- question_id; subsequent feature work added question_type (multi
-- vs single vs T/F) and image_url. Without those columns,
-- inserts fail with PGRST204 ("Could not find the 'X' column of
-- 'questions' in the schema cache").
--
-- This migration is the union of every column the application code
-- writes to questions. Idempotent — `add column if not exists`
-- means re-running is a no-op. Safe on existing data — text default
-- '' applies to new rows; existing rows get NULL which the app
-- already treats as "use default" via .get(col) or default patterns.
--
-- Supersedes migrations/phase11_questions_image_url.sql (which is
-- a strict subset of this). Running both is harmless.

alter table questions
  add column if not exists question_type text default 'mcq_single',
  add column if not exists image_url     text default '',
  add column if not exists tags          text[] default '{}',
  add column if not exists created_at    timestamptz default now(),
  add column if not exists updated_at    timestamptz default now();

-- Reload PostgREST's schema cache so new columns are queryable
-- without an API restart. This is the documented signal:
-- https://postgrest.org/en/stable/references/schema_cache.html
notify pgrst, 'reload schema';
