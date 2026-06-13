-- phase110: scheduled (deferred) plan change for mid-cycle downgrades (gap #1)
--
-- change_plan upgrades take effect immediately (prorated add-on), but
-- downgrades are deferred to cycle end so the org keeps what it paid for.
-- The pending target + its effective time live on the subscription row and are
-- applied by the subscription.charged webhook at the next renewal (which then
-- reconciles the cap down, and gap #4 bills any resulting overage).
--
-- Nullable, no constraints → plain ADD COLUMN IF NOT EXISTS is safe and
-- needs no NOT VALID/VALIDATE dance.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS scheduled_plan              TEXT,
  ADD COLUMN IF NOT EXISTS scheduled_plan_effective_at TIMESTAMPTZ;
