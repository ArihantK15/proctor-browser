-- phase144 rollback: drop the consolidated subscriptions status constraint.
--
-- We intentionally do NOT recreate the old constraints: `subscriptions_status_check`
-- (5 statuses, rejected 'created') and `subscriptions_status_chk` (NOT VALID, missing
-- grace/pending/Razorpay states) are exactly what caused the 4-day signup outage.
-- Reversing to "no status CHECK" is over-permissive but safe (never blocks a valid
-- insert); re-apply phase144 to restore the comprehensive constraint.
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_status_check;
