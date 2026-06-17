-- =====================================================================
-- phase125: RLS student-read policies (completes phase124 for the
--           student lobby + exam-load path)
-- =====================================================================
-- phase124 enabled RLS + teacher/org/student-write policies, but never gave
-- the STUDENT role a *read* path to the teacher-owned reference tables the
-- lobby and exam-take flow need (exam_config, questions, invites, the
-- teacher row, batch/extension). Under RLS_SESSION_CONTEXT=1 that emptied the
-- student lobby (the 2026-06-17 incident). This migration adds exactly those
-- SELECT policies, scoped to the student's own enrolment.
--
-- Separate file (not an edit of phase124) because phase124 is already in
-- schema_migrations on prod — the runner skips applied files, so the policies
-- must ship as a new phase to actually land. Fully idempotent (DROP POLICY IF
-- EXISTS then CREATE) so it is safe to re-run.
--
-- Writes on these tables stay teacher/system-scoped. The server-side
-- reconciliation writes a student legitimately triggers (roster auto-link,
-- email propagation, exam-start auto-enroll, account deletion) run under
-- app.set_system_context() in the application layer, NOT via student policies.
-- =====================================================================

BEGIN;

-- Student -> the teacher_ids they're enrolled with (one per teacher whose
-- roster holds this account). SECURITY DEFINER so it resolves over the full
-- students table without being re-filtered by students' own RLS (and without
-- recursing). Owned by the migration role (bypasses RLS to compute mapping).
CREATE OR REPLACE FUNCTION app.my_teacher_ids() RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT DISTINCT s.teacher_id::text FROM students s
   WHERE app.account_id() IS NOT NULL
     AND s.account_id::text = app.account_id()
$$;

-- student_invites: the student's own per-exam roster rows. Scoped to BOTH the
-- enrolled teacher(s) AND the student's roll numbers so a roll-number collision
-- across tenants can't leak another teacher's invite.
DROP POLICY IF EXISTS student_invites_s_sel ON student_invites;
CREATE POLICY student_invites_s_sel ON student_invites FOR SELECT
  USING (app.role() = 'student'
         AND teacher_id::text IN (SELECT app.my_teacher_ids())
         AND roll_number::text IN (SELECT app.my_roll_numbers()));

-- teachers: the student may read the row of teachers they're enrolled with
-- (lobby shows the teacher's name on each exam card).
DROP POLICY IF EXISTS teachers_s_sel ON teachers;
CREATE POLICY teachers_s_sel ON teachers FOR SELECT
  USING (app.role() = 'student' AND id::text IN (SELECT app.my_teacher_ids()));

-- teacher_id-scoped reference tables: the exams/questions/cohort-assignments/
-- extensions of the teachers the student is enrolled with. Guarded per-table
-- (some are optional in older schemas).
DO $$
DECLARE t text;
  tabs text[] := ARRAY['exam_config','questions','exam_batch_assignments','exam_time_extensions'];
BEGIN
  FOREACH t IN ARRAY tabs LOOP
    BEGIN
      EXECUTE format('DROP POLICY IF EXISTS %1$s_s_sel ON %1$I', t);
      EXECUTE format($q$CREATE POLICY %1$s_s_sel ON %1$I FOR SELECT
        USING (app.role() = 'student' AND teacher_id::text IN (SELECT app.my_teacher_ids()))$q$, t);
    EXCEPTION WHEN undefined_table OR undefined_column THEN
      RAISE NOTICE 'phase125 skip student-read %: %', t, SQLERRM;
    END;
  END LOOP;
END $$;

COMMIT;
