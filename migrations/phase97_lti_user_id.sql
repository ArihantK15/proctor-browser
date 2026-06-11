-- Phase 97: LTI user-linking columns (students.lti_user_id, teachers.lti_user_id).
--
-- BUG: the LTI 1.3 code links student/teacher records to their LMS identity via
-- an `lti_user_id` column, and reads/writes it across:
--   • app/lti/launch.py      — teacher lookup/create (542,556), student lookup/
--                              create (568,577) on every LTI launch
--   • app/lti/nrps.py        — roster sync select + insert (123,139)
--   • app/routers/exam.py    — AGS grade-passback lookup (1024)
-- …but no migration ever added the column. In prod those queries raise
-- UndefinedColumnError, which the surrounding try/except swallows — so LTI
-- user-linking, NRPS roster provisioning, and AGS grade passback fail SILENTLY.
--
-- Fix is additive / expand-contract safe: add the columns (idempotent) and a
-- partial index for the `.eq("lti_user_id", …)` lookups (hot on every launch).
-- Old code tolerates the new columns, so a deploy rollback is safe.
--
-- AFTER this applies in prod: refresh schema/columns.json (scripts/dump_schema.py)
-- and remove the (students|teachers, lti_user_id) entries from
-- scripts/check_schema_refs.py IGNORE_REFS so the guard re-arms for them.

ALTER TABLE students ADD COLUMN IF NOT EXISTS lti_user_id TEXT;
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS lti_user_id TEXT;

-- Launch/NRPS filter by lti_user_id (often together with teacher_id); a partial
-- index keeps the lookup fast without bloating rows that never use LTI.
CREATE INDEX IF NOT EXISTS idx_students_lti_user_id
  ON students (lti_user_id) WHERE lti_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_teachers_lti_user_id
  ON teachers (lti_user_id) WHERE lti_user_id IS NOT NULL;
