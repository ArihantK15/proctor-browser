-- =====================================================================
-- Phase 90 — Hard-fail trigger enforcing per-org student quota
-- =====================================================================
-- The subscription contract is "X active students per org" where X is
-- the plan's student cap (starter=30, growth=150, pro=500, enterprise=
-- 999999). The cap is stored on organizations.max_students, set by
-- app/routers/billing.py when a subscription changes.
--
-- Today, the cap is enforced ONLY in Python (app/services/sessions.py
-- check_org_limits). A future code regression that drops or bypasses
-- that check could over-bill an org. This DB trigger is the belt-and-
-- suspenders: any INSERT into students is rejected if accepting it
-- would push the org's total student count past max_students.
--
-- Semantics:
--   - Trigger fires BEFORE INSERT on students.
--   - Resolves new.teacher_id → teachers.org_id → organizations.max_students.
--   - Counts students currently in the org (across all teachers in
--     that org).
--   - If count + 1 > max_students, RAISE EXCEPTION with a clear message
--     pointing the operator at "upgrade your plan" — matches the
--     wording in app/services/sessions.py so dashboards stay coherent.
--
-- Skip-conditions (no quota check applies):
--   - new.teacher_id IS NULL — orphan student row, no org to bound.
--   - teacher.org_id IS NULL — teacher not yet assigned to an org.
--   - organizations.max_students IS NULL — legacy or unconfigured org.
--   - Enterprise tier (max_students = 999999) effectively unlimited;
--     the check still runs but never trips.
--
-- Race window: two concurrent INSERTs that both read v_current = N
-- and both pass the check could each succeed when only one should.
-- The trigger doesn't lock the count to prevent this; the +1 race is
-- the same the Python check has today. For stricter correctness we'd
-- need a denormalized counter column on organizations + a row lock
-- on UPDATE — tracked as a separate hardening task.
--
-- Performance: COUNT(*) over the org's students rows runs per INSERT.
-- With 30 students × 6 teachers (largest current org) it's sub-ms.
-- The bulk-import path adds ~10ms per row at scale, acceptable for
-- an admin-triggered operation.
-- =====================================================================

CREATE OR REPLACE FUNCTION public.enforce_org_student_quota()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_org_id       UUID;
  v_max_students INT;
  v_current      INT;
BEGIN
  -- Resolve org via the teacher referenced by the new row.
  SELECT t.org_id INTO v_org_id
    FROM teachers t
   WHERE t.id = NEW.teacher_id;

  IF v_org_id IS NULL THEN
    RETURN NEW;  -- no teacher row or no org → no quota to enforce
  END IF;

  SELECT o.max_students INTO v_max_students
    FROM organizations o
   WHERE o.id = v_org_id;

  IF v_max_students IS NULL THEN
    RETURN NEW;  -- legacy org without a configured cap → skip
  END IF;

  SELECT COUNT(*) INTO v_current
    FROM students s
    JOIN teachers t ON t.id = s.teacher_id
   WHERE t.org_id = v_org_id;

  IF v_current + 1 > v_max_students THEN
    RAISE EXCEPTION
      'Student quota exceeded for organization %: % current, % allowed by plan. Upgrade your plan to add more students.',
      v_org_id, v_current, v_max_students
      USING ERRCODE = '23514';  -- check_violation (closest standard SQLSTATE)
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS enforce_org_student_quota ON students;
CREATE TRIGGER enforce_org_student_quota
  BEFORE INSERT ON students
  FOR EACH ROW
  EXECUTE FUNCTION public.enforce_org_student_quota();

-- =====================================================================
-- Post-migration verification:
--
--   -- Trigger exists, attached to students, fires BEFORE INSERT
--   SELECT trigger_name, event_manipulation, action_timing, action_statement
--     FROM information_schema.triggers
--    WHERE trigger_name = 'enforce_org_student_quota';
--
--   -- Smoke test (run in a transaction you ROLL BACK):
--   BEGIN;
--     -- Pick any active teacher whose org is at-cap
--     INSERT INTO students (id, roll_number, full_name, teacher_id, email)
--       VALUES (gen_random_uuid(), 'TEST_QUOTA', 'test', '<teacher-uuid-at-cap>', 'test@example.com');
--     -- Expected: ERROR: Student quota exceeded for organization ...
--   ROLLBACK;
-- =====================================================================
