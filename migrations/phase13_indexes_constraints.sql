-- Phase 13: Performance indexes & unique constraints
-- Run this migration after deploying the code changes.

-- ─── Performance indexes ────────────────────────────────────────
-- Hot-path queries do full table scans without these.

CREATE INDEX IF NOT EXISTS idx_exam_sessions_session_key ON exam_sessions(session_key);
CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_id ON exam_sessions(teacher_id);
CREATE INDEX IF NOT EXISTS idx_exam_sessions_roll_number ON exam_sessions(roll_number);
CREATE INDEX IF NOT EXISTS idx_violations_session_key ON violations(session_key);
CREATE INDEX IF NOT EXISTS idx_violations_teacher_id ON violations(teacher_id);
CREATE INDEX IF NOT EXISTS idx_violations_created_at ON violations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_answers_session_key ON answers(session_key);
CREATE INDEX IF NOT EXISTS idx_students_roll_number ON students(roll_number);
CREATE INDEX IF NOT EXISTS idx_students_email ON students(email);
CREATE INDEX IF NOT EXISTS idx_student_accounts_email ON student_accounts(email);
CREATE INDEX IF NOT EXISTS idx_student_invites_token ON student_invites(token);
CREATE INDEX IF NOT EXISTS idx_student_invites_teacher_exam ON student_invites(teacher_id, exam_id);
CREATE INDEX IF NOT EXISTS idx_student_invites_roll_number ON student_invites(roll_number);
CREATE INDEX IF NOT EXISTS idx_exam_config_teacher_id ON exam_config(teacher_id);
CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);
CREATE INDEX IF NOT EXISTS idx_teachers_supabase_uid ON teachers(supabase_uid);

-- ─── Unique constraints ─────────────────────────────────────────
-- These enforce data integrity at the DB level (defense against races).

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'students_roll_teacher_unique'
  ) THEN
    ALTER TABLE students ADD CONSTRAINT students_roll_teacher_unique
      UNIQUE (roll_number, teacher_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'exam_config_teacher_exam_unique'
  ) THEN
    ALTER TABLE exam_config ADD CONSTRAINT exam_config_teacher_exam_unique
      UNIQUE (teacher_id, exam_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'teachers_email_unique'
  ) THEN
    ALTER TABLE teachers ADD CONSTRAINT teachers_email_unique
      UNIQUE (email);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'teachers_supabase_uid_unique'
  ) THEN
    ALTER TABLE teachers ADD CONSTRAINT teachers_supabase_uid_unique
      UNIQUE (supabase_uid);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'student_accounts_email_unique'
  ) THEN
    ALTER TABLE student_accounts ADD CONSTRAINT student_accounts_email_unique
      UNIQUE (email);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'student_accounts_supabase_uid_unique'
  ) THEN
    ALTER TABLE student_accounts ADD CONSTRAINT student_accounts_supabase_uid_unique
      UNIQUE (supabase_uid);
  END IF;
END $$;
