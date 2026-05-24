-- Drop the plaintext token column from org_invites.
-- All lookups switched to token_hash in phase69. Existing pending
-- invites already have token_hash backfilled (see phase69 script);
-- this drop completes the transition.
--
-- Idempotent: ALTER TABLE ... DROP COLUMN IF EXISTS is a no-op
-- when the column is already gone.

ALTER TABLE org_invites DROP COLUMN IF EXISTS token;
