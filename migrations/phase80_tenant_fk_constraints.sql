-- =====================================================================
-- Phase 80 — Tenant-isolation foreign keys
-- =====================================================================
-- Adds DB-level foreign key constraints between tenant tables so the
-- application-level scoping audit is backstopped by the database itself.
-- A future PR that accidentally drops an ``eq("teacher_id", ...)`` filter
-- can still leak rows on READ — these FKs only catch orphan / dangling
-- references on WRITE — but they DO prevent:
--   • a students row pointing at a deleted teacher
--   • an exam_session whose teacher_id never existed (forged JWT class
--     of bug)
--   • a student_account being deleted while enrollments still exist
--     without those enrollments being SET NULL'd or cascaded
--
-- Each constraint is added with NOT VALID so this migration is fast and
-- never fails on legacy rows that already violate the invariant.
-- Validation of existing data is a SEPARATE step:
--
--    -- 1. run the audit script to find existing violations:
--    --    DATABASE_URL=... python3 scripts/audit_tenancy.py --verbose
--    -- 2. fix or clean them up (ops decision per-row)
--    -- 3. then run:
--    --    ALTER TABLE students VALIDATE CONSTRAINT students_teacher_fk;
--    --    ...etc
--
-- All ALTER statements are idempotent (IF NOT EXISTS where supported,
-- DO-blocks with information_schema check otherwise).
-- =====================================================================

-- ── students → teachers ─────────────────────────────────────────────
-- ON DELETE RESTRICT: a teacher cannot be hard-deleted while students
-- still reference them. The teacher_delete flow already orphan-handles
-- this app-side (members are removed by setting org_id=NULL, not by
-- deleting the row), so a real DELETE on teachers is rare ops-only.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'students_teacher_fk' AND table_name = 'students'
  ) THEN
    ALTER TABLE students
      ADD CONSTRAINT students_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── students → student_accounts ─────────────────────────────────────
-- ON DELETE SET NULL: a student_accounts row can be deleted (GDPR
-- "right to be forgotten") and the enrollment rows are kept (anonymised
-- by the app's delete handler) with account_id cleared. This matches
-- the existing app behaviour in _track_a_hybrid_delete_student_account.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'students' AND column_name = 'account_id'
  ) THEN
    RAISE NOTICE 'students.account_id column missing — skipping FK';
  ELSIF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'students_account_fk' AND table_name = 'students'
  ) THEN
    ALTER TABLE students
      ADD CONSTRAINT students_account_fk
      FOREIGN KEY (account_id) REFERENCES student_accounts(id)
      ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

-- ── exam_sessions → teachers ────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'exam_sessions_teacher_fk' AND table_name = 'exam_sessions'
  ) THEN
    ALTER TABLE exam_sessions
      ADD CONSTRAINT exam_sessions_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── exam_sessions → exam_config (composite) ────────────────────────
-- We deliberately do NOT add (teacher_id, exam_id) → exam_config(teacher_id,
-- exam_id) because exam_config doesn't have a UNIQUE constraint on that
-- pair across all deployments. Adding it would require schema verification
-- per environment. The application enforces this pairing via the
-- validate_student handler's exam_config existence check.

-- ── violations → exam_sessions ──────────────────────────────────────
-- Violations CASCADE delete with their session — when admin clear-live-
-- sessions wipes a session, the violations should go too.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'violations_session_fk' AND table_name = 'violations'
  ) THEN
    ALTER TABLE violations
      ADD CONSTRAINT violations_session_fk
      FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
      ON DELETE CASCADE
      NOT VALID;
  END IF;
END $$;

-- ── violations → teachers ──────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'violations_teacher_fk' AND table_name = 'violations'
  ) THEN
    ALTER TABLE violations
      ADD CONSTRAINT violations_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── answers → exam_sessions ─────────────────────────────────────────
-- Same CASCADE rationale as violations.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'answers_session_fk' AND table_name = 'answers'
  ) THEN
    ALTER TABLE answers
      ADD CONSTRAINT answers_session_fk
      FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
      ON DELETE CASCADE
      NOT VALID;
  END IF;
END $$;

-- ── answers → teachers ──────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'answers_teacher_fk' AND table_name = 'answers'
  ) THEN
    ALTER TABLE answers
      ADD CONSTRAINT answers_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── exam_config → teachers ─────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'exam_config_teacher_fk' AND table_name = 'exam_config'
  ) THEN
    ALTER TABLE exam_config
      ADD CONSTRAINT exam_config_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── questions → teachers ───────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'questions_teacher_fk' AND table_name = 'questions'
  ) THEN
    ALTER TABLE questions
      ADD CONSTRAINT questions_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── student_invites → teachers ─────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'student_invites_teacher_fk' AND table_name = 'student_invites'
  ) THEN
    ALTER TABLE student_invites
      ADD CONSTRAINT student_invites_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT
      NOT VALID;
  END IF;
END $$;

-- ── teachers → organizations ────────────────────────────────────────
-- A teacher whose org is deleted falls back to org_id=NULL — they
-- effectively become "no longer in an org" and lose admin scope.
-- The app already treats NULL org_id as "plain teacher" (see scope.py
-- resolve_scope fallback).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'teachers_org_fk' AND table_name = 'teachers'
  ) THEN
    ALTER TABLE teachers
      ADD CONSTRAINT teachers_org_fk
      FOREIGN KEY (org_id) REFERENCES organizations(id)
      ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

-- ── exam_templates → teachers ─────────────────────────────────────
-- Only attempt if the table exists (deployments without the templates
-- feature won't have it).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_templates')
     AND NOT EXISTS (
       SELECT 1 FROM information_schema.table_constraints
       WHERE constraint_name = 'exam_templates_teacher_fk' AND table_name = 'exam_templates'
     )
  THEN
    ALTER TABLE exam_templates
      ADD CONSTRAINT exam_templates_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE
      NOT VALID;
  END IF;
END $$;

-- ── refresh_tokens (user-kind-discriminated; no FK across kinds) ───
-- refresh_tokens.user_id can reference either teachers.id or
-- student_accounts.id depending on the kind column. Postgres doesn't
-- support conditional FKs, so we leave this as app-enforced. The
-- audit script's duplicate-account check catches accidental kind
-- mixups.

-- =====================================================================
-- Next step (separate operator action, when audit is clean):
-- =====================================================================
-- After scripts/audit_tenancy.py reports OK on the prod DB, validate
-- each constraint to bring them in line. Each VALIDATE is a SHARE LOCK
-- on the table — readers proceed, writers block briefly. Run during a
-- maintenance window or use pg_repack-style online verification:
--
--   ALTER TABLE students       VALIDATE CONSTRAINT students_teacher_fk;
--   ALTER TABLE students       VALIDATE CONSTRAINT students_account_fk;
--   ALTER TABLE exam_sessions  VALIDATE CONSTRAINT exam_sessions_teacher_fk;
--   ALTER TABLE violations     VALIDATE CONSTRAINT violations_session_fk;
--   ALTER TABLE violations     VALIDATE CONSTRAINT violations_teacher_fk;
--   ALTER TABLE answers        VALIDATE CONSTRAINT answers_session_fk;
--   ALTER TABLE answers        VALIDATE CONSTRAINT answers_teacher_fk;
--   ALTER TABLE exam_config    VALIDATE CONSTRAINT exam_config_teacher_fk;
--   ALTER TABLE questions      VALIDATE CONSTRAINT questions_teacher_fk;
--   ALTER TABLE student_invites VALIDATE CONSTRAINT student_invites_teacher_fk;
--   ALTER TABLE teachers       VALIDATE CONSTRAINT teachers_org_fk;
--   ALTER TABLE exam_templates VALIDATE CONSTRAINT exam_templates_teacher_fk;
