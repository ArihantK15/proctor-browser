-- Phase 106: Guardian consent columns for student privacy (GDPR Art 8 / COPPA).
--
-- When a student is a minor (date_of_birth → age < 18), the system
-- requires guardian_email and auto-sends a consent request. The student
-- is blocked from validating into any exam until the guardian clicks
-- "Grant" on the consent link.
--
-- The consent_token_hash is SHA-256 of a UUID4; the raw token is
-- never stored. Guardians visit /guardian-consent/<raw-token> which
-- renders a Grant / Deny page. POST /api/v1/guardian/consent records
-- the decision and writes a consent_records proof row.

ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth                DATE;
ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_email               VARCHAR(255);
ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_consent_token_hash  TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_consent_requested_at TIMESTAMPTZ;
ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_consent_granted_at  TIMESTAMPTZ;
ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_consent_denied_at   TIMESTAMPTZ;
