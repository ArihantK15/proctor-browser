# Procta — Architecture & Engineering Roadmap

> Updated 2026-05-12. Covers Phases 0–4 and all hardening sprints.

---

## Current Architecture

| Layer | Modules | Notes |
|-------|---------|-------|
| **Routers** (`app/routers/`) | 22 files | admin, exam, auth, billing, chat, grading, lti, public, question_bank, sse, + domain routers |
| **Services** (`app/services/`) | 13 files | billing, calibration, chat, i18n, invite_landing, practice, release, risk, scorecard, scoring, sessions |
| **Repositories** (`app/repositories/`) | 3 files | questions, sessions, (more as needed) |
| **Models** (`app/models/`) | 9 files | Pydantic schemas for billing, exam, groups, invites, lti, org, student, teacher |
| **Auth** (`app/auth/`) | 3 files | JWT tokens (AuthCtx, require_auth, extract_auth), admin/student auth |
| **Jobs** (`app/jobs/`) | 2 files | email_jobs, helpers (Redis RQ background workers) |
| **LTI 1.3** (`app/lti/`) | 7 files | AGS, Deep Linking, NRPS, key mgmt, launch, registration |
| **Static** (`app/static/`) | 14 files | HTML surfaces, CSS design system, JS helpers |
| **Electron** (`renderer/`) | 1 file | index.html (kiosk exam window) |

**Key metrics:**
- `app/main.py`: ~420 lines (down from 7,642)
- `app/dependencies.py`: ~193 lines (down from 2,091) — pure re-export hub
- Test suite: 474 passing, 21 skipped across 20 test files
- Design system: 3 themes (dark/OLED/light), 155 component classes, Periwinkle Blue accent

---

## Phase 0 — Foundation ✅

*Shipped 2026-05-11 (`b24fea1`)*

1. **`dependencies.py` split** — 2,091 → 193 lines. Auth → `app/auth/`, models → `app/models/`, business logic → `app/services/`, data access → `app/repositories/`, utilities → `app/utils/`.
2. **`admin.py` split** — 3,264 lines → 22 domain routers under `app/routers/`.
3. **Background jobs** — Redis RQ queue with `app/jobs/` and separate worker container. Scorecard email bulk-send migrated.
4. **Test infrastructure** — `EMAIL_PROVIDER=noop`, conftest helpers, shared Supabase mock.

---

## Phase 1 — Self-Service & Revenue ✅

*Shipped across 2 commits (`64cd7a9`, `23a1eab`)*

5. **Org/tenant model** — organizations table, org_id propagation, per-org billing plans (free/pro/enterprise). Org Overview, Members, and Billing panels on dashboard.
6. **Razorpay billing** — `app/services/billing.py` with Razorpay subscription creation, webhook verification (HMAC-SHA256), and sandbox mode (no keys needed for local dev). `app/routers/billing.py` serves plan listing, subscription creation (returns Razorpay short_url), webhooks, and invoice history. Dashboard billing panel shows plan/status/usage with upgrade buttons.
7. **Free trial gate** — 14-day trial with countdown banner on Org Overview panel.

---

## Phase 2 — Hardening & Design System ⚡

*Shipped across 5 commits (`50e9c7a`, `79048a7`, `82ee2a1`, `05399de`, and prior)*

### 2.0 Structural Hardening

52 deferred items from TODO.md §2 and §2.A audited and fixed:

| Category | Items completed |
|----------|----------------|
| Race conditions | Scorecard claim on hard-kill, sessionId orphan recovery, validate-student TOCTOU |
| Performance | ChatHub socket cap + idle eviction, async Supabase hot paths, 2 uvicorn workers |
| Security | Caddy HSTS/X-Frame-Options, filesystem path traversal guards, cross-tenant roll_number isolation, /login rate-limiting (10 req/min) |
| Code quality | `except Exception` audit, naive datetime → aware, Status → StrEnum, `AuthCtx` typed auth, action-btn → .btn migration (90 instances) |
| UX | Focus-traps on modals, WCAG AA contrast fix, spinner/disabled on slow exports, mobile breakpoints |
| Infra | Backup story (restic + cron), disk-fill prevention (screenshot retention), dependency pins |

### 2.1 Codebase Refactoring

- **Escape helpers** — consolidated `chatEscape`/`chatJsEscape` into `_safe.js`, removed inline duplicates
- **CSS extraction** — ~900 lines moved from dashboard.html → `dashboard.css`
- **Long functions split** — `_build_scorecard_pdf` (201→70 LOC), `_render_invite_landing` (166→50 LOC), `export_pdf` (284→120 LOC)
- **Cross-tenant isolation** — `validate_student` chains through invite access_code token; all queries scoped by teacher_id + exam_id
- **Naming normalization** — `safe_tid`→`safe_teacher_id`, `AuthCtx` dataclass, `extract_auth()` helper
- **i18n infrastructure** — `app/services/i18n.py` + `app/static/_i18n.js` with `t()` helpers and ~40 string keys
- **btn migration** — all 90 `action-btn` references converted to `.btn`/`.btn-primary`/`.btn-secondary`/`.btn-ghost`; old CSS rules removed
- **Rate limiting** — Caddy auth endpoint rate limiting (10 req/min per IP)

### 2.2 Visual Redesign (§1.6 — 16 surfaces ported)

The new Periwinkle Blue design system (OKLCH space, IBM Plex fonts, 3 themes) is live across all surfaces:

| Surface | Status | Notes |
|---------|--------|-------|
| Live Sessions | ✅ | stats-bar, table-toolbar, severity row accents |
| Analytics | ✅ | stat-chip strip, 2-col ax-card grid, histogram |
| Results | ✅ | stat-tile strip, table-toolbar with risk filter |
| Tools | ✅ | tool-card design system cards |
| Questions | ✅ | 3-column shell (sidebar | content | AI/Bank) |
| History | ✅ | stats-bar + table-toolbar, detail drill-down |
| Org Overview | ✅ | stat-tile strip, trial banner |
| Chat | ✅ | btn classes, broadcast modal design tokens |
| Members | ✅ | stats-bar + table-toolbar + design token table |
| All Orgs | ✅ | stats-bar + table-toolbar + design token table |
| Org Settings | ✅ | card wrapper, input class, btn-primary |
| Billing | ✅ | stat-tile top, tool-card plans |
| Student Lobby | ✅ | exam cards with card chrome |
| Marketing Landing | ✅ | full vanilla HTML port (hero, features, pricing) |
| Renderer (Electron) | ✅ | accent recolor (emerald→periwinkle), tokens embedded inline |
| Calibration | ✅ | concentric-ring dots, calmer pulse |

**Design system assets:** `tokens.css`, `components.css`, `theme.css`, `logo.svg`, `dashboard.css`

---

## Phase 3 — Institutional Sales ✅

*Shipped 2026-05-12 (`05399de`)*

8. **LTI 1.3 integration** — `app/lti/` implements AGS (grade passback), Deep Linking (content selection), NRPS (roster sync), key management, OIDC launch, and dynamic registration. Canvas/Moodle/Blackboard compatible. Router at `app/routers/lti.py`.
9. **Public REST API** — `app/routers/api.py` with X-API-Key auth. Endpoints: list exams, get exam details, list/session students, session drill-down. API key management at `/api/v1/admin/api-keys` (create/list/revoke). `app/auth/api_auth.py` handles key generation (SHA-256 hashed, prefix-identified) and authentication middleware.
10. **Alembic migrations** — `alembic.ini` + `migrations/alembic/env.py` configured for PostgreSQL (reads `DATABASE_URL` or constructs from `SUPABASE_*` vars). Existing raw SQL migrations coexist in `migrations/*.sql`.
11. **Typed repository** — `app/repositories/base.py` provides `Repository` base class with fluent `QueryBuilder` (select/insert/update/delete with .eq/.neq/.gt/.gte/.lt/.lte/.in_/.order/.limit/.offset/.maybe_single) and `QueryResult` typed wrapper. Table-specific repositories can subclass with `table = "tablename"`.

---

## Phase 4 — Scale & Polish

*Not started*

10. **Observability** — structured logging, distributed tracing, dashboards.
11. **Live proctoring** — multi-camera, screen mirror, audio monitoring.

---

## Phase 5 — MOAT

*Not started*

12. **Mobile PWA** — exam-taking on phones (BYOD). React Native or Flutter.
13. **AI audit trail** — bulk review UI for AI-suggested grades.

---

## What's Left (Priority Order)

| Priority | Item | Effort |
|----------|------|--------|
| **Medium** | macOS/Windows code signing (EV cert) | 1 week |
| **Backlog** | Mobile app (Phase 5) | 2-3 months |
| **Backlog** | Observability (Phase 4) | 1 month |

---

## Notes

- Redis with Append-Only File persistence is the only stateful service. RQ workers process email jobs.
- Caddy reverse-proxies HTTPS with auto-renewing Let's Encrypt certificates.
- Screenshots auto-delete after 90 days (configurable via `SCREENSHOT_RETENTION_DAYS`).
- All HTML surfaces load the design token system; 16 of 16 panels on Phase 2 layout.
- Test suite: 474 passed, 21 skipped, 0 failures. Runs in ~11s.
