-- =====================================================================
-- phase126: per-teacher roll numbers (drop the global roll_number unique)
-- =====================================================================
-- `students` carried TWO uniqueness rules:
--   • students_roll_teacher_unique  UNIQUE (roll_number, teacher_id)  ← correct
--   • students_roll_number_key      UNIQUE (roll_number)              ← BUG
-- The second made roll numbers unique across the ENTIRE platform, so the
-- first tutor to use "1" blocked every other tutor from ever using "1", and
-- silently broke the second teacher's enrolment (the insert hit the global
-- constraint → the register path treated it as "returning" and created no
-- roster row under teacher B). It contradicts the multi-tenant model.
--
-- The application already scopes every roll lookup by teacher (register,
-- lobby, history, admin) and the exam-entry resolver denies an ambiguous
-- roll-only launch ("use your access code"), so removing the global rule is
-- safe. Roll numbers become a PER-TEACHER namespace; identity stays
-- (teacher_id, roll_number) for the roster row + email→student_accounts for
-- the cross-teacher person/login.
--
-- Idempotent. Keeps students_roll_teacher_unique untouched.
-- =====================================================================

BEGIN;

ALTER TABLE students DROP CONSTRAINT IF EXISTS students_roll_number_key;
-- Some older dumps materialised it as a bare unique index of the same name.
DROP INDEX IF EXISTS students_roll_number_key;

-- Safety: ensure the per-teacher composite still exists (no-op if present).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'students'::regclass AND conname = 'students_roll_teacher_unique'
  ) THEN
    ALTER TABLE students
      ADD CONSTRAINT students_roll_teacher_unique UNIQUE (roll_number, teacher_id);
  END IF;
END $$;

COMMIT;
