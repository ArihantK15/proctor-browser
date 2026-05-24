CREATE TABLE IF NOT EXISTS issues (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id),
  teacher_id      UUID NOT NULL REFERENCES teachers(id),
  session_id      TEXT,
  exam_id         UUID,
  category        TEXT NOT NULL,
  severity        TEXT NOT NULL DEFAULT 'normal',
  description     TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open',
  superadmin_note TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_issues_status_partial ON issues(status) WHERE status != 'resolved';
CREATE INDEX IF NOT EXISTS idx_issues_org            ON issues(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issues_teacher        ON issues(teacher_id, created_at DESC);
