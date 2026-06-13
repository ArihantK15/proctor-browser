-- Integration-test fixture schema (NOT the production baseline).
--
-- WHY THIS EXISTS: the repo's migrations/ are INCREMENTAL only — the core
-- tables (teachers, exam_sessions, answers, violations, question_bank) live in
-- the original Supabase pg_dump baseline that was never committed. So the repo
-- cannot rebuild its own DB, and migrations don't replay from an empty database.
-- This file is a focused, hand-built schema for the tables the integration
-- tests exercise. It is deliberately PRE-phase96 for organizations/subscriptions
-- so the conftest can apply migrations/phase96_billing_enterprise.sql ON TOP and
-- prove that migration's DDL really works.
--
-- It is NOT authoritative and must NOT be used for disaster recovery. To make a
-- real baseline, capture prod with `pg_dump --schema-only` (see the spec doc
-- docs/superpowers/specs/2026-06-10-deploy-safety-and-db-tests.md).
--
-- Column sets are taken from the exact queries the tested code paths run:
--   reconcile_org_entitlement / record_billing_event / billing_event_seen,
--   auth.scope.scope_to_teacher_ids / assert_session_accessible,
--   repositories.sessions.assert_session_owned.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS organizations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT,
  slug         TEXT,
  max_students INTEGER NOT NULL DEFAULT 30,
  billing_email                TEXT,             -- phase100
  auth_session_timeout_minutes INTEGER,          -- phase102
  max_concurrent_auth_sessions INTEGER,          -- phase102
  deleted_at   TIMESTAMPTZ,                    -- phase107
  deleted_by   UUID REFERENCES teachers(id),   -- phase107
  delete_reason TEXT,                          -- phase107
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  -- gstin added by phase96
);

CREATE INDEX IF NOT EXISTS idx_organizations_active
  ON organizations (id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS subscriptions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID REFERENCES organizations(id),
  plan                     TEXT,
  status                   TEXT,
  razorpay_subscription_id TEXT,
  razorpay_order_id        TEXT,
  current_period_start     TIMESTAMPTZ,
  current_period_end       TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
  -- past_due_since + status CHECK added by phase96
);

-- Immutable financial event log (phase96). Swept at 7 years (phase104).
CREATE TABLE IF NOT EXISTS billing_events (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID REFERENCES organizations(id),
  event_id                 TEXT,
  event_type               TEXT,
  razorpay_payment_id      TEXT,
  razorpay_subscription_id TEXT,
  amount                   INTEGER,
  currency                 TEXT,
  status                   TEXT,
  payload                  JSONB,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-org exam-usage snapshots. Swept at 7 years (phase104).
CREATE TABLE IF NOT EXISTS usage_records (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID REFERENCES organizations(id),
  exam_attempts   INTEGER NOT NULL DEFAULT 0,
  overage         INTEGER NOT NULL DEFAULT 0,
  overage_amount  INTEGER NOT NULL DEFAULT 0,
  period_end      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teachers (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID REFERENCES organizations(id),
  org_role   TEXT NOT NULL DEFAULT 'teacher',
  email      TEXT,
  full_name  TEXT,
  status     TEXT DEFAULT 'active',          -- phase62
  org_suspended_at TIMESTAMPTZ,              -- phase108
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exam_sessions (
  session_key             TEXT PRIMARY KEY,
  teacher_id              UUID,
  exam_id                 TEXT,
  roll_number             TEXT,
  full_name               TEXT,
  email                   TEXT,
  status                  TEXT,
  started_at              TIMESTAMPTZ,
  submitted_at            TIMESTAMPTZ,
  score                   NUMERIC,
  total                   NUMERIC,
  percentage              NUMERIC,
  risk_score              NUMERIC,
  time_taken_secs         INTEGER,
  student_id              UUID,
  room_cam_status         TEXT,
  room_cam_approved_at    TIMESTAMPTZ,
  terminated_by           TEXT,
  termination_reason_code TEXT,
  termination_reason_text TEXT,
  paused_secs_total       INTEGER,
  paused_at               TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS violations (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_key    TEXT,
  teacher_id     UUID,
  violation_type TEXT,
  severity       TEXT,
  details        TEXT,
  -- compute_risk_score filters `WHERE dismissed_at IS NULL`; without the
  -- column the risk path silently errors on every submit.
  dismissed_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS answers (
  session_key TEXT,
  question_id TEXT,
  teacher_id  UUID,
  exam_id     TEXT,
  answer      TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (session_key, question_id)
);

CREATE TABLE IF NOT EXISTS question_bank (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id    UUID,
  question      TEXT,
  question_type TEXT,
  options       TEXT,
  correct       TEXT,
  image_url     TEXT,
  tags          TEXT[],
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-exam delivered questions. options is TEXT (the writer json.dumps it).
-- The UNIQUE(teacher_id,exam_id,question_id) is the constraint update_questions
-- upserts against — the whole point of the re-save test.
CREATE TABLE IF NOT EXISTS questions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id       UUID,
  exam_id          TEXT,
  question_id      TEXT,
  question         TEXT,
  options          TEXT,
  correct          TEXT,
  question_type    TEXT,
  image_url        TEXT,
  reference_answer TEXT,
  rubric           TEXT,
  max_score        NUMERIC,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (teacher_id, exam_id, question_id)
);

-- Columns mirror what app/repositories/questions.py:_EXAM_CONFIG_COLUMNS
-- actually SELECTs; load_exam_config reads all of them, so a minimal table
-- 500s with UndefinedColumnError instead of exercising the real read path.
CREATE TABLE IF NOT EXISTS exam_config (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id              UUID,
  exam_id                 TEXT,
  exam_title              TEXT,
  duration_minutes        INTEGER,
  access_code             TEXT,
  starts_at               TIMESTAMPTZ,
  ends_at                 TIMESTAMPTZ,
  shuffle_questions       BOOLEAN DEFAULT TRUE,
  shuffle_options         BOOLEAN DEFAULT TRUE,
  phone_camera_enabled    BOOLEAN DEFAULT FALSE,
  proctoring_sensitivity  TEXT DEFAULT 'balanced',
  audio_keywords          TEXT,
  audio_keywords_language TEXT DEFAULT 'en',
  pass_mark               SMALLINT NOT NULL DEFAULT 40,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (teacher_id, exam_id)
);

-- Mirrors the production invite_send_counters: teacher_id is UUID, so the
-- atomic cap claim must cast its text param ($1::uuid). The (teacher_id, day)
-- UNIQUE is what makes the INSERT ... ON CONFLICT DO NOTHING idempotent.
CREATE TABLE IF NOT EXISTS invite_send_counters (
  teacher_id UUID NOT NULL,
  day        DATE NOT NULL,
  count      INTEGER NOT NULL DEFAULT 0,
  UNIQUE (teacher_id, day)
);

-- Breach incident records (phase103).
CREATE TABLE IF NOT EXISTS breach_incidents (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discovered_at           TIMESTAMPTZ NOT NULL,
  description             TEXT NOT NULL,
  data_categories         TEXT,
  affected_scope          JSONB NOT NULL DEFAULT '{}',
  risk_level              TEXT NOT NULL DEFAULT 'unknown',
  role                    TEXT NOT NULL DEFAULT 'processor',
  status                  TEXT NOT NULL DEFAULT 'open',
  authority_notified_at   TIMESTAMPTZ,
  controllers_notified_at TIMESTAMPTZ,
  subjects_notified_at    TIMESTAMPTZ,
  created_by              UUID,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Right-to-object records (phase105). Retained after account deletion.
CREATE TABLE IF NOT EXISTS objection_records (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,
  user_type   TEXT NOT NULL,
  grounds     TEXT,
  scope       TEXT NOT NULL DEFAULT 'all',
  status      TEXT NOT NULL DEFAULT 'open',
  routed_to   TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

-- Transient auth tables the TTL sweeper (sweep_transient_rows, phase86/101/104)
-- DELETEs from. The phase104 sweep test invokes the WHOLE function, so every
-- table it touches must exist here or the first DELETE aborts the call.
CREATE TABLE IF NOT EXISTS google_oauth_states (
  state      TEXT PRIMARY KEY,
  teacher_id UUID,
  expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS email_otps (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID,
  user_kind  TEXT,
  purpose    TEXT,
  code_hash  TEXT,
  attempts   INTEGER NOT NULL DEFAULT 0,
  used_at    TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  jti             TEXT PRIMARY KEY,
  user_id         UUID,
  kind            TEXT,
  ip              TEXT,
  user_agent      TEXT,
  replaced_by_jti TEXT,
  issued_at       TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  jti          TEXT PRIMARY KEY,
  user_id      UUID,
  user_kind    TEXT,
  ip           TEXT,
  user_agent   TEXT,
  issued_at    TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS auth_events (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID,
  user_kind  TEXT,
  email      TEXT,
  event_type TEXT,
  ip         TEXT,
  user_agent TEXT,
  meta       JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Student roster. id is BIGSERIAL to match production (the quota trigger and
-- the AGS grade-passback path both resolve org via teacher_id → teachers.org_id,
-- so teacher_id is the column that matters). The phase90/91 quota trigger is
-- attached to this table in conftest.py so the REAL trigger DDL is exercised.
CREATE TABLE IF NOT EXISTS students (
  id                           BIGSERIAL PRIMARY KEY,
  roll_number                  TEXT NOT NULL,
  full_name                    TEXT,
  email                        TEXT,
  teacher_id                   UUID,
  account_id                   UUID,
  org_id                       UUID,
  date_of_birth                DATE,
  guardian_email               TEXT,
  guardian_consent_token_hash  TEXT,
  guardian_consent_requested_at TIMESTAMPTZ,
  guardian_consent_granted_at  TIMESTAMPTZ,
  guardian_consent_denied_at   TIMESTAMPTZ,
  created_at                   TIMESTAMPTZ DEFAULT now(),
  removed_at                   TIMESTAMPTZ,
  UNIQUE (roll_number, teacher_id)
);
