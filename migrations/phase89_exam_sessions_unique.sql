-- =====================================================================
-- Phase 89 — exam_sessions (teacher_id, exam_id, roll_number) UNIQUE
-- =====================================================================
-- phase88 added 4 composite UNIQUEs but deferred exam_sessions because
-- 2 LOADTEST dupe groups (4 rows) blocked the constraint. Those rows
-- have been deleted; the audit confirms zero remaining logical dupes,
-- so the constraint can land now.
--
-- Semantics: a student (identified by roll_number under a specific
-- teacher) can have at most one session row per exam. Retries / replays
-- under network failure that would otherwise insert a duplicate logical
-- session are now rejected at the DB layer with a UNIQUE violation.
--
-- Partial index because:
--   - roll_number can be empty string for anonymous practice sessions
--     that we deliberately allow to duplicate.
--   - older rows in production may have NULL teacher_id from a path
--     that pre-dates strict tenancy stamping (already audited; the
--     remaining set passes this filter).
-- =====================================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_exam_sessions_teacher_exam_roll
  ON exam_sessions (teacher_id, exam_id, roll_number)
  WHERE teacher_id IS NOT NULL
    AND exam_id IS NOT NULL
    AND roll_number IS NOT NULL
    AND roll_number <> '';

-- =====================================================================
-- Post-migration verification:
--
--   SELECT i.relname, pg_get_indexdef(ix.indexrelid)
--     FROM pg_index ix
--     JOIN pg_class i ON i.oid = ix.indexrelid
--    WHERE i.relname = 'uq_exam_sessions_teacher_exam_roll';
--
-- Expected: 1 row with a CREATE UNIQUE INDEX ... WHERE definition.
-- =====================================================================
