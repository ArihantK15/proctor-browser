-- =====================================================================
-- Phase 85 — CHECK constraints on enum-shaped TEXT columns
-- =====================================================================
-- Several TEXT columns across the schema accept only a small fixed set
-- of values — the contract is enforced in Python (Pydantic models +
-- app/constants.py + StrEnum) but not at the DB level. A typo'd
-- write (or a future code regression that drops a validator) silently
-- corrupts dashboards.
--
-- This migration adds CHECK constraints derived from:
--   - app/models/exam.py        (SessionStatus)
--   - app/models/billing.py     (SubscriptionStatus, PlanTier)
--   - app/constants.py          (ISSUE_CATEGORIES/SEVERITIES/STATUSES)
--   - phase50_privacy.sql       (consent_type comment)
--   - phase40_grading_audit.sql (action + ai_confidence comments)
--   - phase20_organizations.sql (org_role usage)
--
-- All actual production values were audited and are subsets of these
-- enums, so plain CHECK (no NOT VALID) works — the constraint validates
-- against existing rows in real-time. Sub-second locks on these tables.
--
-- Intentionally skipped:
--   - teachers.status: 4 legacy rows have '' which needs a separate
--     data-cleanup migration before the CHECK can be added.
--   - violations.severity / violations.violation_type: enum sets too
--     broad and partially computed in services/risk.py — not safe to
--     hardcode at the DB level yet.
--   - appeals.appeal_type / appeals.status / consent_records.user_type:
--     already have CHECKs from their original migrations.
--
-- All ADDs use idempotent IF NOT EXISTS via the conditional DO block
-- pattern (information_schema.table_constraints lookup) so re-running
-- is safe.
-- =====================================================================

-- ── exam_sessions.status ──────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'exam_sessions_status_check'
                    AND table_name = 'exam_sessions') THEN
    ALTER TABLE exam_sessions
      ADD CONSTRAINT exam_sessions_status_check
      CHECK (status IS NULL OR status IN (
        'in_progress', 'paused', 'completed', 'submitted',
        'force_submitted', 'abandoned', 'rejected'
      ));
  END IF;
END $$;

-- ── subscriptions.status ──────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'subscriptions_status_check'
                    AND table_name = 'subscriptions') THEN
    ALTER TABLE subscriptions
      ADD CONSTRAINT subscriptions_status_check
      CHECK (status IN ('trialing', 'active', 'paused', 'expired', 'cancelled'));
  END IF;
END $$;

-- ── subscriptions.plan ────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'subscriptions_plan_check'
                    AND table_name = 'subscriptions') THEN
    ALTER TABLE subscriptions
      ADD CONSTRAINT subscriptions_plan_check
      CHECK (plan IN ('starter', 'growth', 'pro', 'enterprise'));
  END IF;
END $$;

-- ── teachers.org_role ─────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'teachers_org_role_check'
                    AND table_name = 'teachers') THEN
    ALTER TABLE teachers
      ADD CONSTRAINT teachers_org_role_check
      CHECK (org_role IS NULL OR org_role IN ('admin', 'teacher'));
  END IF;
END $$;

-- ── issues.severity ───────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'issues_severity_check'
                    AND table_name = 'issues') THEN
    ALTER TABLE issues
      ADD CONSTRAINT issues_severity_check
      CHECK (severity IN ('low', 'normal', 'high'));
  END IF;
END $$;

-- ── issues.status ─────────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'issues_status_check'
                    AND table_name = 'issues') THEN
    ALTER TABLE issues
      ADD CONSTRAINT issues_status_check
      CHECK (status IN ('open', 'triaged', 'resolved'));
  END IF;
END $$;

-- ── issues.category ───────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'issues_category_check'
                    AND table_name = 'issues') THEN
    ALTER TABLE issues
      ADD CONSTRAINT issues_category_check
      CHECK (category IN ('bug', 'question', 'feature', 'session-issue', 'other'));
  END IF;
END $$;

-- ── org_invites.status ────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'org_invites_status_check'
                    AND table_name = 'org_invites') THEN
    ALTER TABLE org_invites
      ADD CONSTRAINT org_invites_status_check
      CHECK (status IN ('pending', 'accepted', 'expired', 'revoked'));
  END IF;
END $$;

-- ── consent_records.consent_type ──────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'consent_records_consent_type_check'
                    AND table_name = 'consent_records') THEN
    ALTER TABLE consent_records
      ADD CONSTRAINT consent_records_consent_type_check
      CHECK (consent_type IN ('signup_terms', 'privacy_policy', 'phone_camera'));
  END IF;
END $$;

-- ── grading_audit.action ──────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'grading_audit_action_check'
                    AND table_name = 'grading_audit') THEN
    ALTER TABLE grading_audit
      ADD CONSTRAINT grading_audit_action_check
      CHECK (action IN ('confirmed', 'bulk_accept', 'bulk_reject', 'overridden'));
  END IF;
END $$;

-- ── grading_audit.ai_confidence ───────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'grading_audit_ai_confidence_check'
                    AND table_name = 'grading_audit') THEN
    ALTER TABLE grading_audit
      ADD CONSTRAINT grading_audit_ai_confidence_check
      CHECK (ai_confidence IS NULL OR ai_confidence IN ('low', 'medium', 'high'));
  END IF;
END $$;

-- =====================================================================
-- Post-migration verification:
--
--   SELECT conname, conrelid::regclass AS table_name, convalidated AS valid
--     FROM pg_constraint
--    WHERE contype = 'c'
--      AND conname IN (
--        'exam_sessions_status_check',
--        'subscriptions_status_check', 'subscriptions_plan_check',
--        'teachers_org_role_check',
--        'issues_severity_check', 'issues_status_check', 'issues_category_check',
--        'org_invites_status_check',
--        'consent_records_consent_type_check',
--        'grading_audit_action_check', 'grading_audit_ai_confidence_check'
--      )
--    ORDER BY conname;
--
-- Expected: 11 rows, all valid = t.
--
-- Follow-ups for a future migration:
--   - clean teachers.status = '' rows → 'active' or NULL → then add
--     teachers_status_check constraint.
--   - audit violation_type / severity enums in services/risk.py; once
--     stable, add CHECKs on the violations table.
-- =====================================================================
