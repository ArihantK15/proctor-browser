# Procta — Architecture & Engineering Roadmap

> CTO-level analysis of the current state and phased plan to scale.

---

## Architecture Audit

**Current state at a glance:**

| Metric | Value | Verdict |
|--------|-------|---------|
| `app/dependencies.py` | 193 lines | Thin orchestrator — models/auth/services extracted |
| `app/routers/` | 22 domain files | Split from monolithic admin.py into per-domain routers |
| `app/services/` | 12 modules | Service layer extracted (chat, scorecard, risk, billing, etc.) |
| `app/models/` | 10 modules | Pydantic models extracted |
| `app/repositories/` | 5 modules | Data access layer extracted |
| `app/auth/` | 5 modules | Auth helpers extracted |
| Background jobs | Redis RQ in `app/jobs/` | Async job queue with retries, separate worker container |
| Email notifications | `app/emailer.py` with RQ | Async via job queue, EMAIL_PROVIDER=noop for tests |

---

## Phased Roadmap

### Phase 0 — Foundation ✅ (months 1-2)
**Unlocks everything else. No new features, just structural debt paydown.**

1. ~~Fix `log` bug in emailer.py~~ ✅ (committed)
2. ~~Extract service layer from `dependencies.py` (2091 → 193 lines)~~ ✅
3. ~~Extract service layer from `admin.py` (3264 → 22 domain routers)~~ ✅
4. ~~Background job system (Redis RQ + worker container)~~ ✅
5. ~~Fix test infrastructure (`EMAIL_PROVIDER=noop`, conftest helpers)~~ ✅

### Phase 1 — Self-Service & Revenue (months 3-4)
**Unlocks the business model directly.**

6. Organization/tenant model
7. Stripe billing
8. Convert demo-request gate to free trial

### Phase 2 — Institutional Sales (months 4-6)
**#1 procurement requirement for universities.**

9. LTI 1.3 integration (Canvas, Moodle, Blackboard)
10. Public REST API with developer portal

### Phase 3 — Scale & Polish (months 6-9)
11. Observability (structured logging, tracing, dashboards)
12. Live proctoring dashboard

### Phase 4 — MOAT (months 9-12)
13. PWA for students (mobile exam-taking)
14. AI audit trail with bulk review UI

---

## Phase 0 — Completed (commit `b24fea1`)

All five Steps were shipped in a single refactor pass (2026-05-11):

- **Step 2:** `dependencies.py` went from 2091 → 193 lines. Auth helpers moved to `app/auth/`, models to `app/models/`, business logic to `app/services/`, data access to `app/repositories/`, utilities to `app/utils/`.
- **Step 3:** `admin.py` split into 22 domain-specific router files under `app/routers/` (admin_exams, admin_students, admin_invites, admin_scorecards, etc.). `app/main.py` is now a thin 420-line orchestrator.
- **Step 4:** Redis RQ background job system with `app/jobs/` (email_jobs, helpers) and a separate `worker` container in docker-compose.
- **Step 5:** Test infrastructure fixed — `EMAIL_PROVIDER=noop` env var, conftest helpers (`make_admin_token`, `shared_supabase_mock`), tests use `SendResult` return values instead of mocking the emailer.

---

## What I'd do differently (if starting from scratch)

| Current approach | What I'd change | Why |
|---|---|---|
| ~~`dependencies.py` (2k lines)~~ | ✅ Extracted to `app/models/`, `app/auth/`, `app/repositories/` | — |
| ~~`admin.py` (3.2k lines)~~ | ✅ Split into 22 domain routers | — |
| ~~Sync email in async handler~~ | ✅ Migrated to Redis RQ job queue | — |
| `_atable("table")` everywhere | Typed repository layer | Tests become trivial, provider swaps possible |
| Raw SQL migration files | Alembic | Schema versioning, rollbacks, auto-generation |
| Single flat teacher model | Org → Admin → Teacher hierarchy | Cannot sell to universities without this |
| No feature flags | Simple env-var flag system | Canary releases with friendly schools |
