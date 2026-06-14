-- Reverse phase114: drop support override columns.
ALTER TABLE overage_charges
  DROP COLUMN IF EXISTS credit_applied_inr;
ALTER TABLE organizations
  DROP COLUMN IF EXISTS billing_credit_inr,
  DROP COLUMN IF EXISTS max_students_override;
