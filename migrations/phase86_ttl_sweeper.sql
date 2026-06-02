-- =====================================================================
-- Phase 86 — Transient-row TTL sweeper function
-- =====================================================================
-- Several tables accumulate ephemeral / audit rows that no production
-- query path ever reads after a known lifetime, but nothing cleans
-- them up — so they grow forever:
--
--   • google_oauth_states  — OAuth flow nonces, expires_at ~10 min
--   • email_otps           — 6-digit codes, expires_at typically 10 min
--   • refresh_tokens       — revoked or expired JWT refresh tokens
--   • auth_sessions        — revoked sessions
--   • auth_events          — login/security audit log (retention bound)
--
-- This migration creates a single SECURITY DEFINER function that
-- deletes aged rows from each table using retention windows tuned for
-- forensic value vs storage cost:
--
--   • google_oauth_states: 1 hour past expires_at
--   • email_otps:          7 days past expires_at OR used_at
--   • refresh_tokens:      90 days past revoked_at OR expires_at
--   • auth_sessions:       30 days past revoked_at
--   • auth_events:         180 days past created_at (compliance window)
--
-- The function RAISE NOTICEs each per-table deletion count so the
-- operator can see what was swept. It returns the total number of
-- rows removed.
--
-- SCHEDULING (operator action — pick one, OUTSIDE this migration):
--
--   1. pg_cron extension (cleanest, runs inside Postgres):
--        CREATE EXTENSION IF NOT EXISTS pg_cron;
--        SELECT cron.schedule('ttl-sweeper-nightly', '17 3 * * *',
--                             $$SELECT public.sweep_transient_rows();$$);
--
--   2. System cron + psql (no extension required):
--        17 3 * * *  psql "$DATABASE_URL" -c \
--                    "SELECT public.sweep_transient_rows();" > /var/log/ttl-sweep.log 2>&1
--
--   3. RQ scheduled job in app/jobs/ — define a periodic task that
--      shells out to the function via asyncpg.execute().
--
-- Pick whichever fits your ops setup. The function is idempotent and
-- safe to run as often as you like; running hourly instead of nightly
-- just trims the table sooner.
--
-- Re-running this migration replaces the function definition (CREATE
-- OR REPLACE). Re-applying is always safe.
-- =====================================================================

CREATE OR REPLACE FUNCTION public.sweep_transient_rows()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_oauth_states  INTEGER := 0;
  v_email_otps    INTEGER := 0;
  v_refresh_toks  INTEGER := 0;
  v_auth_sessions INTEGER := 0;
  v_auth_events   INTEGER := 0;
  v_total         INTEGER := 0;
BEGIN
  -- ── google_oauth_states ────────────────────────────────────────
  -- OAuth state nonces live ~10 minutes between init and callback.
  -- Anything past expires_at + 1 hour grace is dead weight.
  DELETE FROM google_oauth_states
    WHERE expires_at < now() - INTERVAL '1 hour';
  GET DIAGNOSTICS v_oauth_states = ROW_COUNT;
  RAISE NOTICE 'sweep: google_oauth_states deleted % rows', v_oauth_states;

  -- ── email_otps ─────────────────────────────────────────────────
  -- OTP codes expire in minutes. Keep used/expired rows 7 days for
  -- forensic correlation, then drop.
  DELETE FROM email_otps
    WHERE (used_at IS NOT NULL AND used_at < now() - INTERVAL '7 days')
       OR (used_at IS NULL AND expires_at < now() - INTERVAL '7 days');
  GET DIAGNOSTICS v_email_otps = ROW_COUNT;
  RAISE NOTICE 'sweep: email_otps deleted % rows', v_email_otps;

  -- ── refresh_tokens ─────────────────────────────────────────────
  -- Revoked or expired tokens are unusable. Keep 90 days for the
  -- security audit trail (linking a refresh chain to a session).
  DELETE FROM refresh_tokens
    WHERE (revoked_at IS NOT NULL AND revoked_at < now() - INTERVAL '90 days')
       OR (revoked_at IS NULL AND expires_at < now() - INTERVAL '90 days');
  GET DIAGNOSTICS v_refresh_toks = ROW_COUNT;
  RAISE NOTICE 'sweep: refresh_tokens deleted % rows', v_refresh_toks;

  -- ── auth_sessions ──────────────────────────────────────────────
  -- Revoked sessions are inactive. Keep 30 days post-revocation for
  -- audit; anything older is replaced by auth_events anyway.
  DELETE FROM auth_sessions
    WHERE revoked_at IS NOT NULL
      AND revoked_at < now() - INTERVAL '30 days';
  GET DIAGNOSTICS v_auth_sessions = ROW_COUNT;
  RAISE NOTICE 'sweep: auth_sessions deleted % rows', v_auth_sessions;

  -- ── auth_events ────────────────────────────────────────────────
  -- Security audit log. 180 days is the standard SaaS compliance
  -- retention. If you need longer for a specific incident, archive
  -- the relevant subset BEFORE the next sweep run.
  DELETE FROM auth_events
    WHERE created_at < now() - INTERVAL '180 days';
  GET DIAGNOSTICS v_auth_events = ROW_COUNT;
  RAISE NOTICE 'sweep: auth_events deleted % rows', v_auth_events;

  v_total := v_oauth_states + v_email_otps + v_refresh_toks
           + v_auth_sessions + v_auth_events;
  RAISE NOTICE 'sweep: total deleted % rows', v_total;
  RETURN v_total;
END;
$$;

-- Lock the function down — only superuser / db owner can call it.
-- (The function itself is SECURITY DEFINER so the caller doesn't need
-- DELETE perms on each table; this revoke prevents random app roles
-- from triggering a sweep at the wrong moment.)
REVOKE ALL ON FUNCTION public.sweep_transient_rows() FROM PUBLIC;

-- =====================================================================
-- Post-migration verification (manual run):
--
--   SELECT public.sweep_transient_rows();
--
-- Expected: returns an integer (total rows swept), and the NOTICE
-- stream above prints per-table counts. First run on a long-running
-- DB will clear a backlog; subsequent runs typically delete few/zero
-- rows on a fresh sweep cycle.
-- =====================================================================
