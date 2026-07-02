-- phase150: guard against out-of-order Razorpay webhook delivery.
--
-- Razorpay's own docs warn webhooks are not guaranteed to arrive in order.
-- The razorpay_webhook handler had no defence against this: a late-arriving
-- 'subscription.charged' for the cycle just before a cancellation, delivered
-- AFTER 'subscription.cancelled' already downgraded the org, would blindly
-- re-set status='active' — resurrecting access to a subscription the
-- customer had already cancelled, with the next reconcile the only thing
-- that would eventually (re-)notice and fix it.
--
-- last_webhook_event_at records the event.created_at (Razorpay's own
-- timestamp, not our receive time) of the last webhook actually APPLIED to
-- this row. The handler now compares each incoming event's created_at
-- against it and ignores (200s, does not apply, does not error) anything
-- older than what's already been applied.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS last_webhook_event_at TIMESTAMPTZ;
