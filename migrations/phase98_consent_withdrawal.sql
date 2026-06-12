-- Phase 98: Consent withdrawal support (GDPR Art 7(3) / DPDP Act §7(4)).
--
-- Withdrawing consent does NOT delete the original consent record (it must
-- be retained as proof under §7(2)). Instead we add:
--   withdrawn_at  — when the user withdrew consent (NULL = still active)
--   withdrawn_ip  — IP from which the withdrawal was made
--
-- The application uses withdrawn_at IS NULL to determine whether a consent
-- type is currently active. The original record remains for audit.

ALTER TABLE consent_records ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;
ALTER TABLE consent_records ADD COLUMN IF NOT EXISTS withdrawn_ip TEXT;
