-- Phase 14: Exam Templates
-- Reusable exam configurations that can be cloned into new exams.

CREATE TABLE IF NOT EXISTS exam_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    exam_title TEXT NOT NULL,
    duration_minutes INTEGER DEFAULT 60,
    access_code TEXT DEFAULT '',
    shuffle_questions BOOLEAN DEFAULT FALSE,
    shuffle_options BOOLEAN DEFAULT FALSE,
    questions JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exam_templates_teacher_id ON exam_templates(teacher_id);
CREATE INDEX IF NOT EXISTS idx_exam_templates_created_at ON exam_templates(created_at DESC);
