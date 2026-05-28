-- Phase 73: Dismissal columns on violations + supporting index
--
-- Cluster & Batch Review (per docs/STRATEGIC_AUDIT_2026-05-27 follow-up
-- and the external-AI review plan in
-- /Users/arihantkaul/.claude/plans/the-load-is-running-sparkling-canyon.md).
--
-- A teacher reviewing a 3,500-student exam can have ~175 false-positive
-- violations to triage. The new Cluster mode in ReviewPanel groups
-- violations by (exam, type, question) and lets the teacher bulk-
-- dismiss an entire cluster (e.g. "42 students flagged for off-screen
-- gaze during Question 12 — they were reading a printed diagram, all
-- dismiss").
--
-- We mark dismissed instead of deleting so the audit trail stays
-- intact and so a dismissed violation can be un-dismissed if needed.
-- `dismissed_at` is the timestamp; `dismissed_reason` is the optional
-- free-text justification.
--
-- Idempotent: safe to re-run.

ALTER TABLE violations
  ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ NULL;

ALTER TABLE violations
  ADD COLUMN IF NOT EXISTS dismissed_reason TEXT NULL;

-- Supports the cluster-aggregate query
-- (GROUP BY violation_type WHERE teacher_id = ? AND dismissed_at IS NULL).
-- Exam-scoping is layered on via a join through exam_sessions
-- (violations.session_key -> exam_sessions.session_key -> exam_id),
-- so the index stays narrow. Partial — we only query the not-yet-
-- dismissed slice.
CREATE INDEX IF NOT EXISTS idx_violations_active_clusters
  ON violations (teacher_id, violation_type)
  WHERE dismissed_at IS NULL;
