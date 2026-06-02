-- =====================================================================
-- Phase 84 — Indexes on FK source columns
-- =====================================================================
-- Postgres auto-indexes the *referenced* column on a FK (it's almost
-- always a primary key) but does NOT auto-index the *referencing*
-- column. Without that index, parent deletes and reverse joins have to
-- seq-scan the child table.
--
-- A diagnostic query against pg_constraint + pg_index found 8 FK
-- source columns with no leading-column index:
--
--   appeals.session_key                         (FK → exam_sessions)
--   exam_group_assignments.group_id             (FK → student_groups)
--   exam_group_assignments.teacher_id           (FK → teachers; added phase82)
--   google_oauth_states.teacher_id              (FK → teachers; added phase82)
--   grading_audit.session_key                   (FK → exam_sessions; added phase81)
--   org_invites.invited_by                      (FK → teachers; pre-existing)
--   student_group_members.teacher_id            (FK → teachers; added phase82)
--   student_invites.group_id                    (FK → student_groups)
--
-- This migration adds an index on each. Plain CREATE INDEX (not
-- CONCURRENTLY) because the migration runner wraps each file in a
-- transaction and CONCURRENTLY can't run in a transaction. The lock is
-- brief (ACCESS EXCLUSIVE) and these tables are small enough that a
-- sub-second window is acceptable; if any one of them ever grows past
-- the comfort threshold, recreate it with CONCURRENTLY outside the
-- runner.
--
-- IF NOT EXISTS makes the migration idempotent — re-running is safe.
-- =====================================================================

CREATE INDEX IF NOT EXISTS idx_appeals_session_key
  ON appeals (session_key);

CREATE INDEX IF NOT EXISTS idx_exam_group_assignments_group_id
  ON exam_group_assignments (group_id);

CREATE INDEX IF NOT EXISTS idx_exam_group_assignments_teacher_id
  ON exam_group_assignments (teacher_id);

CREATE INDEX IF NOT EXISTS idx_google_oauth_states_teacher_id
  ON google_oauth_states (teacher_id);

CREATE INDEX IF NOT EXISTS idx_grading_audit_session_key
  ON grading_audit (session_key);

CREATE INDEX IF NOT EXISTS idx_org_invites_invited_by
  ON org_invites (invited_by);

CREATE INDEX IF NOT EXISTS idx_student_group_members_teacher_id
  ON student_group_members (teacher_id);

CREATE INDEX IF NOT EXISTS idx_student_invites_group_id
  ON student_invites (group_id);

-- =====================================================================
-- Post-migration verification — re-run the FK-without-index diagnostic
-- and confirm it returns zero rows:
--
--   SELECT c.conname, conrelid::regclass AS table_name, a.attname AS column_name
--     FROM pg_constraint c
--     JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
--    WHERE c.contype = 'f'
--      AND conrelid::regclass::text NOT LIKE 'pg_%'
--      AND NOT EXISTS (
--        SELECT 1 FROM pg_index i
--         WHERE i.indrelid = c.conrelid AND i.indkey[0] = a.attnum
--      )
--    ORDER BY conrelid::regclass::text, c.conname;
--
-- Expected: 0 rows.
-- =====================================================================
