-- Phase 11: Cached AI scorecard insight
--
-- Stores the LLM-generated personalised note attached to each
-- student's scorecard PDF (see _build_scorecard_pdf in app/main.py).
-- Caching is essential here: every "download all scorecards" click
-- would otherwise re-call Groq for every session, both burning the
-- token budget and slowing the bulk export by ~1 s per student.
--
-- Why a column on exam_sessions and not a separate insight table:
--   * The insight is 1:1 with the session — there's no audit/version
--     history requirement (the underlying score is immutable, so the
--     insight can be regenerated deterministically by clearing this
--     column).
--   * One join less in the hot scorecard path. PDF builds run
--     synchronously inside the request lifecycle, so the latency
--     budget is tight.
--
-- Storing as text (not jsonb) — it's a single short paragraph, no
-- structured fields. Capped at 600 chars in app code; column is
-- unbounded so future format changes don't need a migration.
--
-- To regenerate insights for an exam (e.g. after tweaking the
-- prompt), clear the column for that exam_id:
--   update exam_sessions set scorecard_insight = null where exam_id = '...';

alter table exam_sessions
  add column if not exists scorecard_insight text;
