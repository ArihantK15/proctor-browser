-- Email-OTP 2FA replacing TOTP (Google Authenticator) — 2026-05-23.
--
-- Decision: drop TOTP entirely in favour of email-OTP 2FA. Rationale
-- in HANDOFF.md — operational simplicity, removes QR/CSP bug
-- category, universal device support, zero app install for users.
--
-- This migration adds the new column. It does NOT drop the old TOTP
-- columns (totp_secret, totp_enabled_at, backup_codes_hash,
-- totp_grace_started_at) so the data remains available if we ever
-- need to migrate it back. The application code stops reading them
-- after this commit lands.
--
-- Auto-migration of existing TOTP users: any teacher with
-- totp_enabled_at NOT NULL gets their email_2fa_enabled_at set to
-- the same timestamp — they're already a 2FA user, just switching
-- delivery channel.
--
-- IF NOT EXISTS makes this idempotent. Safe to re-run.

ALTER TABLE teachers ADD COLUMN IF NOT EXISTS email_2fa_enabled_at TIMESTAMPTZ;
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS email_2fa_enabled_at TIMESTAMPTZ;

-- Auto-migrate existing TOTP-enrolled users → email-OTP. They keep
-- the protection without having to re-enrol. On their next login
-- the dashboard will show a one-time banner explaining the switch.
UPDATE teachers
   SET email_2fa_enabled_at = totp_enabled_at
 WHERE totp_enabled_at IS NOT NULL
   AND email_2fa_enabled_at IS NULL;

UPDATE student_accounts
   SET email_2fa_enabled_at = totp_enabled_at
 WHERE totp_enabled_at IS NOT NULL
   AND email_2fa_enabled_at IS NULL;
