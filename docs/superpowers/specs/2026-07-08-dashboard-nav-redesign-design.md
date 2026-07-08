# Teacher Dashboard Navigation Redesign — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Group the existing ~18-item left sidebar in `app/static/dashboard.html` into labeled sections + add a new "Overview" landing page (mockup Direction 3, approved 2026-07-08), while changing zero backend behavior — every existing `data-action`, endpoint, and load function stays wired exactly as-is.

**Correction (2026-07-08, post-approval):** the original draft of this spec said the redesign turns a "flat top tab bar" into a sidebar. That's wrong — **the desktop dashboard (≥1025px) already renders `.tabs` as a fixed, collapsible left sidebar** (`app/static/dashboard.css:1944`, `@media (min-width:1025px)`): 216px wide, `position:fixed`, backdrop-blur, left-border active-state, with a working collapse/expand hamburger toggle (`toggleSidebar()`, dashboard-app.js:1992). Below 1025px it falls back to the original horizontal row (tablet: sticky; mobile: bottom-sheet "more" menu). **What's actually missing is only: (1) section grouping headers — today it's one flat ungrouped list of 18 items; (2) icons — today it's plain text labels, zero icons anywhere in the nav; (3) the Overview landing page.** This makes the real scope of work meaningfully smaller than originally described: the fixed positioning, collapse behavior, and all three responsive breakpoints (mobile/tablet/desktop) already exist and are NOT being rebuilt — only grouping + icons + one new page are layered on top of the existing `.tabs`/`.tab` structure.

**Architecture:** Pure front-end restructuring. `switchTab(tab)` (dashboard-app.js:1122) only depends on two things: elements with `class="tab"` + `data-tab="<name>"`, and panels with `id="panel-<name>"`. Neither cares about the container the `.tab` buttons live in or whether they're wrapped in group sub-containers. This means the redesign is an **enhancement of the existing sidebar**, not a rewrite: the same buttons, same IDs, same `data-action="switchTab"` wiring get wrapped in new `<div class="nav-group">` sections with `<div class="nav-group-label">` headers, plus an icon `<svg>` added to each `.tab`. No JS logic inside `switchTab`, `_dispatchTabLoad`, or any `load*()` function needs to change.

**Tech Stack:** Same as today — vanilla JS (dashboard-app.js), plain CSS (dashboard.css + components.css/tokens.css/theme.css already in place), no build step, no new dependencies. Icons: inline SVGs (see Icons section) — no emoji, no icon-font dependency.

## Global Constraints

- No backend/API changes. Every endpoint below is called identically post-redesign.
- No new external dependencies (no icon library CDN, no framework).
- Do not touch the existing fixed-position/collapse/responsive-breakpoint CSS (`app/static/dashboard.css:1944-1990`) beyond what's needed to insert group headers and icons — that mechanism already works and is out of scope to rebuild.
- Must preserve the existing mobile "more sheet" bottom-nav pattern — see Mobile section.
- Must preserve keyboard nav (`_initTabKeyboard`, dashboard-app.js:1177) — arrow keys/Home/End across tabs.
- Must preserve badge counters (`appeals-pending-badge`, `issues-open-badge`, live-alert badge, chat unread) exactly — same element IDs, same update call sites.
- No emoji characters anywhere in the app's nav or status-message copy touched by this change — inline SVG icons only in nav (see Icons section). Also sweep the emoji already present in status-message strings this work happens to touch (confirmed real instances: `✅`/`🎉`/`🔒` in dashboard.html:680 and dashboard-app.js:83,1760,1777,1856,1903,1976,1988,2129,2272) — replace with plain text or a small inline SVG, but only where this plan's tasks already touch that code; a full app-wide emoji sweep is separate, larger work and out of scope here.

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

## 5. Icons (emoji replacement) — corrected: reuse existing mobile icons, don't reinvent

**Correction:** the mobile bottom-nav (`app/static/dashboard.html:377-401`) already has real, properly-styled inline SVG icons for 5 tabs — `live`, `results`, `history`, `chat`, `profile` — using the exact pattern this spec was about to propose (`viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"`). These must be reused **verbatim** in the desktop sidebar, not redrawn, for visual consistency between mobile and desktop (the same icon should look identical in both places):

```html
<!-- live -->      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
<!-- results -->   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
<!-- history -->   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
<!-- chat -->       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
<!-- profile -->    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
```

**13 new icons needed** (same `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"` template, feather-icons-style line art — the exact SVG path data for each is written out in Task 1 below): `overview` (new), `questions`, `tools`, `review`, `analytics`, `org`, `security`, `members`, `billing`, `org-settings`, `all-orgs`, `issues`, `debug`, `privacy`.

No icon-font, no sprite sheet, no CDN — literal inline `<svg>` per item, matching this codebase's existing ad hoc inline-SVG pattern (search icon, select chevron, bottom-nav icons above).

## 6. Group naming — reuse the mobile more-sheet's existing vocabulary

**Correction:** the mobile "more sheet" (`app/static/dashboard.html:420-441`) is *already* grouped, with `<h3>` headers: **"Teaching tools"** (Analytics, Questions, Tools, Review), **"Organization"** (Org Overview, Members, Billing, Org Settings, Security), **"Platform"** (Privacy, All Orgs, Issues, Flags/Debug) — Live/Results/History/Chat/Profile live in the persistent bottom-nav instead, since mobile only needs to group the *overflow*. Desktop shows everything in the sidebar at once (nothing sits in an equivalent "always visible" strip), so desktop needs one more group than mobile's overflow list, but must reuse mobile's exact names where the same items are grouped, to keep the app internally consistent:

- **Overview** (new, ungrouped, pinned to the top — no header)
- **Exam** — Live Sessions, Questions, Chat, Tools, Review *(mobile puts Live/Chat in the bottom-nav directly, but groups the rest under "Teaching tools" — desktop's "Exam" is the superset of what mobile calls "Teaching tools" plus the two bottom-nav items)*
- **Insights** — Results, Analytics, History *(mobile has no equivalent group since Results/History live in its bottom-nav)*
- **Organization** — Org Overview, Security, Members, Billing, Org Settings *(exact same name AND exact same item set as mobile's "Organization" group — direct reuse)*
- **Account** — Profile, Privacy *(mobile has no equivalent group; Profile is bottom-nav, Privacy is in "Platform")*
- **Platform** — All Orgs, Issues, Flags *(exact same name as mobile's "Platform" group, minus Privacy which desktop puts under Account instead — Privacy is a compliance/data-export tool, not a maintenance flag, so it reads better grouped with Profile; this is the one deliberate naming/grouping deviation from mobile, called out explicitly here rather than left as a silent inconsistency)*

## 7. Mobile

The bottom-nav + more-sheet pattern (`app/static/dashboard.html:377-443`) already has icons and grouping (see §5/§6) — it needs zero structural changes. The only mobile-facing work in this plan is: (a) the more-sheet already uses correct group names, so nothing to rename there; (b) if new icons are added for items the more-sheet currently shows as plain text (`org`, `members`, `billing`, `org-settings`, `security`, `all-orgs`, `issues`, `debug`, `privacy` — check each `.more-sheet-item` for an existing `<svg>`; several currently have none), add the same new SVG from Task 1 to that mobile button too, so mobile and desktop show identical icons for identical destinations.

## 8. Testing

- No new backend tests needed (no API changes).
- Front-end: manual verification via the existing browser-preview workflow (real CSS/JS loaded against real markup, screenshotted in both themes) — same method used for the button/select consistency pass earlier this session.
- Regression checklist before considering this done: every row in §3's endpoint table still fires on its tab's first open; badge counts still update; keyboard arrow-nav still cycles tabs; mobile sheet still opens and matches desktop grouping; Organization group correctly disappears for a plain org-teacher account and correctly shows (with only Billing, if owner) for an org-teacher who is the billing owner.
