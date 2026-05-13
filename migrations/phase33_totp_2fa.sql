-- TOTP 2FA — mandatory for everyone after 30-day grace.

ALTER TABLE teachers ADD COLUMN IF NOT EXISTS totp_secret TEXT;                -- Fernet-encrypted
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS totp_enabled_at TIMESTAMPTZ;
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS backup_codes_hash JSONB DEFAULT '[]'::JSONB;
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS totp_grace_started_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS totp_enabled_at TIMESTAMPTZ;
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS backup_codes_hash JSONB DEFAULT '[]'::JSONB;
ALTER TABLE student_accounts ADD COLUMN IF NOT EXISTS totp_grace_started_at TIMESTAMPTZ DEFAULT now();
