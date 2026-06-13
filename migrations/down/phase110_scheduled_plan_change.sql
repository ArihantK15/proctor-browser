-- Reverse phase110_scheduled_plan_change: remove the columns that were added.
-- This is a safe revert because the columns are nullable with no constraints,
-- so DROP COLUMN IF EXISTS is itself safe (the contract step has no dependents).
ALTER TABLE subscriptions
  DROP COLUMN IF EXISTS scheduled_plan,
  DROP COLUMN IF EXISTS scheduled_plan_effective_at;
