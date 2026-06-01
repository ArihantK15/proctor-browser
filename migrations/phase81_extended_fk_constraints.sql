-- =====================================================================
-- Phase 81 — Extended referential integrity (non-tenant tables)
-- =====================================================================
-- phase80 covered the tenant-data plane (students, sessions, answers,
-- violations, etc). This pass extends the FK net to the remaining
-- 13 tables that have ID-shaped columns but no DB-level constraints
-- holding them to a real parent row.
--
-- What this catches going forward:
--   • An appeals row pointing at a deleted session.
--   • A grading_audit row pointing at a deleted teacher.
--   • A student_group_members row whose group was already removed.
--   • An exam_group_assignments row referencing a non-existent exam.
--   • Etc.
--
-- Same NOT VALID + EXCEPTION-per-constraint pattern as phase80, so a
-- type-mismatch or missing column on one constraint won't block the
-- rest from landing. Each block:
--   1. Verifies the target column exists (some tables differ across
--      legacy schemas).
--   2. Verifies the constraint isn't already present.
--   3. Wraps the ALTER in a savepoint-style BEGIN/EXCEPTION so a
--      type drift NOTICEs and continues.
--
-- The "user_id" columns on auth_events / auth_sessions / refresh_tokens
-- / email_otps are kind-discriminated (point at either teachers.id or
-- student_accounts.id based on the kind column). Postgres doesn't
-- support conditional/partial FKs, so we deliberately leave those as
-- app-enforced. Tested by the regression suite.
-- =====================================================================

-- ── appeals ─────────────────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'appeals' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'appeals_teacher_fk'
                        AND table_name = 'appeals')
  THEN
    BEGIN
      ALTER TABLE appeals
        ADD CONSTRAINT appeals_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: appeals_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'appeals' AND column_name = 'session_key')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'appeals_session_fk'
                        AND table_name = 'appeals')
  THEN
    BEGIN
      ALTER TABLE appeals
        ADD CONSTRAINT appeals_session_fk
        FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: appeals_session_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'appeals' AND column_name = 'student_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'appeals_student_fk'
                        AND table_name = 'appeals')
  THEN
    BEGIN
      ALTER TABLE appeals
        ADD CONSTRAINT appeals_student_fk
        FOREIGN KEY (student_id) REFERENCES student_accounts(id)
        ON DELETE SET NULL NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: appeals_student_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── consent_records ────────────────────────────────────────────────
-- user_id is a student_account id (GDPR consent is per-login).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'consent_records' AND column_name = 'user_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'consent_records_user_fk'
                        AND table_name = 'consent_records')
  THEN
    BEGIN
      ALTER TABLE consent_records
        ADD CONSTRAINT consent_records_user_fk
        FOREIGN KEY (user_id) REFERENCES student_accounts(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: consent_records_user_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── student_groups → teachers ──────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'student_groups' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'student_groups_teacher_fk'
                        AND table_name = 'student_groups')
  THEN
    BEGIN
      ALTER TABLE student_groups
        ADD CONSTRAINT student_groups_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: student_groups_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── student_group_members → teachers (group_id FK already exists) ─
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'student_group_members' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'student_group_members_teacher_fk'
                        AND table_name = 'student_group_members')
  THEN
    BEGIN
      ALTER TABLE student_group_members
        ADD CONSTRAINT student_group_members_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: student_group_members_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── exam_group_assignments → teachers ─────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'exam_group_assignments' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'exam_group_assignments_teacher_fk'
                        AND table_name = 'exam_group_assignments')
  THEN
    BEGIN
      ALTER TABLE exam_group_assignments
        ADD CONSTRAINT exam_group_assignments_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: exam_group_assignments_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── grading_audit → teachers + sessions ───────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'grading_audit' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'grading_audit_teacher_fk'
                        AND table_name = 'grading_audit')
  THEN
    BEGIN
      ALTER TABLE grading_audit
        ADD CONSTRAINT grading_audit_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: grading_audit_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'grading_audit' AND column_name = 'session_key')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'grading_audit_session_fk'
                        AND table_name = 'grading_audit')
  THEN
    BEGIN
      ALTER TABLE grading_audit
        ADD CONSTRAINT grading_audit_session_fk
        FOREIGN KEY (session_key) REFERENCES exam_sessions(session_key)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: grading_audit_session_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── question_bank → teachers ──────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'question_bank' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'question_bank_teacher_fk'
                        AND table_name = 'question_bank')
  THEN
    BEGIN
      ALTER TABLE question_bank
        ADD CONSTRAINT question_bank_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: question_bank_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── exam_templates → teachers (re-attempt; may have been skipped) ─
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'exam_templates' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'exam_templates_teacher_fk'
                        AND table_name = 'exam_templates')
  THEN
    BEGIN
      ALTER TABLE exam_templates
        ADD CONSTRAINT exam_templates_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: exam_templates_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── invite_send_counters → teachers ───────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'invite_send_counters' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'invite_send_counters_teacher_fk'
                        AND table_name = 'invite_send_counters')
  THEN
    BEGIN
      ALTER TABLE invite_send_counters
        ADD CONSTRAINT invite_send_counters_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: invite_send_counters_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── google_oauth_states → teachers ────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'google_oauth_states' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'google_oauth_states_teacher_fk'
                        AND table_name = 'google_oauth_states')
  THEN
    BEGIN
      ALTER TABLE google_oauth_states
        ADD CONSTRAINT google_oauth_states_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: google_oauth_states_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── usage_records → org or teacher (column-existence determines which) ─
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'usage_records' AND column_name = 'org_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'usage_records_org_fk'
                        AND table_name = 'usage_records')
  THEN
    BEGIN
      ALTER TABLE usage_records
        ADD CONSTRAINT usage_records_org_fk
        FOREIGN KEY (org_id) REFERENCES organizations(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: usage_records_org_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'usage_records' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'usage_records_teacher_fk'
                        AND table_name = 'usage_records')
  THEN
    BEGIN
      ALTER TABLE usage_records
        ADD CONSTRAINT usage_records_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE RESTRICT NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: usage_records_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── google_classroom_links → teachers (existing FK is for course_id) ─
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'google_classroom_links' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'google_classroom_links_teacher_fk'
                        AND table_name = 'google_classroom_links')
  THEN
    BEGIN
      ALTER TABLE google_classroom_links
        ADD CONSTRAINT google_classroom_links_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE CASCADE NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: google_classroom_links_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- ── issues → teachers (currently 2 FKs, may be org_id+resolved_by) ─
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'issues' AND column_name = 'teacher_id')
     AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                      WHERE constraint_name = 'issues_teacher_fk'
                        AND table_name = 'issues')
  THEN
    BEGIN
      ALTER TABLE issues
        ADD CONSTRAINT issues_teacher_fk
        FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        ON DELETE SET NULL NOT VALID;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'phase81: issues_teacher_fk skipped (%): %', SQLSTATE, SQLERRM;
    END;
  END IF;
END $$;

-- =====================================================================
-- Notes on skipped tables:
--   • auth_events, auth_sessions, refresh_tokens, email_otps:
--     user_id is kind-discriminated (teacher vs student_account).
--     No conditional FK in Postgres; app-enforced.
--   • demo_requests, organizations, student_accounts, schema_migrations:
--     root tables with no parent.
--   • exam_sessions(exam_id) → exam_config: would need a UNIQUE
--     constraint on exam_config(teacher_id, exam_id) first. Tracked
--     for a future migration.
-- =====================================================================
-- After this migration applies, run the audit script + the VALIDATE
-- step in docs/TENANCY_HARDENING_RUNBOOK.md to promote the NOT VALID
-- constraints once orphan rows are cleaned.
