-- Phase 100: dedicated billing email address for dunning notifications.
--
-- Adds billing_email column to organizations. When populated, dunning
-- emails (payment failures, renewal reminders) go to this address instead
-- of falling back to the first org admin found.
--
-- The fallback in _notify_payment_issue (app/routers/billing.py) is:
--   1. organizations.billing_email (if set)
--   2. first teacher with org_role='admin' in the org

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS billing_email TEXT;
