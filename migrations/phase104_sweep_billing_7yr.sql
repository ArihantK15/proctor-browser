-- Phase 104: sweep billing_events older than 7 years.
--
-- Implements the storage-limitation ceiling for financial records
-- per §44AA of PRIVACY.md (7-year tax-retention period). The
-- billing_events table is an immutable append-only event log, so a
-- time-based DELETE is both safe and required for compliance.
--
-- usage_records (per-org exam-usage snapshots) is also swept at
-- 7 years for the same reason.

CREATE OR REPLACE FUNCTION public.sweep_transient_rows()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_oauth_states   INTEGER := 0;
  v_email_otps     INTEGER := 0;
  v_refresh_toks   INTEGER := 0;
  v_auth_sessions  INTEGER := 0;
  v_auth_events    INTEGER := 0;
  v_violations     INTEGER := 0;
  v_billing_events INTEGER := 0;
  v_usage_records  INTEGER := 0;
  v_total          INTEGER := 0;
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

  DELETE FROM billing_events
    WHERE created_at < now() - INTERVAL '7 years';
  GET DIAGNOSTICS v_billing_events = ROW_COUNT;
  RAISE NOTICE 'sweep: billing_events deleted % rows', v_billing_events;

  DELETE FROM usage_records
    WHERE created_at < now() - INTERVAL '7 years';
  GET DIAGNOSTICS v_usage_records = ROW_COUNT;
  RAISE NOTICE 'sweep: usage_records deleted % rows', v_usage_records;

  v_total := v_oauth_states + v_email_otps + v_refresh_toks
           + v_auth_sessions + v_auth_events + v_violations
           + v_billing_events + v_usage_records;
  RAISE NOTICE 'sweep: total deleted % rows', v_total;
  RETURN v_total;
END;
$$;

REVOKE ALL ON FUNCTION public.sweep_transient_rows() FROM PUBLIC;
