-- phase115: student cohort/batch grouping (gap #59)
--
-- A single optional label on each student row identifying their cohort —
-- e.g. "2024-CSE-A", "Sem-3", "Batch-B". Free-text (institutions name cohorts
-- however they like) and nullable (existing students are simply ungrouped).
-- Indexed by (teacher_id, batch) because every read filters within a teacher's
-- scope first, then by batch.

ALTER TABLE students ADD COLUMN IF NOT EXISTS batch VARCHAR(120);

CREATE INDEX IF NOT EXISTS idx_students_teacher_batch ON students (teacher_id, batch);
