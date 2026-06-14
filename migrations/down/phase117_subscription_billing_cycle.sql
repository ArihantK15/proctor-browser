-- Gap #15 rollback: drop billing_cycle from subscriptions
ALTER TABLE subscriptions DROP COLUMN IF EXISTS billing_cycle;
