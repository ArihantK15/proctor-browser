-- phase114: support/admin overrides (cap override + billing credit) (gap #13)
--
-- Two columns on organizations:
--   max_students_override INTEGER NULL       — NULL = use plan-derived cap
--   billing_credit_inr    INTEGER NOT NULL DEFAULT 0  — INR offset for overage
--
-- One column on overage_charges:
--   credit_applied_inr    INTEGER NOT NULL DEFAULT 0  — how much credit was consumed

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS max_students_override INTEGER,
  ADD COLUMN IF NOT EXISTS billing_credit_inr    INTEGER NOT NULL DEFAULT 0;

ALTER TABLE overage_charges
  ADD COLUMN IF NOT EXISTS credit_applied_inr INTEGER NOT NULL DEFAULT 0;
