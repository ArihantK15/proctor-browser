-- =====================================================================
-- Phase 88 — Composite UNIQUE constraints on natural logical keys
-- =====================================================================
-- An inventory of pg_constraint showed 11 composite UNIQUE constraints
-- already in place (answers, exam_config, students, etc.). A dupe-group
-- audit on five additional candidates returned zero dupes for four of
-- them — safe to constrain immediately:
--
--   student_invites:  (teacher_id, lower(email), exam_id)
--   exam_templates:   (teacher_id, template_name)
--   org_invites:      (org_id, lower(email))      WHERE status='pending'
--   appeals:          (session_key, student_id, appeal_type) WHERE status='pending'
--
-- The fifth candidate (exam_sessions (teacher_id, exam_id, roll_number))
-- had 2 dupe groups in production data; deferred to a follow-up phase
-- after the dupes are inspected and cleaned.
--
-- Two of the four use functional / partial unique INDEXES (rather than
-- table-level CONSTRAINTS) because:
--   - case-insensitive uniqueness on email needs lower(email), which
--     ALTER TABLE ... ADD CONSTRAINT UNIQUE doesn't support.
--   - status='pending' is a partial predicate so we don't constrain
--     historical accepted/rejected rows that may have shared keys.
--
-- Idempotent via IF NOT EXISTS guards.
-- =====================================================================

-- ── student_invites — case-insensitive idempotency key ────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_student_invites_teacher_email_exam
  ON student_invites (teacher_id, lower(email), exam_id)
  WHERE email IS NOT NULL AND email <> '' AND exam_id IS NOT NULL;

-- ── exam_templates — one template name per teacher ────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'exam_templates_teacher_name_unique'
                    AND table_name = 'exam_templates') THEN
    ALTER TABLE exam_templates
      ADD CONSTRAINT exam_templates_teacher_name_unique
      UNIQUE (teacher_id, template_name);
  END IF;
END $$;

-- ── org_invites — one open invite per (org, email) ────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_invites_org_email_pending
  ON org_invites (org_id, lower(email))
  WHERE status = 'pending';

-- ── appeals — one open appeal per (session, student, type) ────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_appeals_session_student_type_pending
  ON appeals (session_key, student_id, appeal_type)
  WHERE status = 'pending';

-- =====================================================================
-- Post-migration verification:
--
--   -- A: confirm the 4 unique-things exist with the right keys
--   SELECT i.relname AS index_name, pg_get_indexdef(ix.indexrelid)
--     FROM pg_index ix
--     JOIN pg_class i ON i.oid = ix.indexrelid
--    WHERE ix.indisunique
--      AND i.relname IN (
--        'uq_student_invites_teacher_email_exam',
--        'exam_templates_teacher_name_unique',
--        'uq_org_invites_org_email_pending',
--        'uq_appeals_session_student_type_pending'
--      )
--    ORDER BY i.relname;
--
--   Expected: 4 rows.
--
-- =====================================================================
-- Follow-up tracked for next phase:
--   exam_sessions(teacher_id, exam_id, roll_number) UNIQUE — blocked
--   on 2 duplicate logical session groups in prod data. Inspect via
--   the audit query in step "First — inspect the 2 exam_sessions dupe
--   groups", decide retention policy (newer / one-with-answers / one-
--   with-terminal-status), DELETE the others, then add the constraint.
-- =====================================================================
