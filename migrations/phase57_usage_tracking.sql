-- Phase 57: Usage tracking for attempt-based billing.
-- Tracks exam submissions per org per billing period.

CREATE TABLE IF NOT EXISTS usage_records (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        TEXT NOT NULL,
  period_start  TIMESTAMPTZ NOT NULL,
  period_end    TIMESTAMPTZ NOT NULL,
  exam_attempts INT NOT NULL DEFAULT 0,
  students_used INT NOT NULL DEFAULT 0,
  plan_limit    INT NOT NULL DEFAULT 30,
  overage       INT NOT NULL DEFAULT 0,
  overage_amount NUMERIC(10,2) NOT NULL DEFAULT 0.00,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Unique constraint required for ON CONFLICT (org_id, period_start)
  UNIQUE (org_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_usage_org_period
  ON usage_records (org_id, period_start DESC);

-- Function to upsert usage for current period
CREATE OR REPLACE FUNCTION upsert_usage(
  p_org_id TEXT,
  p_exam_attempts INT DEFAULT 1,
  p_students_used INT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
  v_now TIMESTAMPTZ := now();
  v_period_start TIMESTAMPTZ := date_trunc('month', v_now);
  v_period_end TIMESTAMPTZ := date_trunc('month', v_now) + INTERVAL '1 month';
  v_plan_limit INT;
  v_usage_id UUID;
BEGIN
  -- Get plan limit from org's max_students column (set by billing webhooks).
  -- Avoids plan::json cast which would fail for text values like 'starter'.
  SELECT COALESCE(
    (SELECT o.max_students
     FROM organizations o
     WHERE o.id = p_org_id
     LIMIT 1
    ), 30
  ) INTO v_plan_limit;

  INSERT INTO usage_records (org_id, period_start, period_end, exam_attempts, students_used, plan_limit)
  VALUES (p_org_id, v_period_start, v_period_end, p_exam_attempts, COALESCE(p_students_used, 0), v_plan_limit)
  ON CONFLICT (org_id, period_start)
  DO UPDATE SET
    exam_attempts = usage_records.exam_attempts + p_exam_attempts,
    students_used = GREATEST(usage_records.students_used, COALESCE(p_students_used, usage_records.students_used)),
    overage = GREATEST(0, usage_records.exam_attempts + p_exam_attempts - usage_records.plan_limit * 10),
    updated_at = now()
  RETURNING id INTO v_usage_id;

  RETURN v_usage_id;
END;
$$ LANGUAGE plpgsql;
