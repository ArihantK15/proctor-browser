-- Phase 17: Atomic scorecard-claim RPC
--
-- Replaces the read-then-update pattern in email_scorecards that could
-- let two concurrent requests both claim the same session. A fast
-- retry after a timeout could increment already_sent and skip the
-- session even though the first claim never completed sending.
--
-- The RPC uses an atomic UPDATE with IS NULL in the WHERE clause;
-- PostgreSQL row-level locking serialises concurrent claims. The
-- second caller sees scorecard_emailed_at already set and returns
-- false — the session is safely skipped instead of double-sent.

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
    set scorecard_emailed_at = now()
    where session_key = p_session_key
      and teacher_id = p_teacher_id
      and scorecard_emailed_at is null
    returning 1 into v_count;

  return v_count is not null;
end $$;

grant execute on function claim_scorecard_email(text, text) to anon, authenticated, service_role;

notify pgrst, 'reload schema';
