-- Phase 55: Dashboard/reporting composite indexes
-- These cover the 100x-growth read paths used by CSV/PDF exports,
-- grade review, student history, session timelines, and ops status.
--
-- Apply during a quiet deployment window on large databases. The statements
-- are idempotent, but regular CREATE INDEX can still use meaningful I/O while
-- Postgres builds each index.

CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_status_submitted
    ON exam_sessions(teacher_id, status, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_exam_status_submitted
    ON exam_sessions(teacher_id, exam_id, status, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_roll_status_submitted
    ON exam_sessions(teacher_id, roll_number, status, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_exam_sessions_teacher_exam_roll_status
    ON exam_sessions(teacher_id, exam_id, roll_number, status);

CREATE INDEX IF NOT EXISTS idx_violations_session_teacher_created
    ON violations(session_key, teacher_id, created_at);

CREATE INDEX IF NOT EXISTS idx_violations_teacher_session
    ON violations(teacher_id, session_key);

CREATE INDEX IF NOT EXISTS idx_violations_teacher_type_created
    ON violations(teacher_id, violation_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_answers_session_teacher_question
    ON answers(session_key, teacher_id, question_id);

CREATE INDEX IF NOT EXISTS idx_answers_teacher_exam_pending_question
    ON answers(teacher_id, exam_id, question_id)
    WHERE teacher_score IS NULL;
