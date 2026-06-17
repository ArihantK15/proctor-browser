-- Reverse of phase126: restore the global students_roll_number_key UNIQUE.
--
-- WARNING: this only succeeds if roll_number is still globally unique. Once
-- two teachers share a roll number (the whole point of phase126) this ADD
-- CONSTRAINT will fail — that is expected; the forward migration is a genuine
-- contract and is only reversible on a DB that hasn't yet relied on the new
-- per-teacher behaviour.
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'students'::regclass AND conname = 'students_roll_number_key'
  ) THEN
    ALTER TABLE students ADD CONSTRAINT students_roll_number_key UNIQUE (roll_number);
  END IF;
END $$;

COMMIT;
