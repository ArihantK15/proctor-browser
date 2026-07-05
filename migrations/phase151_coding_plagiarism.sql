-- phase151: code plagiarism detection for the coding module.
--
-- coding_plagiarism_matches — one row per flagged same-exam, same-question,
--   same-language submission pair, produced by the Dolos-backed batch job.
--   teacher_id is stamped server-side (never client-supplied), same pattern
--   as coding_submissions (see phase141) so RLS is a direct app.teacher_id()
--   check.
-- coding_plagiarism_checks — tracks which exam_ids have already had a
--   plagiarism check run, so the periodic scheduler (main.py) doesn't
--   re-enqueue the same exam on every loop tick. One row per exam_id;
--   upserted after each run (manual re-run also updates it).
DO $$
BEGIN
  CREATE TABLE IF NOT EXISTS coding_plagiarism_matches (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id           TEXT NOT NULL,
    question_id       TEXT NOT NULL,
    teacher_id        UUID,
    submission_a_id   UUID NOT NULL REFERENCES coding_submissions(id) ON DELETE CASCADE,
    submission_b_id   UUID NOT NULL REFERENCES coding_submissions(id) ON DELETE CASCADE,
    student_a_id      UUID,
    student_b_id      UUID,
    similarity_score  DOUBLE PRECISION NOT NULL,
    matched_regions   JSONB,
    corroborated      BOOLEAN NOT NULL DEFAULT FALSE,
    status            TEXT NOT NULL DEFAULT 'unreviewed',   -- 'unreviewed' | 'confirmed' | 'dismissed'
    reviewed_by       UUID,
    reviewed_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_plagiarism_matches_exam
    ON coding_plagiarism_matches(exam_id, question_id);

  CREATE TABLE IF NOT EXISTS coding_plagiarism_checks (
    exam_id      TEXT PRIMARY KEY,
    teacher_id   UUID,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL DEFAULT 'ok'   -- 'ok' | 'failed'
  );
EXCEPTION WHEN duplicate_table THEN
  RAISE NOTICE 'phase151 skip: coding plagiarism tables exist';
END $$;

-- RLS — phase124 app.* model, mirrors coding_submissions exactly (phase141).
DO $$
BEGIN
  PERFORM app._drop_all_policies('coding_plagiarism_matches'::regclass);
  ALTER TABLE coding_plagiarism_matches ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_plagiarism_matches_sel ON coding_plagiarism_matches FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_plagiarism_matches_ins ON coding_plagiarism_matches FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_matches_upd ON coding_plagiarism_matches FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_matches_del ON coding_plagiarism_matches FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());

  PERFORM app._drop_all_policies('coding_plagiarism_checks'::regclass);
  ALTER TABLE coding_plagiarism_checks ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_plagiarism_checks_sel ON coding_plagiarism_checks FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_plagiarism_checks_ins ON coding_plagiarism_checks FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_checks_upd ON coding_plagiarism_checks FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
EXCEPTION WHEN undefined_function OR undefined_table THEN
  RAISE NOTICE 'phase151 RLS skip (app.* helpers not present yet): %', SQLERRM;
END $$;
