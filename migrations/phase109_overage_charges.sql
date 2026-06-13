-- phase109: Overage-charges ledger for billing per-student overage.
--
-- When subscription.charged fires at the cycle boundary, the system
-- computes how many students exceeded the plan cap during the cycle and
-- creates a Razorpay subscription add-on for overage × ₹80/student.
-- This table is the idempotency + audit + display surface.
--
-- The UNIQUE(org_id, period_start) constraint is the idempotency guard:
-- a webhook retry for the same cycle cannot double-charge.

CREATE TABLE IF NOT EXISTS overage_charges (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id),
  period_start     TIMESTAMPTZ NOT NULL,
  period_end       TIMESTAMPTZ NOT NULL,
  students_used    INTEGER NOT NULL,
  plan_limit       INTEGER NOT NULL,
  overage_count    INTEGER NOT NULL,
  amount_inr       INTEGER NOT NULL,
  razorpay_addon_id TEXT,
  status           TEXT NOT NULL DEFAULT 'pending',  -- pending|charged|skipped|failed
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT overage_charges_period_uniq UNIQUE (org_id, period_start)
);
