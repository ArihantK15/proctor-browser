-- phase49_exam_sessions_student_id.sql
--
-- Prerequisite for phase52_backfill_student_id.
--
-- Application code (commit 82b4792) began writing exam_sessions.student_id
-- before a migration created the column. This ALTER closes that gap so
-- the backfill in phase52 has something to populate.
--
-- Idempotent: IF NOT EXISTS guards re-runs.

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS student_id TEXT;

-- Helpful index for the per-student session lookups the app already does.
-- (phase53_indexes_perf also defines idx_exam_sessions_student_id with
-- IF NOT EXISTS, so this is a no-op when phase53 runs first; included
-- here so phase49 stands alone if applied in isolation.)
CREATE INDEX IF NOT EXISTS idx_exam_sessions_student_id
  ON exam_sessions (student_id);
