-- Down migration for phase123_attest_nonce.sql (Command A).
ALTER TABLE exam_sessions DROP COLUMN IF EXISTS attest_nonce_issued_at;
ALTER TABLE exam_sessions DROP COLUMN IF EXISTS attest_nonce;
