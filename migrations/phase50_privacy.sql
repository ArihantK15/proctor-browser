-- Phase 50: Privacy compliance (consent records, data export/delete)
-- Required for DPDP readiness.

CREATE TABLE IF NOT EXISTS consent_records (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       TEXT NOT NULL,
  user_type     TEXT NOT NULL CHECK (user_type IN ('teacher','student')),
  consent_type  TEXT NOT NULL,  -- 'signup_terms', 'privacy_policy', 'phone_camera'
  ip_address    TEXT DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_consent_user
  ON consent_records (user_id, created_at DESC);
