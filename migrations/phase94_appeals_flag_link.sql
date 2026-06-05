-- =====================================================================
-- Phase 94 — Flag-linked appeals + remediation + escalation affordance
-- =====================================================================
-- Closes the due-process loop. Today appeals (phase51) are session-level
-- and resolution is a dead end: accepting an appeal stamps a status but
-- does NOT dismiss the disputed violation, leaves no audit trail, and the
-- student is never notified. This migration adds the data model needed to
-- make a dispute point at a specific flag and make "accepted" actually
-- correct the record.
--
--   violation_id — WHICH flag the student is disputing. NULLABLE so the
--                  existing session-level appeal path (whole-session
--                  grade/other disputes) keeps working unchanged. When
--                  set, accepting the appeal dismisses exactly this flag
--                  (dismissed_reason='appeal_accepted') and recomputes
--                  the session risk score.
--   resolution   — Machine-readable remediation summary distinct from the
--                  free-text teacher_note, e.g. 'flag_dismissed'. Lets the
--                  student UI show "this flag was removed" deterministically.
--   escalated_to / escalated_at — RESERVED affordance for a future neutral
--                  second-reviewer path (orgs with >1 teacher). No endpoint
--                  or UI reads/writes these yet; columns exist so the
--                  escalation feature is a pure additive change later.
--
-- Idempotent: safe to re-run.
-- =====================================================================

ALTER TABLE appeals
  ADD COLUMN IF NOT EXISTS violation_id UUID NULL;

ALTER TABLE appeals
  ADD COLUMN IF NOT EXISTS resolution TEXT NULL;

ALTER TABLE appeals
  ADD COLUMN IF NOT EXISTS escalated_to TEXT NULL;

ALTER TABLE appeals
  ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ NULL;

-- "Show me appeals against this specific flag" — narrow partial index;
-- the vast majority of appeals are session-level (violation_id NULL) and
-- don't belong in this index.
CREATE INDEX IF NOT EXISTS idx_appeals_violation
  ON appeals (violation_id)
  WHERE violation_id IS NOT NULL;

-- =====================================================================
-- Verification:
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--    WHERE table_name='appeals'
--      AND column_name IN ('violation_id','resolution','escalated_to','escalated_at');
--   -- Expected: 4 rows, all is_nullable = YES
-- =====================================================================
