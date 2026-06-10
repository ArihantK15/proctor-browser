# Deploy Safety & Schema-Correctness Testing

**Date:** 2026-06-10 · **Status:** Planned (not started) · **Owner:** founder

**Decision:** Of a broad external audit, only two items are worth doing now —
automated **deploy rollback** and **real-Postgres schema testing**. Both
directly de-risk the binding failure mode (single VM + deploy-straight-to-prod +
fully-mocked tests). Everything else is deferred or rejected (see §4).

This is a plan only. No code changed when it was written.

---

## 1. Verified problems

- **No deploy rollback.** `deploy.yml` does `docker compose up -d --no-deps api
  worker autosave-worker` (recreates containers with the freshly-built image)
  **before** the 180s healthcheck. If the new image never becomes healthy, the
  old container is already gone, there is no automatic revert, and the later
  "Prune old images" step can delete the previous image — turning a bad commit
  into a manual-recovery outage. (Verified in `.github/workflows/deploy.yml`,
  "Deploy (git pull + docker build + healthcheck)" step.)
- **Zero real-database tests.** `tests/conftest.py` mocks every query through
  `MagicMock`/`_AtableDelegator`; no test touches real Postgres
  (no testcontainers, no `asyncpg.connect`). So the 903 green tests cover **no**
  schema correctness. The git history is full of exactly the bug class this
  misses: `order by non-existent column`, `popped NOT-NULL question_id`,
  `UndefinedColumnError`. These reach prod because nothing exercises real SQL.

Both are cheap relative to their blast radius, and both are prerequisites that
make later work safe (e.g. the god-file refactors the audit wants are dangerous
to attempt while the only tests are mocked).

---

## 2. Workstream A — Automated deploy rollback

**Goal:** a failed healthcheck restores the previously-running image
automatically; prod is never left on a broken build.

**Approach (in the `deploy` job, `deploy.yml`):**
1. Before rebuild, tag the currently-running images as a rollback point:
   `docker tag proctor-browser-api:latest proctor-browser-api:rollback` (and the
   same for `worker`, `autosave-worker`). Skip cleanly on first-ever deploy.
2. Build + `up -d` the new images → run the existing 180s healthcheck loop.
3. **On healthcheck failure:** retag `:rollback → :latest`, `up -d` again,
   re-run the healthcheck, and exit non-zero with a loud message + `docker
   compose logs --tail`. Prod ends on the last-known-good image.
4. **Prune guard:** the "Prune old images" step must run only on deploy
   **success** (remove `if: always()`), so a failed deploy can never delete the
   rollback image.

**Migration safety rule (codify, expand-contract):** migrations must be
**additive/backward-compatible** (`ADD COLUMN IF NOT EXISTS`, `… NOT VALID`,
new tables) so old code tolerates the new schema and a *code* rollback never
collides with an *already-applied* migration. phase96 already follows this.
Destructive changes (drop/rename/narrow) are a two-deploy expand-then-contract,
never inline. Add this as a short note in `DEPLOY.md` / migration style docs.

**Effort:** ~1 evening. **Risk:** low (additive YAML); test via a deliberately
failing healthcheck on a throwaway change before relying on it.
**Acceptance:** a forced-unhealthy deploy leaves `proctor-api` running the prior
image and the job exits non-zero.

---

## 3. Workstream B — Schema correctness in CI

### B1 — migrations-from-scratch gate (cheap, do first)
A CI job with a `postgres:16` service that runs
`scripts/run_postgres_migrations.py` against an **empty** DB, proving all repo
migrations apply cleanly and in order from zero. Catches ordering/drift and
broken DDL for ~20 lines of YAML, independent of any app code.

**Acceptance:** new job is green on `main`; a deliberately-broken migration
fails it.

### B2 — integration tests on critical write paths (higher value, depends on B1)
A `tests/integration/` suite (own `@pytest.mark.integration` marker, real
`asyncpg` via the Postgres `async_table` backend, reusing B1's PG service).
Cover only the highest-severity paths — the 80/20 of the audit's "15 tests":
- exam **submit + scoring** write (incl. numeric-range grading) end-to-end;
- billing **webhook → `billing_events` insert + `reconcile_org_entitlement`**;
- one **tenant-scope** query proving cross-tenant rows are not returned.

Unit suite stays fast and default; integration runs as its own CI job with the
PG service. ~10–15 tests.

**Acceptance:** integration job green in CI; intentionally reintroducing a
known historical bug (e.g. order-by a dropped column) makes it fail.

**Sequencing:** A and B1 are independent and quick — either first. B2 depends on
B1's PG-service wiring. Do **A + B1**, then B2, then (separately) the god-file
refactors the audit wants — those are only safe once B2 exists.

---

## 4. Explicitly rejected / deferred (so it isn't re-litigated)

- **RDS / multi-VM / blue-green / canary / multi-region** — audit assumed
  Hostinger+AWS; prod is a single Contabo VM with self-hosted Postgres. Right-
  sized next step is rollback + backups + (later) a replicated/managed Postgres,
  not k8s-grade topology for a solo founder.
- **Adopt Alembic, backfill 69 SQL files** — a tracked, idempotent runner
  already exists (`schema_migrations`). Alembic is a large, risky migration for
  marginal gain; B1 + the expand-contract rule cover the real risk.
- **N+1 batching** — audit concedes it isn't hurting throughput (0% error @ 3k
  concurrent). Premature, except possibly `exam.py` per-frame violation insert —
  **verify that one specifically** before optimizing; ignore the rest.
- **God-file decomposition (proctor.py 3.5k, auth.py 3.1k)** — real debt, but
  unsafe without B2. Gated behind integration tests.
- **Accessibility / skeleton-loader / OTP-cooldown quick wins** — legit polish;
  batch opportunistically when next touching those files (HTML dashboard /
  student app only — React is dropped).
- **OAuth/SSO/marketplace/open-source-daemon/funding framing** — product &
  business judgment, not engineering-quality work; founder's call, out of scope
  here.

---

## 5. Notes for whoever implements

- `deploy.yml` changes are themselves prod-affecting (the job auto-runs on push
  to `main`). Land the rollback change with the diff reviewed, and validate it
  with a forced-unhealthy deploy before trusting it.
- Keep the `DATABASE_BACKEND=postgres` reality in mind — prod uses
  `run_postgres_migrations.py`, not the Supabase runner; `supabase`/`postgrest`
  packages are installed-but-dead on this path.
