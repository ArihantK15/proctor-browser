-- Phase 136: one-time backfill to the solo-vs-org account model (see phase135 +
-- specs/2026-06-20-account-types-solo-vs-org-design.md).
--
-- Every current account predates account_type and is stored as org_role='admin'
-- with a UI-side solo-downgrade. That downgrade is now removed, so the STORED
-- role must be made honest or these accounts render as manager-only admins and
-- lose exam authoring + Billing. This migration:
--   (a) gives every org an owner_teacher_id (the admin, else the first member),
--       so billing ownership (is_billing_owner) resolves;
--   (b) turns a SOLO org (exactly one member) whose member is an admin into a
--       solo teacher (org_role='teacher'). Multi-member orgs keep their admin —
--       that admin is now correctly manager-only.
--
-- Idempotent: (a) only fills NULL owners; (b) only matches 1-member admins, who
-- become teachers on first run and no longer match.

-- (a) owner = the admin (preferred) else the lowest-id member, per org.
DO $$
BEGIN
    UPDATE organizations o
    SET owner_teacher_id = pick.tid
    FROM (
        SELECT DISTINCT ON (org_id) org_id, id AS tid
        FROM teachers
        WHERE org_id IS NOT NULL
        ORDER BY org_id, (org_role = 'admin') DESC, id ASC
    ) pick
    WHERE o.id = pick.org_id AND o.owner_teacher_id IS NULL;
EXCEPTION
    WHEN undefined_column THEN RAISE NOTICE 'phase136(a): owner_teacher_id absent; skip';
    WHEN undefined_table  THEN RAISE NOTICE 'phase136(a): table absent; skip';
END $$;

-- (b) solo org (1 member) whose member is an admin → solo teacher.
DO $$
BEGIN
    UPDATE teachers t
    SET org_role = 'teacher'
    WHERE t.org_role = 'admin'
      AND t.org_id IN (
          SELECT org_id FROM teachers
          WHERE org_id IS NOT NULL
          GROUP BY org_id HAVING COUNT(*) = 1
      );
EXCEPTION
    WHEN undefined_column THEN RAISE NOTICE 'phase136(b): column absent; skip';
    WHEN undefined_table  THEN RAISE NOTICE 'phase136(b): table absent; skip';
END $$;
