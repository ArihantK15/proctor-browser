-- phase108: marker distinguishing org-suspension from individual suspension
--
-- delete_org (gap #5) suspends an org's teachers by setting status='suspended'.
-- That status is also used for INDIVIDUAL suspensions (e.g. abuse). Without a
-- marker, restore_org couldn't tell the two apart and would wrongly re-activate
-- a teacher who had been suspended on their own merits before the org was
-- soft-deleted. This column is stamped only when the org-delete suspends a
-- teacher, and restore re-activates only rows that carry it — so individual
-- suspensions survive an org delete+restore cycle.
--
-- No status CHECK change needed: this is a separate timestamp column, not a
-- new value in teachers_status_check.

ALTER TABLE teachers
  ADD COLUMN IF NOT EXISTS org_suspended_at TIMESTAMPTZ;
