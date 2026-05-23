-- Backfill password_changed_at for accounts that predate phase60_local_auth.
--
-- Without a value here, password-reset tokens for legacy accounts omit the
-- `pwc` claim, which means the confirm endpoint can't enforce single-use
-- binding. An attacker who captures the reset link could replay it before
-- the legitimate user's first use completes (TOCTOU race), or both the
-- token's claim and live column are NULL and the check falls through.
--
-- Setting a historical timestamp is safe: any in-flight legacy token was
-- minted WITHOUT pwc, so the confirm check still sees pwc_claim=None but
-- now live_pwc is non-None → the elif branch catches it as "expired".
-- This forces the user to request a fresh reset link, which WILL embed
-- the new password_changed_at, giving full single-use protection.

UPDATE teachers
SET password_changed_at = NOW()
WHERE password_changed_at IS NULL;

UPDATE student_accounts
SET password_changed_at = NOW()
WHERE password_changed_at IS NULL;
