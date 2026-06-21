-- phase141: Edge Compiler coding-assessment tables.
--
-- NOTE on numbering: phase139/phase140 are RESERVED by the parked
-- `feat/rough-sheet-config-foundation` branch (not yet on main). This coding
-- work starts at phase141 deliberately so the two feature branches never collide
-- on a phase number when both eventually merge. Do NOT reuse 139/140 here.
--
-- coding_test_cases  — server-authoritative test cases. expected_output for HIDDEN
--                      cases is NEVER serialized to the student (query-layer
--                      invariant; students get NO RLS select — delivery is a
--                      system-context read that projects expected_output out).
-- coding_submissions — per-(student,question) judged result + telemetry. teacher_id
--                      is a REAL column, stamped server-side from the JWT, so RLS is
--                      a direct app.teacher_id() check and the offboarding guard can
--                      categorize it. See the spec's Anti-cheat + Compliance sections.
DO $$
BEGIN
  CREATE TABLE IF NOT EXISTS coding_test_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id     TEXT NOT NULL,
    teacher_id      UUID,
    idx             INTEGER NOT NULL,
    input           TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'hidden',   -- 'sample' | 'hidden'
    float_tolerance DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_test_cases_q ON coding_test_cases(question_id, idx);

  CREATE TABLE IF NOT EXISTS coding_submissions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id                   TEXT,
    teacher_id                UUID,
    session_id                TEXT,
    student_id                UUID,
    question_id               TEXT,
    language                  TEXT,
    test_cases_total          INTEGER,
    test_cases_passed         INTEGER,
    is_fully_solved           BOOLEAN GENERATED ALWAYS AS
                                (test_cases_total > 0 AND test_cases_passed = test_cases_total) STORED,
    average_execution_ms      INTEGER,
    memory_consumed_kb        INTEGER,
    source_code               TEXT,
    keystroke_rhythm_variance DOUBLE PRECISION,
    paste_attempts            INTEGER DEFAULT 0,
    focus_loss_count          INTEGER DEFAULT 0,
    submitted_at              TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_submissions_exam_student
    ON coding_submissions(exam_id, student_id);
  -- Drives the per-question submit-attempt cap (the output-oracle defense).
  CREATE INDEX IF NOT EXISTS coding_submissions_attempts
    ON coding_submissions(session_id, question_id);
EXCEPTION WHEN duplicate_table THEN
  RAISE NOTICE 'phase141 skip: coding tables exist';
END $$;

-- RLS — phase124 app.* model EXACTLY (mirrors phase137). Inert until the cutover
-- flag flips (app connects as the table owner today, so policies don't gate yet).
DO $$
BEGIN
  -- coding_submissions: direct teacher_id scoping (own-or-org read, own write).
  PERFORM app._drop_all_policies('coding_submissions'::regclass);
  ALTER TABLE coding_submissions ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_submissions_sel ON coding_submissions FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_submissions_ins ON coding_submissions FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_submissions_upd ON coding_submissions FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_submissions_del ON coding_submissions FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());

  -- coding_test_cases: teacher_id scoping for AUTHORING; the STUDENT role gets no
  -- grant here (policy-less for non-privileged non-owner = deny-all at cutover),
  -- so a student can never SELECT expected_output. Test-case DELIVERY to students
  -- is a server-side system_context() read (is_privileged()) that projects hidden
  -- expected_output OUT of the column list — see app/routers/coding.py.
  PERFORM app._drop_all_policies('coding_test_cases'::regclass);
  ALTER TABLE coding_test_cases ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_test_cases_sel ON coding_test_cases FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_test_cases_ins ON coding_test_cases FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_test_cases_upd ON coding_test_cases FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_test_cases_del ON coding_test_cases FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
EXCEPTION WHEN undefined_function OR undefined_table THEN
  RAISE NOTICE 'phase141 RLS skip (app.* helpers not present yet): %', SQLERRM;
END $$;
