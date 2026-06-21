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
  -- FK to teachers(id) is intentionally omitted in this fixture: teachers is
  -- created later in this file (it FKs org_id back to organizations), so an
  -- inline REFERENCES here would be a forward reference and fail to build.
  -- The prod migration (phase107) keeps the real FK — teachers exists there.
  deleted_by   UUID,                           -- phase107
  delete_reason TEXT,                          -- phase107
  require_2fa          BOOLEAN NOT NULL DEFAULT FALSE,  -- phase111
  max_students_override INTEGER,                          -- phase114 (gap #13)
  billing_credit_inr    INTEGER NOT NULL DEFAULT 0,       -- phase114 (gap #13)
  owner_teacher_id      UUID,                              -- phase135 (billing-owner decouple); FK to teachers omitted (forward ref, see note above)
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  -- gstin added by phase96
);

CREATE INDEX IF NOT EXISTS idx_organizations_active
  ON organizations (id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS subscriptions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID REFERENCES organizations(id),
  plan                     TEXT,
  status                   TEXT,
  trial_end                TIMESTAMPTZ,
  razorpay_subscription_id TEXT,
  razorpay_order_id        TEXT,
  current_period_start     TIMESTAMPTZ,
  current_period_end       TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  scheduled_plan           TEXT,
  scheduled_plan_effective_at TIMESTAMPTZ,
  -- past_due_since + status CHECK added by phase96
  billing_cycle            TEXT NOT NULL DEFAULT 'monthly'
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

-- Overage charge ledger (phase109). Idempotent per (org_id, period_start).
CREATE TABLE IF NOT EXISTS overage_charges (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id),
  period_start     TIMESTAMPTZ NOT NULL,
  period_end       TIMESTAMPTZ NOT NULL,
  students_used    INTEGER NOT NULL,
  plan_limit       INTEGER NOT NULL,
  overage_count    INTEGER NOT NULL,
  amount_inr         INTEGER NOT NULL,
  credit_applied_inr INTEGER NOT NULL DEFAULT 0,        -- phase114 (gap #13)
  razorpay_addon_id  TEXT,
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT overage_charges_period_uniq UNIQUE (org_id, period_start)
);

CREATE TABLE IF NOT EXISTS teachers (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID REFERENCES organizations(id),
  org_role   TEXT NOT NULL DEFAULT 'teacher',
  email      TEXT,
  full_name  TEXT,
  supabase_uid        TEXT,                  -- auth identity (signup tx)
  password_hash       TEXT,                  -- local-auth (signup tx)
  auth_provider       TEXT,                  -- 'local' | 'supabase' (signup tx)
  password_changed_at TIMESTAMPTZ,           -- local-auth (signup tx)
  status     TEXT DEFAULT 'active',          -- phase62
  org_suspended_at TIMESTAMPTZ,              -- phase108
  notification_prefs JSONB NOT NULL DEFAULT '{}'::jsonb,  -- phase112
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
  paused_at               TIMESTAMPTZ,
  kiosk_attested          BOOLEAN,
  client_version          TEXT,
  attested_at             TIMESTAMPTZ,
  attest_nonce            TEXT,
  attest_nonce_issued_at  TIMESTAMPTZ
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

CREATE TABLE IF NOT EXISTS question_versions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id     TEXT NOT NULL,
  teacher_id      TEXT NOT NULL,
  version_number  INTEGER NOT NULL,
  change_type     TEXT NOT NULL CHECK (change_type IN ('create','update','delete')),
  snapshot        JSONB NOT NULL,
  changed_by      TEXT,
  changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_qversions_q
  ON question_versions(question_id, version_number DESC);

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
  early_join_minutes      INTEGER NOT NULL DEFAULT 15,
  shuffle_questions       BOOLEAN DEFAULT TRUE,
  shuffle_options         BOOLEAN DEFAULT TRUE,
  phone_camera_enabled    BOOLEAN DEFAULT FALSE,
  proctoring_sensitivity  TEXT DEFAULT 'balanced',
  audio_keywords          TEXT,
  audio_keywords_language TEXT DEFAULT 'en',
  archived_at             TIMESTAMPTZ,
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

-- Coupon codes for Razorpay Offers (phase120).
CREATE TABLE IF NOT EXISTS coupons (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code              TEXT NOT NULL UNIQUE,
  razorpay_offer_id TEXT NOT NULL,
  description       TEXT,
  max_redemptions   INTEGER,
  times_redeemed    INTEGER NOT NULL DEFAULT 0,
  expires_at        TIMESTAMPTZ,
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  created_by        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE TABLE IF NOT EXISTS exam_time_extensions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID NOT NULL,
    exam_id       TEXT NOT NULL,
    roll_number   TEXT NOT NULL,
    extra_minutes INTEGER NOT NULL DEFAULT 0,
    created_by    UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT exam_time_ext_uniq UNIQUE (teacher_id, exam_id, roll_number)
);

-- Tables below are minimal stubs for the teacher-reassign integration test.
-- They only need enough columns for the teaching-data remap UPDATE to succeed
-- (i.e. a teacher_id column).  Migrations define the real schema.

CREATE TABLE IF NOT EXISTS student_groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID
);

CREATE TABLE IF NOT EXISTS student_group_members (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   UUID,
    student_id UUID,
    teacher_id UUID
);

CREATE TABLE IF NOT EXISTS student_invites (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID,
    email         TEXT,
    status        TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS exam_batch_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID,
    exam_id       TEXT,
    batch_id      TEXT
);

CREATE TABLE IF NOT EXISTS exam_group_assignments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID,
    exam_id    TEXT,
    group_id   UUID,
    UNIQUE (exam_id, group_id)
);

CREATE TABLE IF NOT EXISTS exam_templates (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id     UUID,
    template_name  TEXT
);

CREATE TABLE IF NOT EXISTS appeals (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id     UUID,
    session_key    TEXT,
    appeal_type    TEXT,
    status         TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS grading_audit (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID,
    session_key TEXT
);

CREATE TABLE IF NOT EXISTS google_classroom_links (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID,
    classroom_id  TEXT
);

-- Edge Compiler (phase141). Plain fixture — no RLS here; is_fully_solved is a
-- plain BOOLEAN (the migration computes it GENERATED, the fixture doesn't need to).
CREATE TABLE IF NOT EXISTS coding_test_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id     TEXT NOT NULL,
    teacher_id      UUID,
    idx             INTEGER NOT NULL,
    input           TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'hidden',
    float_tolerance DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coding_submissions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id                   TEXT,
    teacher_id                UUID,
    session_id                TEXT,
    student_id                UUID,
    question_id               TEXT,
    language                  TEXT,
    test_cases_total          INTEGER,
    test_cases_passed         INTEGER,
    is_fully_solved           BOOLEAN,
    average_execution_ms      INTEGER,
    memory_consumed_kb        INTEGER,
    source_code               TEXT,
    keystroke_rhythm_variance DOUBLE PRECISION,
    paste_attempts            INTEGER DEFAULT 0,
    focus_loss_count          INTEGER DEFAULT 0,
    submitted_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
