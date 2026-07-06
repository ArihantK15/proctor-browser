-- Reverse of phase134: recreate the phase90 hard-fail student-quota trigger.
-- Restores the DB-level belt-and-suspenders cap (BEFORE INSERT on students).
-- Run this to revert to hard-cap-only enforcement (e.g. if soft-cap is rolled
-- back). Must be paired with OVERAGE_BILLING_ENABLED=off, or over-cap inserts
-- the app intends to allow will be rejected here.

BEGIN;

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
  SELECT t.org_id INTO v_org_id
    FROM teachers t
   WHERE t.id = NEW.teacher_id;

  IF v_org_id IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT o.max_students INTO v_max_students
    FROM organizations o
   WHERE o.id = v_org_id;

  IF v_max_students IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT COUNT(*) INTO v_current
    FROM students s
    JOIN teachers t ON t.id = s.teacher_id
   WHERE t.org_id = v_org_id;

  IF v_current + 1 > v_max_students THEN
    RAISE EXCEPTION
      'Student quota exceeded for organization %: % current, % allowed by plan. Upgrade your plan to add more students.',
      v_org_id, v_current, v_max_students
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS enforce_org_student_quota ON students;
CREATE TRIGGER enforce_org_student_quota
  BEFORE INSERT ON students
  FOR EACH ROW
  EXECUTE FUNCTION public.enforce_org_student_quota();

COMMIT;
