-- Down migration for phase115_student_batch.sql (gap #59).
-- Additive change — safe to revert by dropping the index + column.

DROP INDEX IF EXISTS idx_students_teacher_batch;
ALTER TABLE students DROP COLUMN IF EXISTS batch;
