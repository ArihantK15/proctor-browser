# Design — Dashboard trim + make tenancy real

**Status:** Proposed
**Date:** 2026-06-07
**Author:** Arihant (with Claude)
**Supersedes/relates:** strategic_audit_2026_05 (React dashboard rewrite — now shelved)

## Context

The teacher/admin dashboard has accreted into a maintenance and correctness
problem. A read-only audit (2026-06-07) found three things:

1. **Two parallel dashboards.** The legacy monolith (`app/static/dashboard.html`
   2,008 lines + `app/static/dashboard-app.js` 7,721 lines) is the **default**
   served at `/dashboard`. A separate React app (`app/dashboard-ui/` →
   `/dashboard-react`) mirrors it tab-for-tab but is explicitly *not* the
   default and is behind. **Decision: React is shelved. Legacy is canonical.**
   We trim legacy and ignore React this pass.

2. **Tenancy is ~70% built but not wired through.** The data model has
   `teachers.org_id` + `teachers.org_role` (`teacher`/`admin`/`superadmin`),
   per-org seat limits (`check_org_limits`), and per-org billing. But data
   access is scoped **by `teacher_id`**, not by org: ~51 `teacher_id` filters
   in `app/repositories/`, **56** in three admin routers, vs **20** `org_id`
   filters across *all* routers and **0** in the repository layer. Role gating
   is **client-side only** (`applyOrgRole()` at `dashboard-app.js:1110` toggles
   `[data-roles]` elements). Net effect: the `admin` role's org-wide views have
   no real backend — they silently return the admin's own `teacher_id`-scoped
   data, or partial results. The role *looks* implemented; it is cosmetic.

3. **Founder-internal tooling ships inside the customer app.** Superadmin-only
   `all-orgs`, `issues`, and `debug` tabs are in the same dashboard a paying
   teacher logs into.

### Product decisions (locked with the founder)

- **Admin is read-only, per-teacher.** An org admin does **not** edit another
  teacher's exams. They get a per-teacher lens: pick a teacher → view that
  teacher's exams, sessions, results, students; pull reports/exports. Coordinate
  and audit, never mutate someone else's exam content.
- **Two UI modes driven by real state, not just role:**
  - **Solo account** (org has exactly one member): a pure teacher dashboard.
    Zero org/admin/superadmin chrome. The 30-student buyer is one teacher and
    must never see an "admin" concept.
  - **Institute account** (org has >1 member): admin sees the read-only
    per-teacher roll-up described above; teachers see only their own data.
- **Superadmin tooling leaves the product** (separate path/build).

## Goals

- Make the existing `org_role` model **actually enforced server-side**, so
  admin views return correct org data and isolation can't be forgotten.
- Collapse the dashboard to what each account type actually needs (solo vs
  institute), removing dead/unbacked/superadmin surface.
- Add a **tenant-isolation safety net** (the one place a regression is
  existential for a B2B exam product).
- Do this as **trim + correction, not a rewrite.** No React work.

## Non-goals (out of scope this pass)

- Resurrecting or deleting the React dashboard (`app/dashboard-ui/`). Left
  untouched; just no longer the direction.
- Splitting the 7,721-line `dashboard-app.js` into modules. Flagged as a
  structural risk; separate refactor.
- Billing/pricing changes.
- Schema-per-tenant. We stay **shared-schema + scoping predicate** (right call
  for Postgres + this stage; one migration path, lighter ops).
- Any change to student-facing flow or the proctor.

## Architecture — the spine

### A. Centralize the access predicate (server)

The fix for both "broken admin logic" and "isolation can't be forgotten" is one
scope helper, applied in the repository layer instead of 56 hand-written
`.eq("teacher_id")` calls.

**Access rule (single source of truth):**

| Caller `org_role` | Visibility |
|-------------------|-----------|
| `teacher`         | rows where `teacher_id == self.id` |
| `admin`           | rows where `org_id == self.org_id` (READ); writes still limited to own `teacher_id` or admin-management endpoints |
| `superadmin`      | all rows (maintenance only; not in the product UI) |

Implementation:
- `app/auth.py` `require_admin` already returns the teacher dict carrying
  `id`, `org_id`, `org_role`. Introduce a small value object
  `AccessScope(teacher_id, org_id, org_role)` built from that dict.
- `app/repositories/base.py` (`QueryBuilder`/`Repository`, already present but
  only partially adopted): add a `scoped(scope: AccessScope, *, owner_col=
  "teacher_id", org_col="org_id")` method that appends the correct filter for
  the role. Read paths call `.scoped(scope)`; the helper decides teacher vs org
  vs none.
- Migrate the data-bearing read paths (exams, sessions, results, students,
  questions) from raw `_atable(...).eq("teacher_id", ...)` to the repo +
  `.scoped()`. Write paths stay owner-scoped (admins don't edit others' exams,
  per the product decision) — so writes keep `teacher_id` equality; only reads
  widen to org for admins.
- **Tables that carry data need `org_id`.** Audit which of exams/sessions/
  results/students/questions already have `org_id`. Where a table only has
  `teacher_id`, admin org-reads resolve the org's member `teacher_id`s first
  (join through `teachers`), OR we backfill `org_id` (preferred for the hot
  tables). Decide per table in the plan; backfill is a one-time migration.

### B. Two-mode UI (legacy dashboard)

- Compute an **`is_solo`** signal: org member count == 1. Expose it on the
  profile/login payload the dashboard already loads (alongside `org_role`).
- `applyOrgRole()` (`dashboard-app.js:1110`) becomes the single gate:
  - `is_solo` → force teacher view; hide *all* org/admin/superadmin tabs
    regardless of stored `org_role`.
  - institute admin → show the read-only per-teacher roll-up; hide exam-edit
    affordances on other teachers' data.
- Add the **teacher picker** for admin roll-up (a selector that sets the
  `teacher_id` context for read panels: results/history/analytics/sessions).

### C. Pull superadmin tooling out of the product

- Remove `all-orgs`, `issues`, `debug` tabs from `dashboard.html` and their
  handlers from `dashboard-app.js`. Move behind a separate internal route/build
  (e.g. `/internal`, gated to the master superadmin email) — or, minimally,
  hard-gate them so they never render for non-superadmin and are excluded from
  the teacher bundle. Exact home decided in the plan.

## Per-file change list (to be expanded in the implementation plan)

**Server**
- `app/auth.py` — add `AccessScope` from the teacher dict; export.
- `app/repositories/base.py` — add `.scoped(scope, ...)` to `QueryBuilder`.
- `app/repositories/{sessions,questions,...}.py` — route reads through
  `.scoped()`; keep writes owner-scoped.
- `app/routers/admin_sessions.py`, `admin_students.py`, `admin_scorecards.py`,
  `admin_exams.py` — replace ad-hoc `.eq("teacher_id")` reads with scoped repo
  calls; add the admin per-teacher read paths.
- Migration: backfill `org_id` on hot data tables that lack it (one-time).

**Legacy dashboard**
- `app/static/dashboard.html` — remove `all-orgs`/`issues`/`debug` tabs +
  panels; add teacher-picker UI for admin roll-up.
- `app/static/dashboard-app.js` — `applyOrgRole()` honors `is_solo`; wire
  teacher-picker read context; delete superadmin handlers.
- `app/routers/public.py` — `is_solo` in the profile payload if not already
  derivable client-side.

## Verification & safety net

1. **Tenant-isolation test (the load-bearing one).** A test that creates two
   orgs (A, B), each with teachers + exams + results, and asserts:
   - teacher in A sees only own data;
   - admin in A sees all of A, **none** of B (every data read path);
   - no endpoint returns a B row to an A caller. This is the regression guard
     against the existential bug.
2. **Admin roll-up correctness.** Admin in a multi-teacher org sees each
   teacher's exams/results via the picker (proves the org-read path returns
   data — the thing that's silently broken today).
3. **Solo-mode UI.** A single-member org shows zero org/admin chrome.
4. **No superadmin tabs** render in the teacher bundle.
5. **Self-Review Before Commit** on every changed file (HARD RULE): re-read,
   audit syntax/runtime/config/cross-ref/auth/failure, state findings before
   any commit. User commits; not the agent.

## Risks

- **Backfilling `org_id`** on live tables is the riskiest step — do it as an
  additive, reversible migration; verify counts before/after; never drop the
  `teacher_id` column.
- **Widening admin reads to org** is where a scoping bug becomes a cross-tenant
  leak. The isolation test (V1) must land *before* the widening ships.
- **Bus factor 1** — keep changes small, sequential, each independently
  shippable behind the existing release flow.

## Open questions

- Which hot tables already have `org_id` vs need backfill? (Resolve in plan
  via a column audit.)
- Where do superadmin tools live — separate build, or same app behind a hard
  email gate + route? (Lean: hard gate now, separate build later.)
- Does the admin roll-up need org-wide aggregate views (all teachers at once),
  or is per-teacher selection sufficient for v1? (Founder leans per-teacher.)
