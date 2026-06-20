-- Phase 135: org billing owner — decouple billing ownership from org_role so a
-- solo teacher (org_role='teacher') can own their own subscription. See
-- docs/superpowers/specs/2026-06-20-account-types-solo-vs-org-design.md
DO $$ BEGIN
  ALTER TABLE organizations ADD COLUMN IF NOT EXISTS owner_teacher_id UUID;
EXCEPTION WHEN undefined_table THEN RAISE NOTICE 'organizations absent; skip';
          WHEN duplicate_column THEN RAISE NOTICE 'owner_teacher_id exists; skip'; END $$;

DO $$ BEGIN
  ALTER TABLE organizations
    ADD CONSTRAINT fk_org_owner_teacher FOREIGN KEY (owner_teacher_id)
    REFERENCES teachers(id) ON DELETE SET NULL NOT VALID;
EXCEPTION WHEN duplicate_object THEN RAISE NOTICE 'fk exists; skip';
          WHEN undefined_table THEN RAISE NOTICE 'table absent; skip'; END $$;

DO $$ BEGIN
  ALTER TABLE organizations VALIDATE CONSTRAINT fk_org_owner_teacher;
EXCEPTION WHEN undefined_object THEN RAISE NOTICE 'constraint absent; skip';
          WHEN undefined_table THEN RAISE NOTICE 'table absent; skip'; END $$;
