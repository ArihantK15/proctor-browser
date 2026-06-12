-- Phase 101: sweep violations older than 1 year.
--
-- Extends the TTL sweeper function (phase86) to also delete violations
-- whose created_at is older than 1 year, per the published data-retention
-- policy. Violations are considered permanent exam evidence during the
-- active exam window but become storage liability after the retention
-- period.

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
  v_violations    INTEGER := 0;
  v_total         INTEGER := 0;
BEGIN
  DELETE FROM google_oauth_states
    WHERE expires_at < now() - INTERVAL '1 hour';
  GET DIAGNOSTICS v_oauth_states = ROW_COUNT;
  RAISE NOTICE 'sweep: google_oauth_states deleted % rows', v_oauth_states;

  DELETE FROM email_otps
    WHERE (used_at IS NOT NULL AND used_at < now() - INTERVAL '7 days')
       OR (used_at IS NULL AND expires_at < now() - INTERVAL '7 days');
  GET DIAGNOSTICS v_email_otps = ROW_COUNT;
  RAISE NOTICE 'sweep: email_otps deleted % rows', v_email_otps;

  DELETE FROM refresh_tokens
    WHERE (revoked_at IS NOT NULL AND revoked_at < now() - INTERVAL '90 days')
       OR (revoked_at IS NULL AND expires_at < now() - INTERVAL '90 days');
  GET DIAGNOSTICS v_refresh_toks = ROW_COUNT;
  RAISE NOTICE 'sweep: refresh_tokens deleted % rows', v_refresh_toks;

  DELETE FROM auth_sessions
    WHERE revoked_at IS NOT NULL
      AND revoked_at < now() - INTERVAL '30 days';
  GET DIAGNOSTICS v_auth_sessions = ROW_COUNT;
  RAISE NOTICE 'sweep: auth_sessions deleted % rows', v_auth_sessions;

  DELETE FROM auth_events
    WHERE created_at < now() - INTERVAL '180 days';
  GET DIAGNOSTICS v_auth_events = ROW_COUNT;
  RAISE NOTICE 'sweep: auth_events deleted % rows', v_auth_events;

  DELETE FROM violations
    WHERE created_at IS NOT NULL
      AND created_at < now() - INTERVAL '1 year';
  GET DIAGNOSTICS v_violations = ROW_COUNT;
  RAISE NOTICE 'sweep: violations deleted % rows', v_violations;

  v_total := v_oauth_states + v_email_otps + v_refresh_toks
           + v_auth_sessions + v_auth_events + v_violations;
  RAISE NOTICE 'sweep: total deleted % rows', v_total;
  RETURN v_total;
END;
$$;

REVOKE ALL ON FUNCTION public.sweep_transient_rows() FROM PUBLIC;
