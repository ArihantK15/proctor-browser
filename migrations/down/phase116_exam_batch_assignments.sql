-- Down migration for phase116_exam_batch_assignments.sql (gap #59).
DROP INDEX IF EXISTS idx_eba_exam;
DROP TABLE IF EXISTS exam_batch_assignments;
