# Procta — Architecture & Engineering Roadmap

> CTO-level analysis of the current state and phased plan to scale.

---

## Architecture Audit

**Current state at a glance:**

| Metric | Value | Verdict |
|--------|-------|---------|
| `app/dependencies.py` | 2,091 lines | God module — auth, models, helpers, rendering, caching |
| `app/routers/admin.py` | 3,264 lines | Monolithic — every admin operation in one file |
| `app/services/` | Empty dir | No service layer |
| Frontends | React SPA + jQuery-in-HTML | Two stacks to maintain |
| Background jobs | Polling loop in `reminders.py` | No retries, no queues |
| Email notifications | Sync HTTP call in async handler | Blocks event loop |

The biggest problem isn't missing features — it's that the architecture makes adding features expensive and risky.

---

## Phased Roadmap

### Phase 0 — Foundation (months 1-2)
**Unlocks everything else. No new features, just structural debt paydown.**

1. ~~Fix `log` bug in emailer.py~~ ✅ (committed)
2. Extract service layer from `dependencies.py` (2091 lines)
3. Extract service layer from `admin.py` (3264 lines)
4. Background job system (Redis RQ)
5. Fix test infrastructure

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

## Phase 0 — Detailed Execution Plan

### Step 2: Extract service layer from `app/dependencies.py`

**Current state:** Auth helpers, Pydantic models, DB helpers, rendering functions, constants, caching — all in one file. Every import pulls in the entire Supabase client.

**Target structure:**

```
app/
  models/              # Pydantic models only (no side effects)
    __init__.py
    teacher.py         # TeacherSignupIn, TeacherLoginIn, etc.
    student.py         # StudentSignupIn, RegisterIn, etc.
    exam.py            # ExamConfig, SessionStatus, etc.
    invites.py         # Invite models
    demo_request.py    # DemoRequest
  repositories/        # Data access layer (wraps Supabase)
    __init__.py
    teacher_repo.py
    student_repo.py
    exam_repo.py
    invite_repo.py
    demo_request_repo.py
  services/            # Business logic (currently empty!)
    __init__.py
    teacher_service.py
    exam_service.py
    invite_service.py
    scorecard_service.py
    demo_request_service.py
  auth/                # Auth-specific helpers
    __init__.py
    tokens.py          # JWT issue/verify
    admin_auth.py      # require_admin, require_student_account
  utils/
    __init__.py
    html_escape.py
    fmt_ist.py
  constants.py         # Keep as-is (env-based, no side effects)
  emailer.py           # Keep as-is (clean abstraction)
```

**Migration strategy (safe, incremental):**
1. Create new files — no deletions yet
2. One by one, move a model/function out of `dependencies.py`, update all imports in routers to point to the new location
3. Keep old imports working via re-exports in `dependencies.py` with deprecation warnings
4. Once all callers migrate, delete the old code

### Step 3: Extract service layer from `app/routers/admin.py`

**Target:** Each logical domain gets its own router file and a corresponding service:

```
app/routers/
  admin.py              # Shrinks to ~300 lines — just route defs + auth
  admin_exams.py        # Exam CRUD
  admin_students.py     # Student management, bulk register
  admin_invites.py      # Send invites, revoke, list
  admin_scorecards.py   # Scorecard emailing
  admin_settings.py     # Teacher settings
```

### Step 4: Background job system (Redis RQ)

```
app/
  worker.py             # RQ worker entrypoint (separate container)
  jobs/
    __init__.py
    send_email.py       # All email sends go through here
    send_reminders.py   # Schedule reminder emails
    cleanup.py          # Periodic cleanup tasks
```

**Docker-compose:** Add `worker` service.

### Step 5: Fix test infrastructure

- Stop mocking the full emailer module
- Set `EMAIL_PROVIDER=noop` in test env
- Test email notifications via `SendResult` return values
- Add proper integration test helpers

---

## What I'd do differently (if starting from scratch)

| Current approach | What I'd change | Why |
|---|---|---|
| `dependencies.py` (2k lines) | `app/models/`, `app/auth/`, `app/repositories/` | Every new feature becomes harder, not easier |
| `admin.py` (3.2k lines) | Router dispatches to service layer | Business logic should be testable without HTTP |
| Sync email in async handler | Async job queue (Redis RQ) | Currently blocks the event loop on every send |
| `_atable("table")` everywhere | Typed repository layer | Tests become trivial, provider swaps possible |
| Raw SQL migration files | Alembic | Schema versioning, rollbacks, auto-generation |
| Single flat teacher model | Org → Admin → Teacher hierarchy | Cannot sell to universities without this |
| No feature flags | Simple env-var flag system | Canary releases with friendly schools |
