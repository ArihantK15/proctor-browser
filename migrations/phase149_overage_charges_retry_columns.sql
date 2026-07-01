-- phase149: retry bookkeeping for overage_charges (follow-up #20 / #4c).
--
-- bill_cycle_overage() leaves a claim row as status='failed' when the
-- Razorpay add-on API call errors, and deliberately never retries inline
-- (the base subscription.charged webhook must still 200). Nothing else in
-- the app has ever retried it — a transient Razorpay/network blip on
-- overage day permanently drops that month's overage revenue unless a
-- human notices the 'failed' row and settles it by hand.
--
-- Adds the bookkeeping an automated sweeper needs to retry safely and
-- eventually give up: retry_count caps how many attempts we make,
-- last_retry_at lets the sweeper back off (skip rows retried too recently)
-- and lets an operator see how long something has been stuck.

ALTER TABLE overage_charges
  ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMPTZ;
