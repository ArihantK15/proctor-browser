-- =====================================================================
-- phase130: persist PKCE code_verifier for the Google OAuth flow
-- =====================================================================
-- google-auth-oauthlib >=1.x auto-generates a PKCE code_verifier when the
-- authorization URL is built (/api/v1/google/auth) and sends its code_challenge
-- to Google. At token exchange (/api/v1/google/callback) Google requires the
-- SAME verifier, but the callback constructs a brand-new Flow that never saw
-- it — so the exchange failed with "(invalid_grant) Missing code verifier."
--
-- Fix: stash the verifier alongside the one-time state row and replay it in
-- exchange_code(). Additive, nullable column — no backfill needed (states are
-- ephemeral, 10-min TTL, cleaned up on callback).
-- =====================================================================

BEGIN;

ALTER TABLE google_oauth_states ADD COLUMN IF NOT EXISTS code_verifier TEXT;

COMMIT;
