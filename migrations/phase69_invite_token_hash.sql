-- Hash org invite tokens before storage — 2026-05-23.
--
-- Audit M13: invite tokens were stored plaintext in org_invites.token
-- and used directly as the DB lookup key. A read-only DB compromise
-- would expose every unused invite token, letting an attacker join
-- any organisation they have an unaccepted invite URL for.
--
-- Strategy:
--   1. Add a `token_hash` column (SHA-256 hex, 64 chars).
--   2. Backfill hash for every still-pending invite so existing
--      invite URLs keep working after deploy.
--   3. App code now SELECTs by token_hash; INSERTs populate both
--      `token` (kept for backward compat during transition) and
--      `token_hash`. A follow-up migration will drop `token` after
--      we verify everything works end-to-end.
--
-- SHA-256 is fine here even though it's "just a hash" (not a slow
-- KDF like bcrypt): invite tokens are 128-bit UUIDs with full
-- entropy, so brute-forcing the original from the hash is
-- mathematically infeasible (2^128 search space). bcrypt would be
-- overkill and add ~100ms to every invite-link click.
--
-- IF NOT EXISTS makes this idempotent. Safe to re-run.

ALTER TABLE org_invites ADD COLUMN IF NOT EXISTS token_hash TEXT;

-- Index for the lookup path. Partial index on still-usable rows
-- (most queries filter status='pending').
CREATE INDEX IF NOT EXISTS idx_org_invites_token_hash
    ON org_invites (token_hash)
    WHERE status = 'pending';

-- Backfill: compute SHA-256 hex of the existing plaintext token for
-- every pending invite. The pgcrypto extension provides digest().
CREATE EXTENSION IF NOT EXISTS pgcrypto;

UPDATE org_invites
   SET token_hash = encode(digest(token, 'sha256'), 'hex')
 WHERE token IS NOT NULL
   AND token_hash IS NULL;
