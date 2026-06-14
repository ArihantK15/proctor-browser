-- phase123: per-session attestation nonce (Command A)
-- Server-issued, single-use, session-bound nonce for each kiosk attestation.
-- This prevents capture-and-replay of signed attestation payloads by making
-- every attestation unique.
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS attest_nonce TEXT;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS attest_nonce_issued_at TIMESTAMPTZ;
