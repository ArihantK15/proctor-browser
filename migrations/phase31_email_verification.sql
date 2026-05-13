-- Email verification for teachers + student accounts.
-- DELIBERATELY no backfill: every existing user re-verifies on next login.

ALTER TABLE teachers ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
