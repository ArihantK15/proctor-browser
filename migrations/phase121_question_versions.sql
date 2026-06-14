-- phase121: question version history (audit trail) — gap #42
-- Append-only log of every create/update/delete on the teacher's own question_bank.
CREATE TABLE IF NOT EXISTS question_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id TEXT NOT NULL,
    teacher_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('create','update','delete')),
    snapshot JSONB NOT NULL,
    changed_by TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qversions_q
    ON question_versions(question_id, version_number DESC);
