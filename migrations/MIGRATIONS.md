# Migrations — rules & realities

## How migrations are applied

Prod runs **plain Postgres** (`DATABASE_BACKEND=postgres`). The deploy's
"preflight migrations" step runs `scripts/run_postgres_migrations.py`, which:

1. ensures a `schema_migrations(filename, applied_at)` bookkeeping table,
2. scans `migrations/*.sql` **sorted by filename**, and
3. applies any file not already in `schema_migrations`, each in its own
   transaction, recording the filename on success.

The runner also supports **reverse** operations:

| Command | What it does |
|---------|-------------|
| `python scripts/run_postgres_migrations.py` | Apply pending forward migrations (default — this is the deploy preflight path, unchanged) |
| `python scripts/run_postgres_migrations.py --status` | List applied migrations (with timestamps) and any pending |
| `python scripts/run_postgres_migrations.py --down phase110_scheduled_plan_change.sql` | Reverse a migration via `migrations/down/<filename>`, then remove its `schema_migrations` row (transactional) |
| `python scripts/run_postgres_migrations.py --rollback-last` | Reverse the most recently applied migration (determined by `applied_at`, not filename order) |

The forward path is **incremental and forward-only** by default; the `--down` and
`--rollback-last` commands provide an operator-invoked revert path for contract
steps that include a reverse script.

## Down-migration convention

Every reverse script lives at `migrations/down/<same-filename>.sql` (e.g.
`migrations/down/phase110_scheduled_plan_change.sql`). The `down/` subdirectory
is excluded from the forward-apply glob (`migrations/*.sql`), so down scripts
never apply forward. A down script runs in its own transaction and must leave
the schema in a state the next forward pass can safely re-apply.

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

## Contract steps and the `-- migration:contract` marker

When you *must* write destructive DDL (a **contract** step — the old shape is
provably unreferenced by running code), two things are required:

1. **Marker:** The first line of the migration file must be:
   ```sql
   -- migration:contract <reason>
   ```
   (e.g. `-- migration:contract drop legacy token column; all code reads from token_hash`)
2. **Down script:** A matching reverse script at `migrations/down/<filename>`
   that can undo the destructive change.

The CI **migration-safety guard** (`scripts/check_migration_safety.py`) enforces
both: unmarked destructive DDL fails the build with a message pointing here.

## Migration-safety linter

`scripts/check_migration_safety.py` runs in CI (next to the schema-ref guard)
and detects:

- `DROP COLUMN`
- `DROP TABLE`
- `ALTER COLUMN … TYPE` (narrowing)
- `RENAME`
- `SET NOT NULL` (without a paired DEFAULT)
- `DROP CONSTRAINT` (non-`NOT VALID`)

in newly-added migration files. Violations exit 1 unless:

1. The file carries a `-- migration:contract <reason>` marker, **and**
2. A matching reverse script exists at `migrations/down/<filename>`.

Review the CI output for file:line details and this document for the expand-contract
pattern.

## Known gap — the repo cannot rebuild the DB from scratch

The core tables (`teachers`, `exam_sessions`, `answers`, `violations`,
`question_bank`) live only in the **original Supabase pg_dump baseline**, which
was never committed. The `migrations/*.sql` files are increments on top of that
baseline, and their **filename-sort order is not dependency-correct** (e.g.
`phase10_invite_clicks.sql` sorts before `phase10_student_invites.sql` it
depends on). So `run_postgres_migrations.py` against an *empty* database fails —
it only works because prod already has the baseline + history recorded.

**The wiring to close this is built and waiting for the dump** — see
`migrations/baseline/README.md`. Capture the schema + the already-applied set
from prod, commit them, and the `schema-from-scratch` CI gate self-activates and
proves the DB rebuilds from zero on every push. `scripts/bootstrap_db_from_baseline.sh`
is the same path for disaster recovery. Until the dump lands, that gate is a
no-op (green) and `integration_tests/schema.sql` is a focused, hand-built fixture
(NOT the prod schema) that lets the integration suite exercise real Postgres —
see `docs/superpowers/specs/2026-06-10-deploy-safety-and-db-tests.md`.
