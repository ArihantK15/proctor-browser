-- Phase 105: right-to-object records (GDPR Art 21 / DPDP Act §9).
--
-- When a data subject objects to processing, Procta records the
-- objection and routes it to the controller (the teacher/school for
-- students; privacy@procta.net for teachers).
--
-- These records are retained after account deletion for audit-proof
-- purposes (same as consent_records).

CREATE TABLE IF NOT EXISTS objection_records (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,
  user_type   TEXT NOT NULL CHECK (user_type IN ('student','teacher')),
  grounds     TEXT,
  scope       TEXT NOT NULL DEFAULT 'all'
                CHECK (scope IN ('proctoring','all')),
  status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','routed','resolved')),
  routed_to   TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);
