-- Phase 52: Backfill exam_sessions.student_id for sessions created before
-- the student_id recording fix (commit 82b4792).
--
-- Matches exam_sessions rows that have student_id IS NULL against the
-- students and student_accounts tables via roll_number + teacher_id.
--
-- Safe to run multiple times (WHERE student_id IS NULL guards).

UPDATE exam_sessions es
SET student_id = sa.id
FROM students s
JOIN student_accounts sa ON LOWER(sa.email) = LOWER(s.email)
WHERE es.student_id IS NULL
  AND es.roll_number = s.roll_number
  AND es.teacher_id = s.teacher_id;
