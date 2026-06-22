-- phase143: student RLS policies for coding_submissions.
--
-- coding_submissions is written by the STUDENT-authed judge endpoint
-- (/api/v1/coding/judge), but phase141 only gave it TEACHER-scoped policies
-- (teacher_id = app.teacher_id()). Under the live procta_app cutover a student's
-- session context has no app.teacher_id(), so the judge INSERT would be DENIED
-- → the judge 500s on prod. This is exactly how violations (also student-written)
-- is handled in phase124 (violations_s_ins): scope the student write to a session
-- the student owns. Mirror that pattern. INERT until the RLS cutover flag flips.
--
-- coding_test_cases needs NO student policy — students never SELECT it; delivery
-- is a system_context() read (app.is_privileged()), and hidden expected_output is
-- projected out of the query. So this migration only touches coding_submissions.
DO $$
BEGIN
  -- Student inserts their own submission (session must be one the student owns),
  -- mirroring violations_s_ins. The teacher_id on the row is stamped server-side
  -- from the JWT; the RLS check here is on SESSION ownership, not teacher_id.
  CREATE POLICY coding_submissions_s_ins ON coding_submissions FOR INSERT
    WITH CHECK (app.role() = 'student' AND session_id::text IN (
                 SELECT es.session_key::text FROM exam_sessions es
                  WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())));

  -- Student may read their OWN submissions (own session only) — parity with
  -- violations_s_sel; harmless and future-proofs a "your past attempts" view.
  CREATE POLICY coding_submissions_s_sel ON coding_submissions FOR SELECT
    USING (app.role() = 'student' AND session_id::text IN (
             SELECT es.session_key::text FROM exam_sessions es
              WHERE es.roll_number::text IN (SELECT app.my_roll_numbers())));
EXCEPTION
  WHEN duplicate_object THEN
    RAISE NOTICE 'phase143 skip: coding_submissions student policies exist';
  WHEN undefined_function OR undefined_table THEN
    RAISE NOTICE 'phase143 skip (app.* helpers / table not present): %', SQLERRM;
END $$;
