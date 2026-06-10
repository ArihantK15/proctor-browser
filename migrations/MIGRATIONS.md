# Migrations — rules & realities

## How migrations are applied

Prod runs **plain Postgres** (`DATABASE_BACKEND=postgres`). The deploy's
"preflight migrations" step runs `scripts/run_postgres_migrations.py`, which:

1. ensures a `schema_migrations(filename, applied_at)` bookkeeping table,
2. scans `migrations/*.sql` **sorted by filename**, and
3. applies any file not already in `schema_migrations`, each in its own
   transaction, recording the filename on success.

It is **incremental and forward-only**: there are no down-migrations.

## Expand-contract is mandatory (this is what makes deploy rollback safe)

`deploy.yml` can automatically roll the **code** back to the previous image if a
new release fails its healthcheck — but migrations have *already run* by then and
are **not** reverted. Therefore every migration MUST be backward-compatible with
the currently-running code:

- **Allowed inline:** `ADD COLUMN ... IF NOT EXISTS`, new tables, new indexes
  (prefer `CREATE INDEX CONCURRENTLY` outside a txn for big tables), additive
  `... NOT VALID` constraints, new nullable/defaulted columns.
- **Never inline:** `DROP`/`RENAME` column or table, narrowing a type, adding a
  `NOT NULL` without a default, or anything that breaks the *previous* release.
  Do these as a two-step **expand → (deploy) → contract**: ship the additive
  change and the code that tolerates both shapes first; drop the old shape only
  in a *later* deploy once no running code references it.

phase96 (`phase96_billing_enterprise.sql`) is the reference example: all
`ADD COLUMN IF NOT EXISTS` + a `... NOT VALID` CHECK wrapped in an exception-safe
`DO` block.

## Known gap — the repo cannot rebuild the DB from scratch

The core tables (`teachers`, `exam_sessions`, `answers`, `violations`,
`question_bank`) live only in the **original Supabase pg_dump baseline**, which
was never committed. The `migrations/*.sql` files are increments on top of that
baseline, and their **filename-sort order is not dependency-correct** (e.g.
`phase10_invite_clicks.sql` sorts before `phase10_student_invites.sql` it
depends on). So `run_postgres_migrations.py` against an *empty* database fails —
it only works because prod already has the baseline + history recorded.

**To close this (needs prod access):** capture the live schema with
`pg_dump --schema-only --no-owner --no-privileges $DATABASE_URL >
migrations/baseline/000_baseline.sql`, commit it, and either squash the existing
phase files into the baseline or record them as already-applied. Until then,
`integration_tests/schema.sql` is a focused, hand-built fixture (NOT the prod
schema) that lets the integration suite exercise real Postgres — see
`docs/superpowers/specs/2026-06-10-deploy-safety-and-db-tests.md`.
