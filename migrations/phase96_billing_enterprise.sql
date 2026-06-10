-- Phase 96: Enterprise billing rebuild (recurring Subscriptions only).
--
--   1. billing_events — immutable payment/event ledger. The Razorpay
--      event.id UNIQUE gives DB-durable webhook idempotency (replaces the
--      Redis-only dedup), plus an audit/reconciliation/invoice trail.
--   2. organizations.gstin — optional customer GSTIN for GST-compliant
--      Razorpay invoices.
--   3. subscriptions: dunning column (past_due_since) + a defensive status
--      CHECK over the canonical lifecycle set.
--
-- Entitlement (organizations.max_students) is a projection of subscription
-- state, reconciled in app code (services/billing.reconcile_org_entitlement).

CREATE TABLE IF NOT EXISTS billing_events (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id                 TEXT UNIQUE,          -- Razorpay event.id (idempotency)
  org_id                   UUID REFERENCES organizations(id),
  razorpay_subscription_id TEXT,
  razorpay_payment_id      TEXT,
  event_type               TEXT NOT NULL,
  status                   TEXT,                 -- our outcome: ok/ignored/grant/downgrade/…
  amount                   INTEGER,              -- paise
  currency                 TEXT DEFAULT 'INR',
  payload                  JSONB,                -- raw event for audit/reconciliation
  created_at               TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_billing_events_org   ON billing_events(org_id);
CREATE INDEX IF NOT EXISTS idx_billing_events_sub   ON billing_events(razorpay_subscription_id);
CREATE INDEX IF NOT EXISTS idx_billing_events_created ON billing_events(created_at DESC);

-- Optional GSTIN for GST invoices.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS gstin TEXT;

-- Dunning: when the subscription first entered past_due (a renewal charge
-- failed and Razorpay is retrying). Cleared on recovery.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS past_due_since TIMESTAMPTZ;

-- Defensive status CHECK over the canonical lifecycle. Wrapped so a deploy
-- with any legacy out-of-set value doesn't hard-fail the migration.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
    WHERE table_name = 'subscriptions' AND constraint_name = 'subscriptions_status_chk'
  ) THEN
    ALTER TABLE subscriptions
      ADD CONSTRAINT subscriptions_status_chk CHECK (status IN (
        'trialing','created','authenticated','active','past_due',
        'halted','cancelling','cancelled','completed','expired','paused'
      )) NOT VALID;   -- NOT VALID: enforce for new/updated rows, skip legacy scan
  END IF;
EXCEPTION WHEN others THEN
  RAISE NOTICE 'phase96: subscriptions_status_chk skipped (%): %', SQLSTATE, SQLERRM;
END $$;
