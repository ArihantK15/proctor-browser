-- phase107: Org soft-delete / suspend columns
--
-- Adds deleted_at, deleted_by, delete_reason to organizations so the
-- superadmin can suspend an org without destroying data (reversible via
-- the restore endpoint).  Also adds a partial index so list_all_orgs
-- can efficiently skip soft-deleted rows by default.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS deleted_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS deleted_by    UUID REFERENCES teachers(id),
  ADD COLUMN IF NOT EXISTS delete_reason TEXT;

-- Partial index: only active (non-deleted) orgs are listed by default.
-- The restore / list_all_orgs?include_deleted=true paths use the full
-- index or a seq-scan respectively.
DROP INDEX IF EXISTS idx_organizations_active;
CREATE INDEX IF NOT EXISTS idx_organizations_active
  ON organizations (id) WHERE deleted_at IS NULL;
