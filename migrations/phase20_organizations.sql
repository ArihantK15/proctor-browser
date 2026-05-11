-- Phase 20: Organizations & multi-tenant model
--
-- Adds org/tenant model: organizations, org role on teachers,
-- org scope on students, org invitations, and subscriptions.
-- Every teacher belongs to exactly one org; students are org-scoped.
--
-- Run this against Supabase before deploying the Phase 20 backend.

-- ── 1. ORGANIZATIONS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,
  slug         TEXT UNIQUE NOT NULL,
  max_students INTEGER NOT NULL DEFAULT 30,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- ── 2. TEACHERS: add org columns ────────────────────────────────
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE teachers ADD COLUMN IF NOT EXISTS org_role TEXT NOT NULL DEFAULT 'teacher';

-- ── 3. STUDENTS: add org column ─────────────────────────────────
ALTER TABLE students ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- ── 4. ORG_INVITES ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS org_invites (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organizations(id),
  token       TEXT UNIQUE NOT NULL,
  email       TEXT NOT NULL,
  full_name   TEXT,
  status      TEXT NOT NULL DEFAULT 'pending',
  invited_by  UUID REFERENCES teachers(id),
  expires_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now(),
  accepted_at TIMESTAMPTZ
);

-- ── 5. SUBSCRIPTIONS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  UUID UNIQUE NOT NULL REFERENCES organizations(id),
  plan                    TEXT NOT NULL DEFAULT 'starter',
  status                  TEXT NOT NULL DEFAULT 'trialing',
  trial_end               TIMESTAMPTZ,
  razorpay_subscription_id TEXT,
  razorpay_order_id       TEXT,
  current_period_start    TIMESTAMPTZ,
  current_period_end      TIMESTAMPTZ,
  created_at              TIMESTAMPTZ DEFAULT now(),
  updated_at              TIMESTAMPTZ DEFAULT now()
);

-- ── Indexes ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_teachers_org_id ON teachers(org_id);
CREATE INDEX IF NOT EXISTS idx_students_org_id ON students(org_id);
CREATE INDEX IF NOT EXISTS idx_org_invites_org_id ON org_invites(org_id);
CREATE INDEX IF NOT EXISTS idx_org_invites_token ON org_invites(token);
CREATE INDEX IF NOT EXISTS idx_org_invites_email ON org_invites(email);
CREATE INDEX IF NOT EXISTS idx_subscriptions_org_id ON subscriptions(org_id);

-- ── 6. Backfill existing teachers ───────────────────────────────
-- Each existing teacher gets their own org and becomes admin.
-- Students are attributed to the teacher's org.
DO $$
DECLARE
  t RECORD;
  new_org_id UUID;
  slug_base TEXT;
  slug_text TEXT;
  counter INT;
BEGIN
  FOR t IN SELECT * FROM teachers WHERE org_id IS NULL LOOP
    slug_base := lower(regexp_replace(coalesce(t.full_name, split_part(t.email, '@', 1)), '[^a-z0-9]+', '-', 'g'));
    slug_base := trim(both '-' from slug_base);
    IF slug_base = '' OR slug_base IS NULL THEN
      slug_base := 'org';
    END IF;

    counter := 0;
    slug_text := slug_base;
    LOOP
      BEGIN
        INSERT INTO organizations (name, slug)
        VALUES (t.full_name || '''s Organization', slug_text)
        RETURNING id INTO new_org_id;
        EXIT;
      EXCEPTION WHEN unique_violation THEN
        counter := counter + 1;
        slug_text := slug_base || '-' || counter;
      END;
    END LOOP;

    UPDATE teachers SET org_id = new_org_id, org_role = 'admin' WHERE id = t.id;
    UPDATE students SET org_id = new_org_id WHERE teacher_id = t.id;
    INSERT INTO subscriptions (org_id, plan, status)
    VALUES (new_org_id, 'starter', 'active');
  END LOOP;
END $$;

-- ── 7. RLS policies for organizations ──────────────────────────
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

-- Teachers can select orgs they belong to
DROP POLICY IF EXISTS organizations_teacher_select ON organizations;
CREATE POLICY organizations_teacher_select ON organizations
  FOR SELECT USING (
    id::text IN (SELECT org_id::text FROM teachers WHERE supabase_uid::text = auth.uid()::text)
    OR auth.role() = 'service_role'
  );

-- Teachers can update their own org (only admins will do this through API)
DROP POLICY IF EXISTS organizations_teacher_update ON organizations;
CREATE POLICY organizations_teacher_update ON organizations
  FOR UPDATE USING (
    id::text IN (SELECT org_id::text FROM teachers WHERE supabase_uid::text = auth.uid()::text)
  );

-- ── 8. RLS policies for subscriptions ──────────────────────────
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS subscriptions_teacher_select ON subscriptions;
CREATE POLICY subscriptions_teacher_select ON subscriptions
  FOR SELECT USING (
    org_id::text IN (SELECT org_id::text FROM teachers WHERE supabase_uid::text = auth.uid()::text)
    OR auth.role() = 'service_role'
  );
