-- AI grading audit trail — every grade confirmation logged.

CREATE TABLE IF NOT EXISTS grading_audit (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id      TEXT NOT NULL,
  teacher_name    TEXT NOT NULL DEFAULT '',
  exam_id         TEXT,
  session_key     TEXT,
  answer_id       TEXT,
  question_id     TEXT,
  ai_score        NUMERIC,
  ai_confidence   TEXT,                -- 'high', 'medium', 'low', or null
  teacher_score   NUMERIC NOT NULL,
  max_score       NUMERIC NOT NULL DEFAULT 1.0,
  action          TEXT NOT NULL,        -- 'confirmed', 'bulk_accept', 'bulk_reject', 'overridden'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_grading_audit_teacher ON grading_audit (teacher_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_grading_audit_exam ON grading_audit (exam_id, created_at DESC);
