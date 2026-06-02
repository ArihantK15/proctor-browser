-- =====================================================================
-- Phase 82 — Normalize TEXT→UUID columns to unblock phase81 FKs
-- =====================================================================
-- phase81 attempted 17 FKs but only 4 landed. The other 13 were skipped
-- because their source columns are TEXT while the parent's id is UUID
-- (historical schema drift). Postgres rejects FKs that bridge
-- incompatible types.
--
-- This migration:
--   1. For 5 tables that have RLS policies referencing the column:
--      drop the policies, ALTER COLUMN TYPE uuid, recreate the policies
--      with an explicit ::text cast so they still type-check against
--      the TEXT-returning get_my_teacher_id() helper.
--   2. For 7 tables with no policy dependents on the column:
--      ALTER COLUMN TYPE uuid directly.
--   3. Re-attempts the 12 phase81 FKs that were blocked on type drift
--      (usage_records.teacher_id doesn't exist on this schema, so its
--      FK is correctly absent and not re-attempted here).
--
-- Each table's conversion is wrapped in its own DO block with EXCEPTION
-- handling so a failure on one table doesn't poison the rest. Run order
-- inside each DO is policy-drop → type-alter → policy-recreate → FK.
--
-- Pre-flight assumptions (validated separately before running):
--   - Every existing value in every column is a valid UUID string
--     (verified by the regex audit in step 19 of the runbook).
--   - get_my_teacher_id() returns TEXT (verified by step 21).
--   - teachers.id, student_accounts.id, organizations.id are all UUID.
-- =====================================================================

-- ── exam_group_assignments ─────────────────────────────────────────
DO $$
BEGIN
  DROP POLICY IF EXISTS ega_teacher_delete ON exam_group_assignments;
  DROP POLICY IF EXISTS ega_teacher_insert ON exam_group_assignments;
  DROP POLICY IF EXISTS ega_teacher_select ON exam_group_assignments;

  ALTER TABLE exam_group_assignments
    ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  CREATE POLICY ega_teacher_delete ON exam_group_assignments
    FOR DELETE USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY ega_teacher_insert ON exam_group_assignments
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY ega_teacher_select ON exam_group_assignments
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'exam_group_assignments_teacher_fk'
                    AND table_name = 'exam_group_assignments') THEN
    ALTER TABLE exam_group_assignments
      ADD CONSTRAINT exam_group_assignments_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: exam_group_assignments conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── invite_send_counters ───────────────────────────────────────────
DO $$
BEGIN
  DROP POLICY IF EXISTS invite_send_counters_teacher_insert ON invite_send_counters;
  DROP POLICY IF EXISTS invite_send_counters_teacher_select ON invite_send_counters;
  DROP POLICY IF EXISTS invite_send_counters_teacher_update ON invite_send_counters;

  ALTER TABLE invite_send_counters
    ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  CREATE POLICY invite_send_counters_teacher_insert ON invite_send_counters
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY invite_send_counters_teacher_select ON invite_send_counters
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY invite_send_counters_teacher_update ON invite_send_counters
    FOR UPDATE USING (teacher_id::text = get_my_teacher_id());

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'invite_send_counters_teacher_fk'
                    AND table_name = 'invite_send_counters') THEN
    ALTER TABLE invite_send_counters
      ADD CONSTRAINT invite_send_counters_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: invite_send_counters conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── question_bank ──────────────────────────────────────────────────
DO $$
BEGIN
  DROP POLICY IF EXISTS question_bank_teacher_delete ON question_bank;
  DROP POLICY IF EXISTS question_bank_teacher_insert ON question_bank;
  DROP POLICY IF EXISTS question_bank_teacher_select ON question_bank;
  DROP POLICY IF EXISTS question_bank_teacher_update ON question_bank;

  ALTER TABLE question_bank
    ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  CREATE POLICY question_bank_teacher_delete ON question_bank
    FOR DELETE USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY question_bank_teacher_insert ON question_bank
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY question_bank_teacher_select ON question_bank
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY question_bank_teacher_update ON question_bank
    FOR UPDATE USING (teacher_id::text = get_my_teacher_id());

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'question_bank_teacher_fk'
                    AND table_name = 'question_bank') THEN
    ALTER TABLE question_bank
      ADD CONSTRAINT question_bank_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: question_bank conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── student_group_members ──────────────────────────────────────────
DO $$
BEGIN
  DROP POLICY IF EXISTS sgm_teacher_delete ON student_group_members;
  DROP POLICY IF EXISTS sgm_teacher_insert ON student_group_members;
  DROP POLICY IF EXISTS sgm_teacher_select ON student_group_members;

  ALTER TABLE student_group_members
    ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  CREATE POLICY sgm_teacher_delete ON student_group_members
    FOR DELETE USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY sgm_teacher_insert ON student_group_members
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY sgm_teacher_select ON student_group_members
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'student_group_members_teacher_fk'
                    AND table_name = 'student_group_members') THEN
    ALTER TABLE student_group_members
      ADD CONSTRAINT student_group_members_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: student_group_members conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── student_groups ─────────────────────────────────────────────────
DO $$
BEGIN
  DROP POLICY IF EXISTS student_groups_teacher_delete ON student_groups;
  DROP POLICY IF EXISTS student_groups_teacher_insert ON student_groups;
  DROP POLICY IF EXISTS student_groups_teacher_select ON student_groups;
  DROP POLICY IF EXISTS student_groups_teacher_update ON student_groups;

  ALTER TABLE student_groups
    ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  CREATE POLICY student_groups_teacher_delete ON student_groups
    FOR DELETE USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY student_groups_teacher_insert ON student_groups
    FOR INSERT WITH CHECK (teacher_id::text = get_my_teacher_id());
  CREATE POLICY student_groups_teacher_select ON student_groups
    FOR SELECT USING (teacher_id::text = get_my_teacher_id());
  CREATE POLICY student_groups_teacher_update ON student_groups
    FOR UPDATE USING (teacher_id::text = get_my_teacher_id());

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'student_groups_teacher_fk'
                    AND table_name = 'student_groups') THEN
    ALTER TABLE student_groups
      ADD CONSTRAINT student_groups_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: student_groups conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── appeals (no RLS dependents on teacher_id / student_id) ─────────
DO $$
BEGIN
  ALTER TABLE appeals ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;
  ALTER TABLE appeals ALTER COLUMN student_id TYPE uuid USING student_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'appeals_teacher_fk'
                    AND table_name = 'appeals') THEN
    ALTER TABLE appeals
      ADD CONSTRAINT appeals_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'appeals_student_fk'
                    AND table_name = 'appeals') THEN
    ALTER TABLE appeals
      ADD CONSTRAINT appeals_student_fk
      FOREIGN KEY (student_id) REFERENCES student_accounts(id)
      ON DELETE SET NULL NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: appeals conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── consent_records ────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE consent_records ALTER COLUMN user_id TYPE uuid USING user_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'consent_records_user_fk'
                    AND table_name = 'consent_records') THEN
    ALTER TABLE consent_records
      ADD CONSTRAINT consent_records_user_fk
      FOREIGN KEY (user_id) REFERENCES student_accounts(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: consent_records conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── exam_templates ─────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE exam_templates ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'exam_templates_teacher_fk'
                    AND table_name = 'exam_templates') THEN
    ALTER TABLE exam_templates
      ADD CONSTRAINT exam_templates_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: exam_templates conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── google_oauth_states ────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE google_oauth_states ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'google_oauth_states_teacher_fk'
                    AND table_name = 'google_oauth_states') THEN
    ALTER TABLE google_oauth_states
      ADD CONSTRAINT google_oauth_states_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: google_oauth_states conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── grading_audit ──────────────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE grading_audit ALTER COLUMN teacher_id TYPE uuid USING teacher_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'grading_audit_teacher_fk'
                    AND table_name = 'grading_audit') THEN
    ALTER TABLE grading_audit
      ADD CONSTRAINT grading_audit_teacher_fk
      FOREIGN KEY (teacher_id) REFERENCES teachers(id)
      ON DELETE RESTRICT NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: grading_audit conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- ── usage_records.org_id ───────────────────────────────────────────
DO $$
BEGIN
  ALTER TABLE usage_records ALTER COLUMN org_id TYPE uuid USING org_id::uuid;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'usage_records_org_fk'
                    AND table_name = 'usage_records') THEN
    ALTER TABLE usage_records
      ADD CONSTRAINT usage_records_org_fk
      FOREIGN KEY (org_id) REFERENCES organizations(id)
      ON DELETE CASCADE NOT VALID;
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'phase82: usage_records conversion failed (%): %', SQLSTATE, SQLERRM;
END $$;

-- =====================================================================
-- Post-migration verification (run separately):
--
--   SELECT conname, conrelid::regclass AS table_name, convalidated AS valid
--     FROM pg_constraint
--    WHERE contype = 'f'
--      AND conname IN (
--        'exam_group_assignments_teacher_fk',
--        'invite_send_counters_teacher_fk',
--        'question_bank_teacher_fk',
--        'student_group_members_teacher_fk',
--        'student_groups_teacher_fk',
--        'appeals_teacher_fk',
--        'appeals_student_fk',
--        'consent_records_user_fk',
--        'exam_templates_teacher_fk',
--        'google_oauth_states_teacher_fk',
--        'grading_audit_teacher_fk',
--        'usage_records_org_fk'
--      )
--    ORDER BY conname;
--
-- Expected: all 12 rows present, valid = f (NOT VALID until audit + VALIDATE).
--
-- Then re-run the tenancy audit script (read-only) to surface any
-- orphan rows that would block VALIDATE:
--
--   DATABASE_URL=... python3 scripts/audit_tenancy.py --verbose
--
-- Once clean, promote each NOT VALID constraint per the runbook.
-- =====================================================================
