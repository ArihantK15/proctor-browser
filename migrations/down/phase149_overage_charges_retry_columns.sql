-- Reverse of phase149 — drop the retry-sweeper bookkeeping columns.
ALTER TABLE overage_charges
  DROP COLUMN IF EXISTS retry_count,
  DROP COLUMN IF EXISTS last_retry_at;
