-- =====================================================================
-- Phase 91 — Race-free quota trigger via per-org advisory lock
-- =====================================================================
-- Phase 90 introduced enforce_org_student_quota() but left a known
-- race: two concurrent INSERTs that both read v_current = N could both
-- pass the (N + 1 > max) check and both commit, exceeding the cap by 1.
--
-- This replacement function acquires a transaction-scoped advisory
-- lock keyed on the org_id before reading the count. The lock:
--
--   • Serializes quota checks for the same org → no race.
--   • Doesn't block readers or queries on other orgs.
--   • Auto-releases at COMMIT/ROLLBACK (no leak).
--   • Costs ~microseconds — negligible vs the SELECT COUNT(*) itself.
--
-- The trigger definition itself doesn't change; CREATE OR REPLACE
-- FUNCTION updates the body in place and the existing
-- enforce_org_student_quota trigger picks up the new definition on
-- the next INSERT.
--
-- hashtextextended() returns a stable bigint hash from the org_id text
-- — exactly the int8 input pg_advisory_xact_lock expects. Different
-- orgs almost certainly hash to different locks (the chance of a
-- 64-bit collision across a few thousand orgs is negligible); on the
-- vanishingly rare collision the cross-org would just serialize
-- briefly, no correctness impact.
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

  -- Race fix: serialize concurrent quota checks for the same org.
  -- Lock is transaction-scoped, released at COMMIT/ROLLBACK.
  PERFORM pg_advisory_xact_lock(hashtextextended(v_org_id::text, 0));

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
      USING ERRCODE = '23514';  -- check_violation
  END IF;

  RETURN NEW;
END;
$$;

-- =====================================================================
-- Post-migration verification:
--
--   -- Function definition includes pg_advisory_xact_lock
--   SELECT pg_get_functiondef('public.enforce_org_student_quota'::regproc);
--
-- Manual race test (requires two psql sessions):
--   In each session, start a transaction, insert a student row that
--   would push the org to exactly max_students:
--     BEGIN;
--     INSERT INTO students (...);
--   The second session blocks on the advisory lock until the first
--   commits. If the first commits a row that puts the org AT the cap,
--   the second's insert raises the quota-exceeded exception. Pre-fix,
--   both inserts would have succeeded.
-- =====================================================================
