-- migration:contract drop hard student-quota trigger to enable soft-cap + overage billing
-- =====================================================================
-- phase134: drop the phase90 hard-fail student-quota trigger (soft cap)
-- =====================================================================
-- Soft cap + overage billing (per-plan rates ₹80/70/60) means exceeding the
-- plan's student limit is ALLOWED and billed, not denied. The phase90
-- BEFORE-INSERT trigger hard-rejects over-cap inserts, which blocks soft-cap
-- entirely (the insert fails at the DB even when the app would allow it).
--
-- Drop the trigger + function. Seat enforcement now lives solely in
-- app/services/sessions.py::check_org_limits, which HARD-caps when
-- OVERAGE_BILLING_ENABLED is off (current/default behaviour, unchanged) and
-- allows overage (billed at cycle renewal) when on. This removes the DB-level
-- belt-and-suspenders backstop the trigger provided — accepted because soft-cap
-- inherently has no hard ceiling, and the app check is the single source of
-- truth. Reversible: migrations/down/phase134… recreates the trigger verbatim.
-- =====================================================================

BEGIN;

DROP TRIGGER IF EXISTS enforce_org_student_quota ON students;
DROP FUNCTION IF EXISTS public.enforce_org_student_quota();

COMMIT;
