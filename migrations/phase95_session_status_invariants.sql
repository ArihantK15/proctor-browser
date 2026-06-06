-- phase95: exam_sessions status invariants (defense in depth)
--
-- Backs the single-source-of-truth status work in app code with DB-level
-- guards so no path (app bug OR manual SQL) can leave a session in an
-- impossible state. Added NOT VALID so they enforce on all NEW writes
-- immediately without failing the migration on any legacy rows; run the
-- VALIDATE step (bottom, commented) after a one-time cleanup if desired.

-- 1. status must be a known value.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'exam_sessions_status_known'
  ) THEN
    ALTER TABLE exam_sessions
      ADD CONSTRAINT exam_sessions_status_known
      CHECK (status IN (
        'in_progress','paused','completed','submitted',
        'force_submitted','abandoned','rejected'
      )) NOT VALID;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'phase95: status_known constraint skipped: %', SQLERRM;
END $$;

-- 2. A scored/result session must carry submitted_at (consistency the
--    reconciler also backfills). completed/force_submitted ⇒ submitted_at set.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'exam_sessions_result_has_submitted_at'
  ) THEN
    ALTER TABLE exam_sessions
      ADD CONSTRAINT exam_sessions_result_has_submitted_at
      CHECK (status NOT IN ('completed','force_submitted') OR submitted_at IS NOT NULL)
      NOT VALID;
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'phase95: result_has_submitted_at constraint skipped: %', SQLERRM;
END $$;

-- 3. Index for the teacher+status view queries (live/results/history).
CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_status
  ON exam_sessions (teacher_id, status);

-- ── Optional one-time VALIDATE (run after cleaning any legacy violators) ──
-- UPDATE exam_sessions SET submitted_at = COALESCE(submitted_at, now())
--   WHERE status IN ('completed','force_submitted') AND submitted_at IS NULL;
-- ALTER TABLE exam_sessions VALIDATE CONSTRAINT exam_sessions_status_known;
-- ALTER TABLE exam_sessions VALIDATE CONSTRAINT exam_sessions_result_has_submitted_at;
