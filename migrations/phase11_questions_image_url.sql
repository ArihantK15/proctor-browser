-- Phase 11: ensure `questions.image_url` exists
--
-- The dashboard's question editor allows uploading an image per
-- question (see #qimg-N + handleQImageUpload in dashboard.html).
-- The bank-to-exam copy path also tries to carry image_url over
-- when copying a bank question into an exam. Both need the column
-- to exist in the questions table.
--
-- Without this migration, "Add Selected to Exam" fails with
-- PGRST204 ("Could not find the 'image_url' column of 'questions'
-- in the schema cache") on Supabase deployments where the column
-- was never added.
--
-- Idempotent: `if not exists` so running this twice is harmless.
-- Safe on existing data: text default '' applies to new rows only;
-- existing rows get NULL which the application code already treats
-- as "no image" (see _load_questions: q.get("image_url") or "").

alter table questions
  add column if not exists image_url text default '';

-- Reload PostgREST's schema cache so the new column is queryable
-- without restarting the API. NOTIFY is the documented way:
-- https://postgrest.org/en/stable/references/schema_cache.html
notify pgrst, 'reload schema';
