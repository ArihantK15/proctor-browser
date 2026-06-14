-- Gap #15: Annual billing option
-- Add billing_cycle column to subscriptions (monthly|annual, default monthly).
-- Annual selects a different Razorpay plan id (yearly interval) at checkout.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS billing_cycle TEXT NOT NULL DEFAULT 'monthly';
