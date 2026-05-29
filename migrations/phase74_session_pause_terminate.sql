-- Phase 74: Pause / resume + termination reason on exam_sessions
--
-- See /Users/arihantkaul/.claude/plans/the-load-is-running-sparkling-canyon.md
-- (Part B — Live teacher intervention).
--
-- Layered teacher intervention: Warn (chat) -> Pause (lock UI, stop
-- timer) -> End (terminate with reason). To make pause fair to the
-- student, we have to subtract the paused interval from
-- time_taken_secs at submission. To make termination defensible in a
-- later dispute (university grade challenge, parent escalation), we
-- have to persist WHO ended the exam, WHEN, and WHY.
--
-- Columns:
--   paused_at              currently-active pause window's start. NULL
--                          when the student is NOT paused. resume_at
--                          NOT stored — on resume we compute
--                          (now() - paused_at) and add to
--                          paused_secs_total, then null this field.
--   paused_secs_total      sum of all pause intervals this session. A
--                          student paused twice for 60s each carries
--                          120 here. INT so the subtraction in
--                          time_taken_secs math stays simple.
--   terminated_by          teacher full name or email at the moment of
--                          force-submit. Snapshotted (not FK) so a
--                          later teacher account deletion does not
--                          erase the audit trail.
--   termination_reason_code one of SESSION_END_REASON_CODES (app/models/
--                          exam.py). Allowlisted at the API layer.
--   termination_reason_text free-text from the teacher. Capped 500
--                          chars at write time. Required when
--                          reason_code == 'other'.
--
-- All new columns nullable / default-0 so existing rows back-compat
-- cleanly. No backfill needed.
--
-- Idempotent: safe to re-run.

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ NULL;

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS paused_secs_total INT NOT NULL DEFAULT 0;

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS terminated_by TEXT NULL;

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS termination_reason_code TEXT NULL;

ALTER TABLE exam_sessions
  ADD COLUMN IF NOT EXISTS termination_reason_text TEXT NULL;

-- Partial index for the "currently paused" lookup — used by the
-- resume endpoint to find sessions in mid-pause and by the dashboard
-- live-sessions panel to badge them. Narrow because most sessions
-- aren't paused.
CREATE INDEX IF NOT EXISTS idx_exam_sessions_currently_paused
  ON exam_sessions (teacher_id)
  WHERE paused_at IS NOT NULL;
