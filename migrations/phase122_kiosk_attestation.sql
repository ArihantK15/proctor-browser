-- phase122: kiosk attestation columns on exam_sessions (gap #43)
-- Tracks whether the student is using the secure desktop browser.
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS kiosk_attested BOOLEAN;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS client_version TEXT;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS attested_at TIMESTAMPTZ;
