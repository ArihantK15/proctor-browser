-- Down migration for phase122_kiosk_attestation.sql (gap #43).
ALTER TABLE exam_sessions DROP COLUMN IF EXISTS attested_at;
ALTER TABLE exam_sessions DROP COLUMN IF EXISTS client_version;
ALTER TABLE exam_sessions DROP COLUMN IF EXISTS kiosk_attested;
