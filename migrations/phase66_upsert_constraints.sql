-- Add UNIQUE constraints on the columns used as ON CONFLICT targets in upserts.
--
-- Postgres requires a UNIQUE constraint (or primary key) on the conflict
-- column for `INSERT ... ON CONFLICT (col) DO UPDATE` to work. The
-- deployed Supabase schema has these via implicit primary keys and
-- dashboard-added constraints; the plain-Postgres deployment only has
-- non-unique indexes (e.g. phase13 created idx_exam_sessions_session_key
-- as a regular index, not unique).
--
-- Without these constraints, every upsert that reaches the asyncpg
-- backend fails with:
--   asyncpg.exceptions.UndefinedColumnError: column "id" does not exist
-- (because the adapter falls back to ON CONFLICT (id) which can't even
-- be parsed against tables that have no id column)
-- or:
--   asyncpg.exceptions.InvalidColumnReferenceError: there is no unique
--   or exclusion constraint matching the ON CONFLICT specification
-- (when the column exists but isn't unique).
--
-- All constraints use IF NOT EXISTS semantics via a DO block — Postgres
-- doesn't have ADD CONSTRAINT IF NOT EXISTS natively, so we check
-- pg_constraint and only ADD when absent. This makes the migration
-- safe to re-run.

-- ── exam_sessions(session_key) ───────────────────────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'exam_sessions_session_key_unique') THEN
    ALTER TABLE exam_sessions
      ADD CONSTRAINT exam_sessions_session_key_unique UNIQUE (session_key);
  END IF;
END $$;

-- ── answers(session_key, question_id) ────────────────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'answers_session_question_unique') THEN
    ALTER TABLE answers
      ADD CONSTRAINT answers_session_question_unique UNIQUE (session_key, question_id);
  END IF;
END $$;

-- ── questions(teacher_id, exam_id, question_id) ──────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'questions_teacher_exam_question_unique') THEN
    ALTER TABLE questions
      ADD CONSTRAINT questions_teacher_exam_question_unique UNIQUE (teacher_id, exam_id, question_id);
  END IF;
END $$;

-- ── exam_config(teacher_id, exam_id) ─────────────────────────────
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'exam_config_teacher_exam_unique') THEN
    ALTER TABLE exam_config
      ADD CONSTRAINT exam_config_teacher_exam_unique UNIQUE (teacher_id, exam_id);
  END IF;
END $$;
