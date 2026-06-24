-- migration:contract drop the two conflicting subscriptions status CHECK constraints (old `subscriptions_status_check` rejected 'created'; `subscriptions_status_chk` was NOT VALID + missing states) and replace with one comprehensive constraint; reverse in migrations/down/phase144_subscriptions_status_fix.sql
-- phase144 — fix the subscriptions status CHECK constraint(s).
--
-- The card-on-signup rework added `subscriptions_status_chk` (NOT VALID) but left
-- the OLD, too-restrictive `subscriptions_status_check` in place. The old one
-- allowed only ('trialing','active','paused','expired','cancelled') — so a signup
-- inserting status='created' (CARD_ON_SIGNUP_ENFORCED) violated it and EVERY
-- teacher signup 500'd for days. Both constraints were also internally
-- inconsistent with the code (missing 'grace'/'pending'/Razorpay states).
--
-- Consolidate to ONE constraint covering every status the app + Razorpay webhooks
-- actually write (verified by grep over app/: 13 values). Idempotent.
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_status_check;
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_status_chk;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_status_check
  CHECK (status IN (
    'created','authenticated','active','trialing','pending',
    'past_due','grace','halted','paused',
    'cancelling','cancelled','completed','expired'
  ));
