# Database baseline (disaster-recovery + from-scratch build)

This directory holds a **squashed snapshot of the production schema** so the repo
can rebuild its own database from zero. It closes the gap documented in
`../MIGRATIONS.md`: the core tables (teachers, exam_sessions, answers,
violations, question_bank) were never in any migration — they lived only in the
original Supabase dump — and the incremental `migrations/*.sql` files don't
replay from an empty DB in filename order.

## What lives here

| File | What it is | How to (re)generate |
|------|------------|---------------------|
| `000_baseline.sql` | Full current prod schema (DDL only) | `pg_dump --schema-only --no-owner --no-privileges "$DATABASE_URL" > migrations/baseline/000_baseline.sql` |
| `001_schema_migrations_data.sql` | The set of migrations already applied in prod, as INSERTs | `pg_dump --data-only --inserts --table=schema_migrations "$DATABASE_URL" > migrations/baseline/001_schema_migrations_data.sql` |

Run BOTH on prod (psql workflow), in one sitting, so the schema snapshot and the
"already-applied" list are consistent. Commit the two files together.

### While you're capturing from prod (one trip)

Also refresh the **static** schema guard's snapshot — a separate, complementary
artifact (`schema/columns.json`: a table→columns map that `scripts/
check_schema_refs.py` uses to catch column-name typos in app code WITHOUT a DB).
The full-DDL baseline here rebuilds the database; `columns.json` statically
ref-checks it. Capture all three at once:

```sh
# with DATABASE_URL pointed at prod:
pg_dump --schema-only --no-owner --no-privileges "$DATABASE_URL" \
    > migrations/baseline/000_baseline.sql
pg_dump --data-only --inserts --table=schema_migrations "$DATABASE_URL" \
    > migrations/baseline/001_schema_migrations_data.sql
python scripts/dump_schema.py        # refreshes schema/columns.json
```

## How it's used

`scripts/bootstrap_db_from_baseline.sh` rebuilds a database from these files:

1. load `000_baseline.sql` (full schema),
2. load `001_schema_migrations_data.sql` (seed `schema_migrations` so the
   pre-baseline migrations are recorded as already-applied),
3. run `scripts/run_postgres_migrations.py`, which now skips everything baked
   into the baseline and applies **only migrations added after** the snapshot.

This is exercised by the `schema-from-scratch` CI job (proves the schema builds
from zero on every push) and is the documented disaster-recovery path.

## Refreshing the snapshot

Re-capture both files whenever you want to re-squash (e.g. after a batch of
migrations accumulates). The bootstrap is idempotent: migrations already in the
seeded `schema_migrations` are skipped, so an up-to-date baseline simply applies
zero migrations on top.

> Until `000_baseline.sql` is committed, the `schema-from-scratch` CI job is a
> deliberate no-op (green) — it activates automatically once the file exists.
