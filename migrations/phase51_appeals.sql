-- Phase 51: Student appeals (dispute violations / grade concerns)
-- Links to exam_sessions; teacher reviews and responds.

CREATE TABLE IF NOT EXISTS appeals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_key     TEXT NOT NULL,
  student_id      TEXT NOT NULL,
  roll_number     TEXT NOT NULL DEFAULT '',
  exam_id         TEXT,
  teacher_id      TEXT NOT NULL,
  appeal_type     TEXT NOT NULL CHECK (appeal_type IN ('violation','grade','other')),
  description     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  teacher_note    TEXT DEFAULT '',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_appeals_teacher
  ON appeals (teacher_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_appeals_student
  ON appeals (student_id, created_at DESC);
