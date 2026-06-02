-- =====================================================================
-- Phase 87 — Deferred teachers.status CHECK constraint
-- =====================================================================
-- phase85 added 11 CHECK constraints but deliberately skipped
-- teachers.status because 4 legacy rows had an empty string instead of
-- a valid enum value or NULL. A subsequent re-audit confirmed those
-- 4 rows are now NULL (which phase62 documents as semantically
-- equivalent to 'active'), and zero rows have any unexpected value.
-- Safe to add the CHECK now.
--
-- Canonical enum per phase62_teachers_status.sql + app/routers/auth.py:
--   NULL                    -- normal account, can log in (legacy default)
--   'active'                -- explicit normal account
--   'pending_verification'  -- email-unverified; login blocked
--   'suspended'             -- admin-disabled (phase62 reserved this term)
--   'deleted'               -- soft-deleted (used in auth.py:2612)
--
-- Plain CHECK (not NOT VALID) — current data is a strict subset of the
-- enum, so the constraint validates immediately on a sub-second lock.
-- =====================================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                  WHERE constraint_name = 'teachers_status_check'
                    AND table_name = 'teachers') THEN
    ALTER TABLE teachers
      ADD CONSTRAINT teachers_status_check
      CHECK (status IS NULL OR status IN (
        'active', 'pending_verification', 'suspended', 'deleted'
      ));
  END IF;
END $$;

-- =====================================================================
-- Post-migration verification:
--
--   SELECT conname, convalidated AS valid
--     FROM pg_constraint
--    WHERE conname = 'teachers_status_check';
--
-- Expected: 1 row, valid = t.
-- =====================================================================
