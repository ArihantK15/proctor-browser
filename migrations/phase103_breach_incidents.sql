-- Phase 103: breach incident tracking (Art 33(5) record).
--
-- Tracks personal-data breach lifecycle: discovery, containment,
-- regulator notification, controller notification, data-subject
-- notification, and closure. All CHECK constraints must match the
-- Python enum values used in app/routers/admin_breach.py.

CREATE TABLE IF NOT EXISTS breach_incidents (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discovered_at           TIMESTAMPTZ NOT NULL,
  description             TEXT NOT NULL,
  data_categories         TEXT,
  affected_scope          JSONB NOT NULL DEFAULT '{}',
  risk_level              TEXT NOT NULL DEFAULT 'unknown'
                            CHECK (risk_level IN ('low','medium','high','unknown')),
  role                    TEXT NOT NULL DEFAULT 'processor'
                            CHECK (role IN ('processor','controller','both')),
  status                  TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','contained','notified','closed')),
  authority_notified_at   TIMESTAMPTZ,
  controllers_notified_at TIMESTAMPTZ,
  subjects_notified_at    TIMESTAMPTZ,
  created_by              UUID,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
