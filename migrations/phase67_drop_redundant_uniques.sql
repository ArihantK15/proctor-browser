-- Drop redundant UNIQUE constraints added by phase66.
--
-- phase66 added UNIQUE constraints on the conflict columns used by
-- async_table().upsert(...) calls, on the assumption those columns
-- weren't already constrained. That assumption was wrong: the tables
-- already have PRIMARY KEY (or composite PK) on the same columns, so
-- phase66 created a SECOND unique B-tree index for each one.
--
-- Cost: every insert/upsert on these tables now maintained two
-- identical unique indexes instead of one. Roughly doubled write
-- amplification on the hottest tables (exam_sessions in particular,
-- which is upserted on every heartbeat / event / submit). This
-- became visible during 3500-VU load tests on 2026-05-21: the
-- join wave timed out long before CPU / RAM / connection pool
-- saturated, because the asyncpg pool was queuing on the slow
-- writes.
--
-- This migration drops the redundant phase66 constraints, leaving
-- only the original PRIMARY KEY on each table. ON CONFLICT clauses
-- in postgres_table.py work against either a PK or a UNIQUE
-- constraint, so the upsert path is unaffected — just faster.
--
-- IF EXISTS makes this idempotent. Safe to re-run.

ALTER TABLE exam_sessions DROP CONSTRAINT IF EXISTS exam_sessions_session_key_unique;
ALTER TABLE answers       DROP CONSTRAINT IF EXISTS answers_session_question_unique;
ALTER TABLE questions     DROP CONSTRAINT IF EXISTS questions_teacher_exam_question_unique;
ALTER TABLE exam_config   DROP CONSTRAINT IF EXISTS exam_config_teacher_exam_unique;
