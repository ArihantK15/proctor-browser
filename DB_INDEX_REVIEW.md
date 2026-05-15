# Database Index Review

This is the production index baseline for dashboard/reporting paths. It is deliberately small and focused on high-cardinality tables that will grow fastest at school scale: `exam_sessions`, `violations`, and `answers`.

## Migration

Apply:

```bash
python scripts/run_migrations.py
```

New migration:

```text
migrations/phase55_dashboard_reporting_indexes.sql
```

On a large production Supabase database, apply during a quiet deployment window. The statements are idempotent, but building indexes still consumes I/O and can briefly affect write latency.

## Covered Access Patterns

| Index | Protects |
|---|---|
| `idx_exam_sessions_teacher_status_submitted` | all-results export, CSV streaming, active-session counts |
| `idx_exam_sessions_teacher_exam_status_submitted` | per-exam result exports, scorecard ZIP, email-scorecards lookup |
| `idx_exam_sessions_teacher_roll_status_submitted` | student history and student search last-attempt lookups |
| `idx_exam_sessions_teacher_exam_roll_status` | duplicate-attempt validation for a specific teacher/exam/roll |
| `idx_violations_session_teacher_created` | timeline, PDF export, student event fetches |
| `idx_violations_teacher_session` | bulk violation counts for result pages and student history |
| `idx_violations_teacher_type_created` | failed-submit status metrics and incident filters |
| `idx_answers_session_teacher_question` | answer review, resume, scorecard/PDF answer fetches |
| `idx_answers_teacher_exam_pending_question` | pending short-answer grading queues |

## Verification SQL

Run this in Supabase SQL editor after deployment:

```sql
select indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and indexname in (
    'idx_exam_sessions_teacher_status_submitted',
    'idx_exam_sessions_teacher_exam_status_submitted',
    'idx_exam_sessions_teacher_roll_status_submitted',
    'idx_exam_sessions_teacher_exam_roll_status',
    'idx_violations_session_teacher_created',
    'idx_violations_teacher_session',
    'idx_violations_teacher_type_created',
    'idx_answers_session_teacher_question',
    'idx_answers_teacher_exam_pending_question'
  )
order by indexname;
```

Expected: 9 rows.

## Follow-Up At Scale

Once the database has real volume, use `pg_stat_statements` or Supabase Query Performance to confirm the slowest queries use these indexes. If write volume becomes the bottleneck, remove unused overlapping single-column indexes only after checking `idx_scan` counts.
