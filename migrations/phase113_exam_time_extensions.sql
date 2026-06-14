-- Per-student exam time extension (accommodations).
-- Gap #22: let a teacher grant extra minutes per (exam, student).

CREATE TABLE IF NOT EXISTS exam_time_extensions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id    UUID NOT NULL,
    exam_id       TEXT NOT NULL,
    roll_number   TEXT NOT NULL,
    extra_minutes INTEGER NOT NULL DEFAULT 0,
    created_by    UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT exam_time_ext_uniq UNIQUE (teacher_id, exam_id, roll_number)
);
