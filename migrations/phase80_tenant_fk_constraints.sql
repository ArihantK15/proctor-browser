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
--
-- IMPORTANT — column-type drift across legacy migrations: some tables use
-- UUID for IDs while others use TEXT (the column drifted across phase
-- migrations). Postgres rejects FKs that bridge incompatible types. Each
-- ALTER below is wrapped in an EXCEPTION-handling DO block that
-- gracefully RAISE NOTICEs the failure and continues — that way the
-- working constraints land even if one is blocked on type drift. Operators
-- can later normalise column types and re-run this migration.
--
-- Validation of existing data is a SEPARATE step:
--
--    -- 1. run the audit script to find existing violations:
--    --    DATABASE_URL=... python3 scripts/audit_tenancy.py --verbose
--    -- 2. fix or clean them up (ops decision per-row)
--    -- 3. then ALTER TABLE ... VALIDATE CONSTRAINT ... per the runbook
-- =====================================================================

-- Helper: attempt to add a FK; log + continue on error (type mismatch,
-- missing column, etc.). Each invocation is its own DO block so a
-- failure isolates to that constraint.
-- (Inlined per-constraint instead of a function because PL/pgSQL DDL
-- inside a function would need EXECUTE FORMAT and complicate the read.)

-- ── students → teachers ─────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'students_teacher_fk' AND table_name = 'students'
  ) THEN
    BEGIN
      ALTER TABLE students
        ADD CONSTRAINT students_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: students_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── students → student_accounts ─────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'students' AND column_name = 'account_id'
  ) THEN
    RAISE NOTICE 'phase80: students.account_id column missing — skipping FK';
  ELSIF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'students_account_fk' AND table_name = 'students'
  ) THEN
    BEGIN
      ALTER TABLE students
        ADD CONSTRAINT students_account_fk
        FOREIGN KEY (account_id) REFERENCES student_accounts(id)
        ON DELETE SET NULL
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: students_account_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── exam_sessions → teachers ────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'exam_sessions_teacher_fk' AND table_name = 'exam_sessions'
  ) THEN
    BEGIN
      ALTER TABLE exam_sessions
        ADD CONSTRAINT exam_sessions_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: exam_sessions_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── violations → exam_sessions ──────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'violations_session_fk' AND table_name = 'violations'
  ) THEN
    BEGIN
      ALTER TABLE violations
        ADD CONSTRAINT violations_session_fk
        FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
        ON DELETE CASCADE
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: violations_session_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── violations → teachers ──────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'violations_teacher_fk' AND table_name = 'violations'
  ) THEN
    BEGIN
      ALTER TABLE violations
        ADD CONSTRAINT violations_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: violations_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── answers → exam_sessions ─────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'answers_session_fk' AND table_name = 'answers'
  ) THEN
    BEGIN
      ALTER TABLE answers
        ADD CONSTRAINT answers_session_fk
        FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
        ON DELETE CASCADE
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: answers_session_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── answers → teachers ──────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'answers_teacher_fk' AND table_name = 'answers'
  ) THEN
    BEGIN
      ALTER TABLE answers
        ADD CONSTRAINT answers_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: answers_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── exam_config → teachers ─────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'exam_config_teacher_fk' AND table_name = 'exam_config'
  ) THEN
    BEGIN
      ALTER TABLE exam_config
        ADD CONSTRAINT exam_config_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: exam_config_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── questions → teachers ───────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'questions_teacher_fk' AND table_name = 'questions'
  ) THEN
    BEGIN
      ALTER TABLE questions
        ADD CONSTRAINT questions_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: questions_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── student_invites → teachers ─────────────────────────────────────
-- Skipped on the production schema because student_invites.teacher_id is
-- TEXT while teachers.id is UUID — historical type drift. Re-run after
-- normalising the column type. The DO block below is best-effort.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'student_invites_teacher_fk' AND table_name = 'student_invites'
  ) THEN
    BEGIN
      ALTER TABLE student_invites
        ADD CONSTRAINT student_invites_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: student_invites_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── teachers → organizations ────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'teachers_org_fk' AND table_name = 'teachers'
  ) THEN
    BEGIN
      ALTER TABLE teachers
        ADD CONSTRAINT teachers_org_fk
        FOREIGN KEY (org_id) REFERENCES organizations(id)
        ON DELETE SET NULL
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: teachers_org_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── exam_templates → teachers ─────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exam_templates')
     AND NOT EXISTS (
       SELECT 1 FROM information_schema.table_constraints
       WHERE constraint_name = 'exam_templates_teacher_fk' AND table_name = 'exam_templates'
     )
  THEN
    BEGIN
      ALTER TABLE exam_templates
        ADD CONSTRAINT exam_templates_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE
        NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase80: exam_templates_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── refresh_tokens (user-kind-discriminated; no FK across kinds) ───
-- refresh_tokens.user_id can reference either teachers.id or
-- student_accounts.id depending on the kind column. Postgres doesn't
-- support conditional FKs, so we leave this as app-enforced.

-- =====================================================================
-- Type-drift cleanup (FOLLOW-UP, not part of this migration):
-- =====================================================================
-- A small subset of the FKs above are blocked by historical column-type
-- drift (e.g., student_invites.teacher_id TEXT vs teachers.id UUID). To
-- unblock those, normalise the column type in a separate migration:
--
--   ALTER TABLE student_invites
--     ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;
--
-- ...then re-run this migration; the missing FK will now land. Do this
-- one column at a time during low-traffic windows; the USING cast
-- will scan every row.
--
-- Next step (separate operator action, when audit is clean): run the
-- VALIDATE CONSTRAINT block from docs/TENANCY_HARDENING_RUNBOOK.md to
-- promote each NOT VALID constraint to fully-enforced.
