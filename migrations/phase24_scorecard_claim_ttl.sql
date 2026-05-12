-- Phase 24: Scorecard claim race fix with TTL recovery
--
-- The original claim RPC set scorecard_emailed_at at claim time, before
-- the email job ran. If the worker was SIGKILL'd between claim and send,
-- the row stayed claimed forever -- the student never got their PDF and
-- re-runs skipped them as "already_sent".
--
-- Fix:
--   1. Add a separate scorecard_claim_at column as the racey sentinel
--   2. RPC sets scorecard_claim_at at claim time (with 5-min TTL recovery)
--   3. scorecard_emailed_at is stamped only after send success

alter table exam_sessions
  add column if not exists scorecard_claim_at timestamptz;

drop function if exists claim_scorecard_email(text, text);

create or replace function claim_scorecard_email(
  p_session_key text,
  p_teacher_id  text
) returns boolean
language plpgsql
as $$
declare
  v_count int;
begin
  update exam_sessions
    set scorecard_claim_at = now()
    where session_key = p_session_key
      and teacher_id = p_teacher_id
      and (
        scorecard_claim_at is null
        or scorecard_claim_at < now() - interval '5 minutes'
      )
    returning 1 into v_count;

  return v_count is not null;
end $$;

grant execute on function claim_scorecard_email(text, text) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
