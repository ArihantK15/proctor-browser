# Teacher Dashboard Navigation Redesign — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the flat ~18-tab top nav bar in `app/static/dashboard.html` with a grouped left sidebar + a new "Overview" landing page (mockup Direction 3, approved 2026-07-08), while changing zero backend behavior — every existing `data-action`, endpoint, and load function stays wired exactly as-is.

**Architecture:** Pure front-end restructuring. `switchTab(tab)` (dashboard-app.js:1122) only depends on two things: elements with `class="tab"` + `data-tab="<name>"`, and panels with `id="panel-<name>"`. Neither cares about the container the `.tab` buttons live in. This means the redesign is a **container swap**, not a rewrite: the same buttons, same IDs, same `data-action="switchTab"` wiring move from a flat `<div class="tabs">` row into a grouped `<nav class="sidebar">`. No JS logic inside `switchTab`, `_dispatchTabLoad`, or any `load*()` function needs to change.

**Tech Stack:** Same as today — vanilla JS (dashboard-app.js), plain CSS (dashboard.css + components.css/tokens.css/theme.css already in place), no build step, no new dependencies. Icons: inline SVGs (see Icons section) — no emoji, no icon-font dependency.

## Global Constraints

- No backend/API changes. Every endpoint below is called identically post-redesign.
- No new external dependencies (no icon library CDN, no framework).
- Must preserve the existing mobile "more sheet" bottom-nav pattern — see Mobile section.
- Must preserve keyboard nav (`_initTabKeyboard`, dashboard-app.js:1177) — arrow keys/Home/End across tabs.
- Must preserve badge counters (`appeals-pending-badge`, `issues-open-badge`, live-alert badge, chat unread) exactly — same element IDs, same update call sites.
- No emoji characters anywhere in new markup — inline SVG icons only (see Icons section).

---

## 1. Chosen structure (Direction 3 mockup, approved)

- Left sidebar, grouped: **Overview** (new) → **Exam** → **Insights** → **Organization** → **Account**.
- Superadmin-only items (`all-orgs`, `issues`, `debug`) get their own **Admin** group, shown only for `currentOrgRole === 'superadmin'`.
- New **Overview** page (`panel-overview`) becomes the default landing tab instead of `live`. Shows stat tiles, "continue where you left off," recent activity, quick actions — all built from data the existing endpoints already return (no new endpoint needed; see §4).
- **Group auto-hide rule (new behavior):** if every tab inside a sidebar group is hidden by the existing per-item visibility checks (`data-roles` / `data-hide-for-admin` / `data-billing-owner`), the group header itself is hidden too — this is what makes "Organization" disappear cleanly for org-teachers (see §2), rather than showing an empty section title.

## 2. Role visibility — full current-state map (verified from dashboard.html, unchanged by this redesign)

| Tab | `data-roles` | hide-for-admin | Other gate | Sidebar group |
|---|---|---|---|---|
| live | teacher, admin | — | — | Exam |
| questions | teacher | ✓ | — | Exam |
| chat | teacher | ✓ | — | Exam |
| tools | teacher | ✓ | — | Exam |
| review | teacher, admin | ✓ | — | Exam |
| results | teacher, admin | — | — | Insights |
| analytics | teacher, admin | — | — | Insights |
| history | teacher, admin | — | — | Insights |
| org | **admin only** | — | — | Organization |
| security | **admin only** | — | — | Organization |
| members | **admin only** | — | — | Organization |
| org-settings | **admin only** | — | — | Organization |
| billing | (any role) | — | `data-billing-owner="1"` (JS toggles `style.display` based on `is_billing_owner`) | Organization |
| profile | teacher, admin, superadmin | — | — | Account |
| privacy | superadmin | — | — | Account (superadmin sees an extra Compliance item) |
| all-orgs | superadmin | — | — | Admin |
| issues | superadmin | — | — | Admin |
| debug | superadmin | — | — | Admin |

**Confirms your ask directly:** `org`/`security`/`members`/`org-settings` are *already* `data-roles="admin"` today — a plain org-teacher never sees them even in the current flat bar. The only NEW behavior needed is the group-auto-hide rule above, so that a teacher doesn't see an "Organization" header with nothing under it. `billing` is the one item in that group visible to non-admins (if they're the billing owner) — it stays in the group and shows/hides independently via the existing `data-billing-owner` toggle, same as today.

## 3. Full nav → panel → load-function → endpoint map

This is the "smooth transition" inventory — everything a tab click currently triggers, verified from `_dispatchTabLoad` (dashboard-app.js:1141) and each function's body. **None of this changes.** The sidebar button for each tab keeps the identical `data-action="switchTab" data-args='["<tab>"]'`.

| Tab | Panel ID | Triggered on tab-open | Endpoint(s) called |
|---|---|---|---|
| live | `panel-live` | *(none — live data comes from the persistent `refreshAll()` poller, not a tab-open loader)* | `GET /api/v1/admin/sessions` (via `refreshLive`, polled) |
| results | `panel-results` | `refreshResults`, `refreshPendingGradeBadge` | `GET /api/v1/results`, `GET /api/v1/admin/pending-grades` |
| questions | `panel-questions` | `loadQuestions` | `GET /api/v1/admin/questions` |
| chat | `panel-chat` | `chatClearActiveUnread`, `chatClearTabBadge` | *(chat itself is SSE/poll-driven, not a single load call)* |
| analytics | `panel-analytics` | `loadAnalytics` | `GET /api/v1/admin/analytics` |
| history | `panel-history` | `refreshStudentList` | `GET /api/v1/student-search` |
| tools | `panel-tools` | `loadRegisteredCount`, `loadAccessCode`, `loadSchedule`, `loadShuffleConfig`, `loadSensitivity`, `loadAudioKeywords`, `loadPassMark`, `loadTemplates`, `loadGoogleClassroom` | `GET /api/v1/admin/registered-count`, `GET /api/v1/admin/access-code`, `GET /api/v1/admin/exam-schedule`, `GET /api/v1/admin/shuffle-config`, `GET /api/v1/admin/proctoring-sensitivity`, `GET /api/v1/admin/audio-keywords`, `GET /api/v1/templates`, `GET /api/v1/google/courses` |
| org | `panel-org` | `loadOrgOverview` | `GET /api/v1/org`, `GET /api/v1/org/billing`, `GET /api/v1/org/members` |
| security | `panel-security` | `loadSecurity` → `load2FAStatus`, `loadSessions`, `loadNotifPrefs`, (+`loadOrgMfaPolicy` if admin/superadmin) | *(sub-endpoints not expanded here — none change)* |
| profile | `panel-profile` | `loadProfile` | `GET /api/v1/auth/me`, `GET /api/v1/org` |
| members | `panel-members` | `loadMembers` | `GET /api/v1/org/members` |
| billing | `panel-billing` | `loadBilling` | `GET /api/v1/org/billing`, `GET /api/v1/billing/invoices` |
| org-settings | `panel-org-settings` | `loadOrgSettings` | `GET /api/v1/org` |
| all-orgs | `panel-all-orgs` | `loadAllOrgs` | `GET /api/v1/admin/all-orgs` |
| issues | `panel-issues` | `loadIssues` | `GET /api/v1/admin/issues` |
| review | `panel-review` | `loadReview`, `loadAppeals` | `GET /api/v1/admin/violations/clusters`, `GET /api/v1/admin/appeals` |
| privacy | `panel-privacy` | *(clears `#sar-result` text only — SAR export is a manual button click, `POST` triggered by `data-action="sarExport"`, not tab-open)* | — |
| **overview (NEW)** | `panel-overview` (new) | New `loadOverview()` — reuses data already fetched elsewhere (see §4) | *(no new endpoint — see §4)* |
| debug | `panel-debug` | *(static flags page, no loader)* | — |

**Also unaffected, verified separately:**
- `_syncExamBar(tab)` — reads `document.querySelector('.tab.active')?.dataset.tab`, which still works once the sidebar's active button carries `class="tab active"` + `data-tab`.
- `_initTabKeyboard` — selects `.tabs [role="tab"]`; the sidebar's tab-item container needs to keep (or be added to) a `.tabs`-selectable wrapper, OR this selector gets updated to the new container class as part of implementation (one-line change, tracked as a task).
- Badge IDs (`appeals-pending-badge`, `appeals-pending-badge-mobile`, `issues-open-badge`, `issues-open-badge-mobile`, live-alert badges) — kept 1:1, same desktop+mobile pair.

## 4. Overview page — data sourcing (no new endpoints)

The Overview page's stat tiles and "continue where you left off" reuse data the dashboard already fetches on other tabs, called once eagerly when Overview is the landing tab:
- **Active Now / Flagged** → `GET /api/v1/admin/sessions` (same call `refreshLive` already makes)
- **Exams this week / Students enrolled** → derivable from `examsList` (already loaded at boot via `loadExams()`) + `GET /api/v1/org/billing`'s `student_count`
- **Flagged reviews needing action** → `GET /api/v1/admin/violations/clusters` (same call `loadReview` makes) or `GET /api/v1/admin/appeals`
- **Recent activity feed** → new, small aggregation client-side from the above (no new backend work) — OR deferred to a fast-follow if it needs true cross-entity ordering; the mockup's activity feed can ship v1 as "recent flagged sessions + recent submissions" from data already in hand.

No new backend route is required for v1. If real chronological cross-source activity ordering is wanted later, that's a separate, backend-touching fast-follow — explicitly out of scope here.

## 5. Icons (emoji replacement)

Per your explicit note — no emoji. Replace each nav item's emoji placeholder with a small inline SVG (matching the existing icon style already used elsewhere in the app, e.g. `.q-sidebar-search svg` in dashboard.html, and the chevron SVG already used for `.select`). Icon set needed (18 total, reused across desktop sidebar + mobile sheet):

Overview, Live Sessions, Questions, Chat, Tools, Review, Results, Analytics, History, Members, Org Settings, Billing, Profile, Privacy, All Orgs, Issues, Debug — plus a generic "org/building" icon for the group label if desired.

Recommend a small, consistent stroke-based icon set (24×24, `stroke="currentColor"`, `stroke-width="1.75"`, no fill) so they inherit `color` from `.nav-item`/`.nav-item.active` the same way the existing chevron SVG inherits via its embedded `stroke` color — kept as literal inline `<svg>` per item (no icon-font, no sprite sheet) to match this codebase's existing pattern (small inline SVGs used ad hoc, e.g. the search icon, the select chevron) rather than introducing a new dependency.

## 6. Mobile

Current mobile pattern: a duplicate `.more-sheet-item` copy of every tab button in a bottom-sheet menu (`app/static/dashboard.html` around line 404–441), plus a persistent bottom nav for the most-used 3-4 tabs. This redesign keeps that pattern unchanged structurally — the sidebar is a desktop-only presentation; below the existing mobile breakpoint (`@media(max-width:900px)` per `.qx-main`/`.q-sidebar` precedent in dashboard.css), the nav collapses to the same bottom-nav + more-sheet it uses today, just re-skinned with the new icons instead of emoji and re-grouped to match the sidebar's sections when the sheet opens.

## 7. Testing

- No new backend tests needed (no API changes).
- Front-end: manual verification via the existing browser-preview workflow (real CSS/JS loaded against real markup, screenshotted in both themes) — same method used for the button/select consistency pass earlier this session.
- Regression checklist before considering this done: every row in §3's endpoint table still fires on its tab's first open; badge counts still update; keyboard arrow-nav still cycles tabs; mobile sheet still opens and matches desktop grouping; Organization group correctly disappears for a plain org-teacher account and correctly shows (with only Billing, if owner) for an org-teacher who is the billing owner.
