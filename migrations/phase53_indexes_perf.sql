-- Phase 53: Additional performance indexes
-- Covers privacy queries, dashboard filters, and grade confirm hot paths.

CREATE INDEX IF NOT EXISTS idx_exam_sessions_student_id ON exam_sessions(student_id);
CREATE INDEX IF NOT EXISTS idx_exam_sessions_exam_id ON exam_sessions(exam_id);
CREATE INDEX IF NOT EXISTS idx_answers_question_id ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_answers_teacher_exam ON answers(teacher_id, exam_id) WHERE teacher_score IS NULL;
CREATE INDEX IF NOT EXISTS idx_student_invites_access_code ON student_invites(teacher_id, exam_id, access_code);
