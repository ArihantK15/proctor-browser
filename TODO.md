# Procta — Pending Steps & Backlog

Single source of truth for everything that's been *built but not deployed* and
everything that's been *deferred but acknowledged*. Update as items move.

---

## 1. Deploy steps (consolidated, do in one go)

These accumulate from the recent feature sprint. None are individually urgent;
batch them whenever you do the next prod push.

### 1.1 Database migrations

```bash
psql "$DB_URL" -f migrations/phase10_invite_clicks.sql
psql "$DB_URL" -f migrations/phase11_scorecard_insight.sql
```

The others (groups, question_bank, scorecard_emailed, invite_reminders,
student_invites) are already applied — their endpoints have been live
for a while.

### 1.2 Environment variables

Add to `.env` on the droplet:

```
GROQ_API_KEY=gsk_...     # for AI question generation + auto-tag
```

Optional overrides (leave unset to use defaults):
```
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_TIMEOUT=30
```

If `GROQ_API_KEY` is missing, AI features return a clean 503 — they don't
break anything else. Safe to deploy without setting it first.

### 1.3 Third-party config (Resend dashboard)

For `mail.procta.net`:
- **Track clicks** — confirm ON (it's usually on by default; verify in
  Resend → Domains → mail.procta.net → settings)
- **Track opens** — optional. If you turn it on, expect noisy data: Outlook
  blocks pixels, Apple Mail pre-fetches them. The Clicked column on the
  invites dashboard is a more reliable signal regardless.

### 1.4 Container restart

```bash
docker compose build api && docker compose up -d api
```

### 1.4b Operational follow-ups (one-time, do soon)

1. **Rotate secrets if `.env` ever escaped local disk** — the audit
   confirmed `.env` contains real Supabase service-role key, JWT secret,
   and admin password. `.env` IS gitignored and dockerignored, so if you
   never shared it / pushed it / sent it, you're fine. If unsure, rotate
   all three in Supabase + redeploy.
2. **Add a screenshots cleanup cron** — `find /app/screenshots -type f
   -mtime +90 -delete` weekly, or earlier if disk pressure. The bind
   mount has no rotation today (see TODO §2.A28).
3. **Document a backup target for `screenshots/`** — Supabase covers
   the DB; forensic screenshot evidence is on droplet ephemeral disk
   only (TODO §2.A27). At minimum: nightly `restic` snapshot to S3.

### 1.5 Smoke tests after deploy

| Feature | Test |
|---|---|
| Click tracking | Send invite → click link → verify Clicked column populates within seconds |
| Started column | Click invite → log into exam → verify Started column populates |
| AI question generation | Question Bank → ✨ Generate → topic "photosynthesis" → 5 questions → confirm preview |
| AI auto-tag | Save to Bank on any question → verify suggested tags appear pre-filled |
| Scorecard insights | Generate a scorecard PDF — should include a "Personalised Note" section with 2-4 sentences. Second download for the same session should be instant (cached). |
| Excel injection guard | Add a student named `=cmd\|'/c calc'!A1`, export Excel, open — should show literal string with leading apostrophe |

---

## 2. Deferred hardening items

Surfaced in the audit, deliberately not fixed in the hardening pass. Each is
real but the cost/benefit didn't justify shipping in a fast sprint. Listed in
priority order.

### 2.1 Scorecard claim race on hard-kill — MED

**Where:** `app/main.py` in `email_all_scorecards` (~line 3920).

**Problem:** The bulk endpoint claims `scorecard_emailed_at` *before* sending.
If the worker is SIGKILL'd between claim and send, the row stays claimed
forever — student never gets their PDF, and re-runs skip them as
"already_sent". Graceful failures (PDF build error, send error) DO roll back
correctly; only worker-kill mid-send is the hole.

**Fix:** Add a `scorecard_claim_at timestamptz` column. Use it as the racey
claim sentinel. Stamp the real `scorecard_emailed_at` only after send
success. Add a 5-min TTL recovery clause to the claim query so stuck claims
get retried automatically.

**Effort:** ~30 lines + 1 migration. Skipped because it requires a schema
change and worker-kill mid-send is genuinely rare in normal operation.

### 2.2 ChatHub per-tenant socket cap + idle eviction — MED

**Where:** `app/main.py` `ChatHub` class (~line 4083).

**Problem:** `teacher_conns[tid]` and `student_meta` are unbounded sets.
Pruning happens only on send failure — an idle leaked socket (student
closed laptop lid, OS hasn't torn the TCP) accumulates indefinitely.

**Fix:** 
- Add `MAX_TEACHER_SOCKETS_PER_TENANT = 50` constant; reject new connections
  past the cap with a 1008 close.
- Add a heartbeat ping every 30s; close connections that don't respond
  within 60s.
- TTL-evict `student_meta` entries older than 4h.

**Effort:** ~80 lines. Skipped because it needs careful testing under load
and current usage is well under any realistic cap.

### 2.3 ~~Resend transport retry with backoff~~ — DONE

Shipped: 3 attempts with exponential backoff (0.5 s, 1.0 s) on transport
errors, 429, and 5xx. 4xx fails fast.

### 2.4 ~~localStorage debounce in renderer~~ — DONE

Shipped: 400 ms debounce on `_persistAnswers`, with `_persistAnswersNow`
synchronous flush wired into `doBulkSave`, `beforeunload`, and `pagehide`
so we never lose the last keystroke.

### 2.5 sessionId orphan recovery in renderer — LOW/MED

**Where:** `renderer/index.html:678` (sessionId generation).

**Problem:** `sessionId = roll + Date.now()` — if Electron crashes mid-exam
and the student re-launches, the new sessionId doesn't match the old
localStorage key, so `_mergeLocalAnswers` returns nothing. Their offline
answers are silently lost.

**Fix:** On exam start, scan `localStorage` for any `answers_<roll>_*` keys,
take the most-recent timestamp, merge in. Add a cleanup step that removes
keys older than 7 days to bound storage growth.

**Effort:** ~25 lines, no schema. Skipped because it's a rare crash-resume
edge case.

### 2.6 Naive `datetime` in `proctor.py` — LOW

**Where:** `app/proctor.py:189, 728`.

**Problem:** Uses `datetime.now()` (naive, local tz) and `datetime.utcnow()`
(naive UTC). If ever compared against a tz-aware datetime, raises
`TypeError`.

**Fix:** Replace with `datetime.now(timezone.utc)` everywhere.

**Effort:** 2-line change. Skipped because the code paths don't currently
compare to aware datetimes — it's pre-emptive only.

### 2.7 Streaming Excel/PDF for huge exports — LOW

**Where:** `app/main.py` `/api/export-excel` and `scorecard-zip`.

**Problem:** Both build the full output in `BytesIO` before streaming.
A 1000-session export can hold ~500 MB temporarily.

**Fix:** Use openpyxl `write_only=True` workbook + chunked iter. For ZIP,
write each PDF directly to the response stream rather than buffering.

**Effort:** ~40 lines per format. Skipped because the bulk caps (1000
sessions, 500 questions) keep memory usage well under the worker's 1.2 GB
limit at current scale.

### 2.8 Cross-tenant roll_number collision — LOW

(description follows the §2 audit additions below.)

---

## §1.6 Visual redesign integration (May 2026 design package)

### Phase 1 — TOKEN FOUNDATION (shipped 2026-05-02) ✅

The new design system is now live across every served HTML surface.
What landed:

  • `app/static/tokens.css` — design tokens (Periwinkle Blue accent,
    OKLCH color space, three themes: dark / dark-OLED / light)
  • `app/static/components.css` — extracted component class library
    (155 CSS rules) — buttons, badges, cards, inputs, tables, modals
  • `app/static/logo.svg` — new wordmark
  • `app/static/theme.css` — REWRITTEN as a legacy↔token bridge.
    Every existing `var(--bg)` / `var(--accent)` / `var(--muted)` /
    etc. across dashboard.html (~4700 LOC), student.html, register,
    download, and renderer/index.html now resolves through the new
    semantic tokens. Visual identity changes (emerald → periwinkle
    blue, Outfit/Space Grotesk → IBM Plex Sans, JetBrains Mono → IBM
    Plex Mono) propagate automatically.
  • All 5 served HTML files updated to load tokens.css + components.css
    BEFORE theme.css and use IBM Plex font links.

Verification: every CSS `var()` reference (192 across the three
files) resolves to a defined token. Static assets all return 200.

### Phase 2 — PER-SURFACE LAYOUT REWRITES (in progress)

**Shipped 2026-05-02:**

  • **2.1 Live Sessions tab** (commit `b21f5c6`) — design's `.stats-bar`
    + `.stat-tile` strip, `.table-toolbar` with search-wrap + severity
    filter, sticky table header, severity left-border row accents
    (`row-critical`/`row-error`/`row-warn`). All 7 JS hooks preserved.
  • **2.2 Analytics tab** (commit `68b9150`) — overview strip migrated
    to `.stat-tile` shape, four section cards now use the design's
    quieter `.analytics-section` pattern with uppercase tracked label
    + monospaced subtitle row. Histogram bars use new accent gradient.
    All 17 JS hooks preserved.
  • **2.3 Results + Tools** (commit `061ea86`) — same `.stat-tile`
    strip pattern applied to both tabs. renderResultsStats() and the
    Tools tab markup migrated.
  • **2.4 Questions tab** (commit `4abaa0f`) — last remaining legacy
    `.stat` block migrated. Missing-Answer tile flips to severity
    error-fg when there ARE missing answers so the issue surfaces.
  • **2.5 Marketing landing** (commit `933dc38`) — full vanilla HTML
    port of the design's React JSX preview. Hero with mockup, 6
    outcome cards, feature strip with student mockup, 3 pricing
    tiers, demo form wired to /api/demo-request, footer. New routes
    `GET /` and `GET /marketing` serve it. Lucide icons replaced
    with inline SVG (no CDN dep). Mobile breakpoint built in.
  • **2.6 Mobile responsive** (commit `7a57c26`) — breakpoints at
    768px and 480px for the new design classes. Stats strip
    horizontally scrolls on phones (preserves the at-a-glance
    affordance), table-toolbar collapses to column, search-wrap
    spans full width, analytics sections shrink padding, bank
    Generate grid restacks. Hidden scrollbars where appropriate.
  • **2.7 Renderer accent recolor** (commit `3d4346d`) — Electron
    exam window's emerald-mint accent (20 hex literals across base,
    active, hover, plus rgba transparencies) swapped for the new
    periwinkle blue. Pure color substitution; layout / structure /
    JS hooks untouched. Visual coherence: dashboard, marketing,
    lobby, AND exam window now share one accent.

  Seven Phase 2 surfaces shipped in seven commits, each independently
  revertable. Zero JS hook breakage. Zero structural changes to the
  proctor stack.

### Phase 2.5 — STRUCTURAL PORTS (shipped 2026-05-02, second pass)

After honest feedback that the first Phase 2 pass was mostly stat-
strip swaps + tokens, this pass actually ports each surface to the
design's intended structure:

  • **2.8 Questions tab** (`1b89d32`) — full 3-column shell:
    sidebar (search + filter chips + question list with smooth-scroll
    focus) | content (toolbar + #q-editor cards + #q-preview) |
    AI/Bank panel (3 tabs: Generate / Bank / Import). All 26 JS
    hooks preserved verbatim. New JS: renderQSidebar, setAITab,
    setQTypeFilter, qFocusCard, qExpandAll, qCollapseAll.
  • **2.9 Analytics tab** (`902bdc9`) — 6 stat-chip strip + 2-col
    .ax-card grid + full-width Violations / Per-Question
    Breakdown cards. Histogram bars get accent gradient + hover
    fade. All 17 JS hooks preserved.
  • **2.10 Results tab** (`445cbf8`) — same Live-tab shell pattern:
    .table-toolbar with search-wrap + new risk-level filter +
    sticky table header.
  • **2.11+12 Tools + Chat tabs** (`2400892`) — design system card
    family applied to .tool-card and .chat-roster/.chat-thread
    chrome. Same surface-1 + radius-xl + uppercase tracked title
    pattern as the rest of Phase 2.
  • **2.13 Student lobby** (`7eda2b4`) — exam cards align with
    same card chrome.
  • **2.14 Renderer tokens** (`0393a36`) — embedded design tokens
    inline at top of renderer's <style> block (Electron can't load
    tokens.css over file://). 91 hardcoded hex literals across
    19 token families (surfaces, borders, text tiers, severity,
    accent, rgba overlays) replaced with semantic var() refs.
  • **2.15 Renderer exam screen** (`0b0dd0b`) — option cards (.opt)
    move to design's .opt-btn pattern (1px border, accent-blue
    selection, monospaced letter key). Question grid (.qdot)
    shrinks to 32px monospaced cells with subtle border + success
    answered state. Nav footer adds top border, Prev becomes
    secondary ghost, Submit stays success-green.
  • **2.16 Calibration dots** (`9a499e2`) — concentric-ring effect
    via ::before halo, smaller 14px center dot, calmer 1.6s pulse.

  Cache fix (`4e1d45a`) — Caddy was baking 24h-stale CSS into
  browsers after every deploy. Now CSS / JS / SVG / HTML get
  no-cache, must-revalidate so deploys light up instantly.

### Phase 2 — REMAINING SURFACES (deferred — needs running exam)

Phase 1 picks up colors, fonts, and shadows automatically. Phase 2
is where actual *layout* changes happen — the design package ships
new HTML structures for individual screens, and replacing the
existing markup means hand-verifying every JS hook (4700+ lines of
JS across the codebase use getElementById / addEventListener /
class selectors). Each surface below is a separate PR sized 200-500
LOC; do them in priority order:

  1. **Live Sessions tab** (port `teacher-live.html` into `#panel-live`)
     — new 3-pane layout with detail-panel slide-in. The on-demand
     Camera Feed slot from §1.6 (already shipped) drops straight into
     this layout — `<img id="liveview-img">` keeps its hook.
  2. **Student exam window** (port `student-exam.html` into
     `renderer/index.html`) — calmer student-facing UX. MUST preserve
     every JS hook (`#cam-preview`, `#exam-timer`, `#save-status`,
     `.q-field`, `.opt-btn`, etc.) so the proctor + anti-cheat stack
     keeps working.
  3. **Marketing / download page** (port `marketing.html`).
  4. **Question editor** (port `question-editor.html`).
  5. **Analytics tab** (port `analytics.html`).
  6. **Calibration screen** (port `calibration.html`) — renderer
     change.
  7. **Mobile responsive surfaces** (per `mobile-spec.html`).

Order assumes each as a separate PR with smoke tests between. Tokens-
first means at any point we can stop and ship — every later step is
purely additive, no rollback risk on what's already in.

### Reference

The design preview files (React JSX previews of each screen) live at
`~/Desktop/AI-Proctored Browser/`. The flagship surfaces:

- `teacher-live.html` — Live Sessions tab including a Camera Feed
  panel (now wired end-to-end via on-demand live-view; see live-view
  endpoints in §1.6 below).
- `student-exam.html` — kiosk exam window, calmer palette.
- `marketing.html` — single-page marketing site.
- `analytics.html`, `calibration.html`, `question-editor.html`,
  `mobile-spec.html`, `migration-plan.html`.

### Suggested integration sequence (incremental, each shippable alone)

1. **Adopt tokens.css** — load it as the first stylesheet on every
   served HTML. The current inline `:root { --bg: #0d1117; ... }` blocks
   should map to the new semantic tokens (`--surface-1`, `--text-default`
   etc.) so old class names still work while colors update. **~30 LOC
   touched per file. No JS or DOM changes.** Single PR.

2. **Component baseline** — replace the existing button / input / badge /
   pill CSS with the design's component classes from `components.html`.
   Existing JS hooks (`#exam-select`, `.action-btn`, `.tab.active`)
   stay reachable. ~200 LOC of CSS swapped in. Visual diff is large
   but no functional change.

3. **Live Sessions tab redesign** — port the `teacher-live.html`
   markup into `dashboard.html`'s existing `#panel-live` panel.
   Camera Feed slot is now wired (see §1.6). 3-pane layout with the
   detail panel slide-in. ~400 LOC.

4. **Student exam window** — port `student-exam.html` into
   `renderer/index.html`. The biggest UX win for students per design
   review. ~500 LOC. Must preserve every existing JS hook
   (`#cam-preview`, `#exam-timer`, `#save-status`, `.q-field`,
   `.opt-btn`, etc.) so the proctor + anti-cheat stack keeps working.

5. **Marketing site** — `marketing.html` replaces `download.html` /
   `website/index.html`. Standalone, lowest risk.

6. **Mobile** — implement the responsive surfaces from
   `mobile-spec.html` for the teacher dashboard's three priority
   views (live monitor, chat, severity counts).

Order assumes shipping each as a separate PR with smoke tests in
between. Tokens-first means at any point we can stop and ship — every
later step is additive.

### §1.6 Live camera view (shipped 2026-05-02)

On-demand teacher access to a student's webcam during a live exam.
Architecture: pull-based polling (no WebRTC, no signaling, no SFU)
with a 60s server-side TTL kill-switch. Endpoints:

- `POST /api/admin/sessions/{sid}/live-view/start` (teacher)
- `POST /api/admin/sessions/{sid}/live-view/keepalive` (teacher, 30s ping)
- `POST /api/admin/sessions/{sid}/live-view/stop` (teacher)
- `GET  /api/admin/sessions/{sid}/live-frame` (teacher; returns image/jpeg or 204)
- `GET  /api/proctor/control/{sid}` (proctor.py polls every 2s)
- `POST /api/proctor/live-frame` (proctor.py uploads when control says yes)

Storage: Redis keys `liveview:<sid>` (60s TTL) and `liveframe:<sid>`
(5s TTL). No disk persistence.

When the design's full Live Sessions redesign lands (§1.6 step 3 above),
the new "Camera Feed" slot in the detail panel will swap the current
modal for the inline panel automatically — JS function `openLiveView`
+ the `<img id="liveview-img">` target are independent of layout.

---

## §2 audit (2026-04-30) — additional findings, deferred

A second-pass audit covering security, code quality, frontend/a11y, and
ops. Items below are real but were judged either too large for a
hardening sprint, too risky to fix without integration tests, or both.
Listed by severity then file.

### 2.A1 Dependency pins — supply-chain hygiene — HIGH (deferred)

`requirements.txt` uses `>=` for every dep. Reproducible builds are not
guaranteed; a transient FastAPI/pydantic minor bump can break prod.

**Why deferred:** pinning blindly with `==` could break the next build
without any test signal — we only just added the CI test job in this
pass. Plan: after CI is green twice, run `pip-compile` from the current
`requirements.txt`, commit the lockfile, and switch the Dockerfile to
install from it.

### 2.A2 Hot-path sync Supabase blocking the event loop — HIGH (deferred)

`validate_student` (~168 lines), `analyze_frame`, `id_verification`,
`register_student`, `get_events` all use the sync supabase client inside
`def` (sync) endpoints. With one async worker, every call blocks ALL
other requests including `/health`.

**Fix sketch:** expand `app/database.py:AsyncTable` to support `.in_()`,
`.gte()`, `.lte()`, `.limit()`, `.range()` (currently missing), then
migrate the five hot endpoints. ~300 LOC; needs careful test coverage
because the sync path is the production code path today.

### 2.A3 23 endpoints take `body: dict = Body(...)` — HIGH (deferred)

Inconsistent input validation; every handler hand-rolls `body.get(...)`.
A `body: SomeModel` would centralise length caps + type coercion +
required-field checks.

**Why deferred:** ~23 endpoints × ~3 fields each = a Pydantic model
sprint. Worth doing as a focused refactor with one PR per logical
group (invites, bank, groups, etc.), not as part of a hardening pass.

### 2.A4 `app/main.py` is 7642 lines — HIGH (deferred)

Natural seams identified in the audit:
`auth/teachers`, `auth/students`, `students/exams`,
`risk/scoring/screenshots`, `proctoring frames+ID`, `live/SSE`,
`exports/PDF/CSV/Excel`, `invites + reminders + landing`,
`email webhooks`, `question-bank`, `chat WS hub`.

**Why deferred:** any module split forces every test to be re-rooted;
given test coverage is at ~35%, the safe path is to grow the suite first
and split once tests catch regressions reliably.

### 2.A5 Test coverage 35% (39 tests / 110 endpoints) — HIGH (deferred)

Uncovered critical paths: invite-bounce flow, `email_scorecards`,
`export_excel`/`export_pdf`/`scorecard_pdf`, `bank_to_exam`,
`generate_bank_questions` (LLM path), `analyze_frame`,
chat WebSocket (`ws_chat_student/teacher`), `id_decision`,
`duplicate_exam`, `admin_submit`.

**Plan:** with the new CI job in place, every PR going forward should
add a test for the path it touches. Backfill the existing gap as a
focused sprint before the main.py split.

### 2.A6 ~120 `except Exception` blocks, many silent — HIGH (deferred)

Real bugs (DB outage, malformed data) indistinguishable from "expected"
errors in `main.py:34, 41, 1267, 1410, 2678, 3417, 3421, 5719, 6036,
6046, 6095, 6145` and many more.

**Fix sketch:** case-by-case audit to either narrow the exception
class, log at WARNING, or re-raise. Cannot be done en masse — needs
domain knowledge per call site.

### 2.A7 `dashboard.html` 4729 lines mixed HTML/CSS/JS — MED (deferred)

Should split into `dashboard.css` + `dashboard.js` modules. Currently
single-file structure makes diffing painful and prevents minification /
SRI / proper caching.

### 2.A8 Status strings stringly-typed everywhere — MED (deferred)

`"in_progress"`, `"completed"`, `"submitted"`, `"pending"` repeated
20+ times across `main.py`. No `Enum`. Typo risk.

### 2.A9 `tid` / `teacher_id` / `safe_tid` / `pre_tid` naming split — MED (deferred)

Same data, four spellings. Worth introducing a single
`AuthCtx`-shaped dependency that returns `teacher_id`, `exam_id`, `roll`
as one typed object.

### 2.A10 Filesystem-sanitization snippet duplicated — MED (deferred)

`main.py:2532, 2594` and `_safe_filename` (887) all do near-identical
sanitization differently. Consolidate.

### 2.A11 HTML escape helpers redefined per file — MED (deferred)

`_escHtml` (dashboard), `escapeHtml` (student), `_e` (main.py landing),
`_escGrp` (dashboard, quote-only), `chatEscape` (renderer) all do the
same job. Move to a shared `app/static/_safe.js` and import.

### 2.A12 Long functions doing 5+ things — MED (deferred)

`clear_live_sessions` (266 LOC), `export_pdf` (298), `_render_invite_landing`
(233), `_build_scorecard_pdf` (193), `validate_student` (168),
`send_invites` (167), `email_scorecards` (166), `get_analytics` (165),
`update_questions` (150), `submit_exam` (138), `admin_submit` (126),
`duplicate_exam` (121).

### 2.A13 `AsyncTable` lacks `.in_/.gte/.lte/.limit/.range` — MED (deferred)

Blocker for migrating sync endpoints to async (#2.A2). Fix this first.

### 2.A14 Folder hygiene — MED (deferred)

- `app/proctoring.db` (SQLite committed in source) → move to `data/`
  and `.gitignore`.
- Two `Locust_*.html` reports at repo root → `loadtest/reports/`.
- `proctor.py` exists at both repo root and `app/proctor.py` —
  duplicate or import-shadowing risk; verify and remove one.

### 2.A15 Mobile breakpoints on bank-generate grid — MED (deferred)

`dashboard.html:778` `grid-template-columns: 1fr 90px 110px 110px` has
no breakpoint override; below ~600px the fixed columns crush the
topic input. Wrap in `@media(max-width:768px){ .gen-grid{grid-template-columns:1fr 1fr} }`.

### 2.A16 Modals lack focus-trap / Esc / aria-modal — DONE

Esc key now closes all open modals (detail, triage, grade review, live view, onboard, broadcast, timeline lightbox).
Native focus-trap would require a larger refactor; Esc-close covers the primary complaint.

### 2.A17 `--muted` color contrast fails WCAG AA — DONE

Fixed by Phase 1 token redesign (tokens.css). `--text-muted` set to oklch values that pass AA on both dark and light backgrounds.

### 2.A18 No spinner / disabled state on slow exports — DONE

CSV, Excel, and Scorecards ZIP buttons now disable + show "Downloading…" during fetch.

### 2.A19 Long question text breaks bank-row layout — DONE

Bank question text clamped to 120px max-height with scrollbar. Action buttons no longer pushed off-screen on narrow panels.

### 2.A20 Auth forms not wrapped in `<form>` — DONE

Login, signup, and password-reset forms now use native `<form>` elements with `autocomplete` attributes. Password managers can now autofill correctly.

### 2.A21 Practice banner overlap with vbanner toast — DONE

vbanner uses `top: calc(70px + var(--top-padding, 0px))` and practice banner sets `--top-padding: 32px`, so the toast shifts down when the practice banner is visible.

### 2.A22 Caddy global headers (HSTS, X-Frame-Options) — DONE

Added `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: strict-origin-when-cross-origin` to Caddyfile.

### 2.A23 Caddy `/login` rate-limiting — DEFERRED

Already covered by slowapi at the FastAPI layer (20/min on `/api/v1/auth/login`). Caddy's `rate_limit` requires a third-party plugin; defence-in-depth only.

### 2.A24 macOS / Windows code signing — MED

Builds were unsigned; users hit Gatekeeper / SmartScreen on first install.

**Shipped 2026-05-04:**
- Added missing files to asar: `lib/**/*`, `config.js`, `setup-preload.js`
  (these were silently omitted from the packaged build, causing a startup crash).
- Added 5s fallback timer to lobby window `show()` so users see *something*
  even if `ready-to-show` never fires.
- Added `did-fail-load` handler that shows the window with an error message.
- Deferred auto-updater by 3s to avoid blocking startup on slow networks.
- Added 10-min timeout to Windows Python setup flow.
- Enabled `hardenedRuntime: true` + entitlements for macOS (needed for
  notarization when a cert is added).
- Added Apple/Windows signing secrets placeholders to `build.yml`.

**Still needed for macOS "damaged" fix:**
- Enroll in Apple Developer Program ($99/yr).
- Create a Distribution Certificate + export `.p12`.
- Set `CSC_LINK`, `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`,
  `APPLE_TEAM_ID` as GitHub repo secrets.
- Rebuild + notarize — Gatekeeper will then trust the DMG.

**Still needed for Windows SmartScreen fix:**
- Azure Trusted Signing or EV code-signing cert.
- Set `CSC_LINK`, `CSC_KEY_PASSWORD` as GitHub repo secrets.

**Interim workaround (no cert):**
- macOS: `xattr -cr /Applications/Procta\ Browser.app`
- Windows: Users click "More info" → "Run anyway" on SmartScreen warning

### 2.A25 Empty states are just "No data" — LOW (deferred)

Most empty states aren't actionable. "No invites sent yet — paste
recipients above and click Send" reuses already-present primitives.

### 2.A26 i18n string table — LOW (deferred)

~1500+ user-facing strings inlined. A `t()` helper would unblock
multi-language support without a full-file refactor.

### 2.A27 Backup story — HIGH (operational, document only)

Supabase handles DB backups, but `screenshots/` (forensic evidence) is
on droplet ephemeral disk with no off-site copy. Add nightly `restic`
or `rclone` to S3-compatible storage; document in `DEPLOY.md`.

### 2.A28 Disk-fill risk on `screenshots/` — HIGH (operational)

No rotation / quota on the `./screenshots:/app/screenshots` bind mount.
A few weeks of active proctoring will fill the droplet. Add a cron:
`find /app/screenshots -type f -mtime +90 -delete`.

### 2.A29 Single uvicorn worker with sync hot paths — HIGH

`docker-compose.yml --workers 1`. Reportlab PDF gen, LLM calls without
`await`, and `psutil` block ALL other requests including `/health`.
Either bump to 2 workers, or migrate sync code to `run_in_executor`.
Linked with #2.A2.

---

### 2.8 Cross-tenant roll_number collision — LOW (full description)

**Where:** `app/main.py:1497` `validate_student`.

**Problem:** `students` is queried by `roll_number` alone. If two teachers
both have a student with roll "STU-001", the first row wins. The
access_code path mitigates it (different teachers' codes won't match), but
the underlying lookup is global.

**Fix:** Make roll_number teacher-scoped. Either (a) require an additional
disambiguator on the login form, or (b) chain validation through the
invite token. Option (b) aligns with where the product is going.

**Effort:** Bigger lift — schema + login UX changes. Skipped because no
real customer has hit this collision yet.

---

## 3. Feature backlog

### 3.1 Short-answer question type with AI grading — SHIPPED ✅

### 3.2 Live risk triage on dashboard — SHIPPED ✅

### 3.3 Question quality lint (pre-publish) — SHIPPED ✅

---

### 3.4 Mobile app

**Why:** Schools increasingly want students taking exams from phones
(BYOD). Electron doesn't help here.

**Effort:** Substantial. React Native or Flutter rewrite of the
renderer. Camera + face detection are the main complexity.

### 3.5 Billing / subscription tier

**Why:** Cannot sell to schools without it.

**Effort:** Stripe integration + usage tracking + plan gates on the
dashboard.

---

## 4. Notes / parking lot

- **Outlook + Apple Mail tracking suppression:** Recipients on these
  clients won't reliably register opens. The Clicked + Started columns
  on the invites table are the canonical engagement signals — opens
  remain on the dashboard for completeness but should be treated as
  noisy.
- **Safari custom-scheme alert:** "Open in Procta app" still shows
  "Safari can't open the page" if Procta isn't installed. There's no
  JS API to detect protocol registration on Safari. Current solution
  is UX softening (muted button + explicit warning text). Don't try
  to fix the underlying alert again — the platform doesn't allow it.
