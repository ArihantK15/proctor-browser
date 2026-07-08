# Dashboard Nav Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group the existing desktop sidebar into labeled sections, add icons (reusing the mobile nav's existing icon set), and add a new Overview landing page — with zero backend changes and zero changes to `switchTab`/`_dispatchTabLoad`/load functions.

**Architecture:** The desktop sidebar (`app/static/dashboard.css:1944`, `@media (min-width:1025px)`) already exists and works — this plan only wraps its existing `.tab` buttons in new `<div class="nav-group">` containers with header labels, adds an `<svg>` icon to each button (reusing the 5 icons the mobile bottom-nav already has verbatim, drawing 13 new ones in the same style for the rest), and adds one new tab (`overview`) that becomes the default landing panel. A new standalone, dependency-free JS file holds one pure function (`_groupShouldBeVisible`) so it can be unit-tested with Node's built-in test runner — the only piece of new logic in this whole change, everything else is markup.

**Tech Stack:** Vanilla JS/CSS, no build step. Node's built-in `node --test` (already used in this repo for `scripts/*.test.mjs`) for the one unit-testable function.

## Global Constraints

- No backend/API changes — see spec §3 for the full endpoint inventory that must keep firing unchanged.
- No new external dependencies (no icon library, no CDN, no framework).
- Do not modify `app/static/dashboard.css:1944-1990` (the existing fixed/collapse/responsive-breakpoint CSS) beyond adding new selectors for group headers/icons — that mechanism already works.
- No emoji in any markup this plan touches.
- Reuse the mobile bottom-nav's existing SVG icons verbatim for `live`/`results`/`history`/`chat`/`profile` — do not redraw them.
- Reuse the mobile more-sheet's exact group names `"Organization"` and `"Platform"` where they cover the same items (see spec §6) — do not invent different names for the same concept.

---

### Task 1: Nav-group visibility helper (pure function)

**Files:**
- Create: `app/static/nav-group-visibility.js`
- Create: `scripts/nav-group-visibility.test.mjs`
- Modify: `app/static/dashboard.html` (add one `<script>` tag)

**Interfaces:**
- Produces: `_groupShouldBeVisible(displayValues)` — a global function (also CommonJS-exported for the test), takes an array of `style.display` strings (one per `.tab` in a group) and returns `true` if the group should be shown (i.e. at least one item is not `'none'`).

- [ ] **Step 1: Write the failing test**

Create `scripts/nav-group-visibility.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { _groupShouldBeVisible } from '../app/static/nav-group-visibility.js';

test('group is visible when at least one item is shown', () => {
  assert.equal(_groupShouldBeVisible(['none', '', 'none']), true);
});

test('group is hidden when every item is display:none', () => {
  assert.equal(_groupShouldBeVisible(['none', 'none', 'none']), false);
});

test('group is hidden when given an empty list', () => {
  assert.equal(_groupShouldBeVisible([]), false);
});

test('an empty-string display value counts as visible', () => {
  assert.equal(_groupShouldBeVisible(['']), true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test scripts/nav-group-visibility.test.mjs`
Expected: FAIL — `Cannot find module '../app/static/nav-group-visibility.js'`

- [ ] **Step 3: Write minimal implementation**

Create `app/static/nav-group-visibility.js`:

```js
// Pure, DOM-free decision used by applyOrgRole() (dashboard-app.js) to hide
// a sidebar section header when every item inside it has been role-gated
// to display:none — otherwise a plain org-teacher sees an empty
// "Organization" header with nothing underneath it. No DOM access in this
// file on purpose: it's the one piece of new logic in the nav redesign, so
// it's the one piece worth a real unit test (via `node --test`, no browser
// needed).
function _groupShouldBeVisible(displayValues) {
  return displayValues.some(function (d) { return d !== 'none'; });
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { _groupShouldBeVisible };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test scripts/nav-group-visibility.test.mjs`
Expected: PASS, 4 tests passing.

- [ ] **Step 5: Load the new file in the browser**

In `app/static/dashboard.html`, find this line (currently right before dashboard-app.js's own script tag):

```html
<script src="/static/cookie-notice.js" defer></script>
<script src="/static/dashboard-app.js" defer></script>
```

Change it to:

```html
<script src="/static/cookie-notice.js" defer></script>
<script src="/static/nav-group-visibility.js" defer></script>
<script src="/static/dashboard-app.js" defer></script>
```

(`nav-group-visibility.js` must load before `dashboard-app.js` since Task 4 calls `_groupShouldBeVisible` from inside it.)

- [ ] **Step 6: Commit**

```bash
git add app/static/nav-group-visibility.js scripts/nav-group-visibility.test.mjs app/static/dashboard.html
git commit -m "feat(dashboard): add pure nav-group-visibility helper + unit tests"
```

---

### Task 2: Group the desktop sidebar + add icons to every item

**Files:**
- Modify: `app/static/dashboard.html:338-366` (the `<div class="tabs">` block)
- Modify: `app/static/dashboard.css` (new rules for `.nav-group`, `.nav-group-label`, icon sizing inside `.tab`)

**Interfaces:**
- Consumes: nothing from earlier tasks — the icon SVGs below are hardcoded directly into this markup, matching how the mobile bottom-nav already embeds its icons directly rather than generating them from a JS lookup.
- Produces: unchanged `data-tab`/`data-action`/`data-args`/`data-roles`/`data-hide-for-admin`/`data-billing-owner` attributes on every `.tab` (Task 4 and later depend on these being untouched), now wrapped in `<div class="nav-group">` containers with a `data-group="<name>"` attribute for Task 4's visibility helper to target.

- [ ] **Step 1: Replace the flat tabs block**

Current (`app/static/dashboard.html:338-366`):

```html
<div class="tabs" role="tablist" aria-label="Dashboard sections">
  <button class="sidebar-collapse" data-action="toggleSidebar" title="Collapse menu" aria-label="Collapse menu">«</button>
  <button type="button" class="tab active" data-tab="live" role="tab" aria-selected="true" data-action="switchTabLiveClearBadge" data-roles="teacher admin">Live Sessions<span class="tab-badge" id="live-alert-badge" style="display:none;background:var(--red);color:#fff">0</span></button>
  <button type="button" class="tab" data-tab="results" role="tab" aria-selected="false" data-action="switchTab" data-args='["results"]' data-roles="teacher admin">Results</button>
  <button type="button" class="tab" data-tab="history" role="tab" aria-selected="false" data-action="switchTab" data-args='["history"]' data-roles="teacher admin">Student History</button>
  <button type="button" class="tab" data-tab="analytics" role="tab" aria-selected="false" data-action="switchTab" data-args='["analytics"]' data-roles="teacher admin">Analytics</button>
  <button type="button" class="tab" data-tab="questions" role="tab" aria-selected="false" data-action="switchTab" data-args='["questions"]' data-roles="teacher" data-hide-for-admin>Questions</button>
  <button type="button" class="tab" data-tab="chat" role="tab" aria-selected="false" data-action="switchTab" data-args='["chat"]' data-roles="teacher" data-hide-for-admin>
    Chat<span class="tab-badge" id="chat-tab-badge" style="display:none">0</span>
  </button>
  <button type="button" class="tab" data-tab="tools" role="tab" aria-selected="false" data-action="switchTab" data-args='["tools"]' data-roles="teacher" data-hide-for-admin>Tools</button>
  <button type="button" class="tab" data-tab="review" role="tab" aria-selected="false" data-action="switchTab" data-args='["review"]' data-roles="teacher admin" data-hide-for-admin style="display:none">Review<span class="tab-badge" id="appeals-pending-badge" style="display:none;background:var(--amber);color:#000">0</span></button>
  <button type="button" class="tab" data-tab="privacy" role="tab" aria-selected="false" data-action="switchTab" data-args='["privacy"]' data-roles="superadmin" style="display:none">Privacy</button>
  <button type="button" class="tab" data-tab="org" role="tab" aria-selected="false" data-action="switchTab" data-args='["org"]' data-roles="admin" style="display:none">Org Overview</button>
  <button type="button" class="tab" data-tab="security" role="tab" aria-selected="false" data-action="switchTab" data-args='["security"]' data-roles="admin" style="display:none">Security</button>
  <button type="button" class="tab" data-tab="members" role="tab" aria-selected="false" data-action="switchTab" data-args='["members"]' data-roles="admin" style="display:none">Members</button>
  <button type="button" class="tab" data-tab="billing" role="tab" aria-selected="false" data-action="switchTab" data-args='["billing"]' data-billing-owner="1" style="display:none">Billing</button>
  <button type="button" class="tab" data-tab="org-settings" role="tab" aria-selected="false" data-action="switchTab" data-args='["org-settings"]' data-roles="admin" style="display:none">Org Settings</button>
  <button type="button" class="tab" data-tab="all-orgs" role="tab" aria-selected="false" data-action="switchTab" data-args='["all-orgs"]' data-roles="superadmin" style="display:none">All Orgs</button>
  <button type="button" class="tab" data-tab="issues" role="tab" aria-selected="false" data-action="switchTab" data-args='["issues"]' data-roles="superadmin" style="display:none">Issues<span class="tab-badge" id="issues-open-badge" style="display:none;background:var(--amber);color:#000">0</span></button>
  <button type="button" class="tab" data-tab="debug" role="tab" aria-selected="false" data-action="switchTab" data-args='["debug"]' data-roles="superadmin" style="display:none">Flags</button>
  <button type="button" class="tab" data-tab="profile" role="tab" aria-selected="false" data-action="switchTab" data-args='["profile"]' data-roles="teacher admin superadmin">Profile</button>
</div>
```

New (note: `overview` is added as the new default-active tab; `live` loses `class="active"`/`aria-selected="true"` since Overview is now the landing tab — Task 3 handles the JS default-tab change):

```html
<div class="tabs" role="tablist" aria-label="Dashboard sections">
  <button class="sidebar-collapse" data-action="toggleSidebar" title="Collapse menu" aria-label="Collapse menu">«</button>

  <button type="button" class="tab active" data-tab="overview" role="tab" aria-selected="true" data-action="switchTab" data-args='["overview"]'>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>
    Overview
  </button>

  <div class="nav-group" data-group="exam">
    <div class="nav-group-label">Exam</div>
    <button type="button" class="tab" data-tab="live" role="tab" aria-selected="false" data-action="switchTabLiveClearBadge" data-roles="teacher admin">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      Live Sessions<span class="tab-badge" id="live-alert-badge" style="display:none;background:var(--red);color:#fff">0</span>
    </button>
    <button type="button" class="tab" data-tab="questions" role="tab" aria-selected="false" data-action="switchTab" data-args='["questions"]' data-roles="teacher" data-hide-for-admin>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
      Questions
    </button>
    <button type="button" class="tab" data-tab="chat" role="tab" aria-selected="false" data-action="switchTab" data-args='["chat"]' data-roles="teacher" data-hide-for-admin>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
      Chat<span class="tab-badge" id="chat-tab-badge" style="display:none">0</span>
    </button>
    <button type="button" class="tab" data-tab="tools" role="tab" aria-selected="false" data-action="switchTab" data-args='["tools"]' data-roles="teacher" data-hide-for-admin>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/></svg>
      Tools
    </button>
    <button type="button" class="tab" data-tab="review" role="tab" aria-selected="false" data-action="switchTab" data-args='["review"]' data-roles="teacher admin" data-hide-for-admin style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>
      Review<span class="tab-badge" id="appeals-pending-badge" style="display:none;background:var(--amber);color:#000">0</span>
    </button>
  </div>

  <div class="nav-group" data-group="insights">
    <div class="nav-group-label">Insights</div>
    <button type="button" class="tab" data-tab="results" role="tab" aria-selected="false" data-action="switchTab" data-args='["results"]' data-roles="teacher admin">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      Results
    </button>
    <button type="button" class="tab" data-tab="analytics" role="tab" aria-selected="false" data-action="switchTab" data-args='["analytics"]' data-roles="teacher admin">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
      Analytics
    </button>
    <button type="button" class="tab" data-tab="history" role="tab" aria-selected="false" data-action="switchTab" data-args='["history"]' data-roles="teacher admin">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
      Student History
    </button>
  </div>

  <div class="nav-group" data-group="organization">
    <div class="nav-group-label">Organization</div>
    <button type="button" class="tab" data-tab="org" role="tab" aria-selected="false" data-action="switchTab" data-args='["org"]' data-roles="admin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"/></svg>
      Org Overview
    </button>
    <button type="button" class="tab" data-tab="security" role="tab" aria-selected="false" data-action="switchTab" data-args='["security"]' data-roles="admin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Security
    </button>
    <button type="button" class="tab" data-tab="members" role="tab" aria-selected="false" data-action="switchTab" data-args='["members"]' data-roles="admin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
      Members
    </button>
    <button type="button" class="tab" data-tab="billing" role="tab" aria-selected="false" data-action="switchTab" data-args='["billing"]' data-billing-owner="1" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
      Billing
    </button>
    <button type="button" class="tab" data-tab="org-settings" role="tab" aria-selected="false" data-action="switchTab" data-args='["org-settings"]' data-roles="admin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      Org Settings
    </button>
  </div>

  <div class="nav-group" data-group="account">
    <div class="nav-group-label">Account</div>
    <button type="button" class="tab" data-tab="profile" role="tab" aria-selected="false" data-action="switchTab" data-args='["profile"]' data-roles="teacher admin superadmin">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      Profile
    </button>
    <button type="button" class="tab" data-tab="privacy" role="tab" aria-selected="false" data-action="switchTab" data-args='["privacy"]' data-roles="superadmin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      Privacy
    </button>
  </div>

  <div class="nav-group" data-group="platform">
    <div class="nav-group-label">Platform</div>
    <button type="button" class="tab" data-tab="all-orgs" role="tab" aria-selected="false" data-action="switchTab" data-args='["all-orgs"]' data-roles="superadmin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
      All Orgs
    </button>
    <button type="button" class="tab" data-tab="issues" role="tab" aria-selected="false" data-action="switchTab" data-args='["issues"]' data-roles="superadmin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Issues<span class="tab-badge" id="issues-open-badge" style="display:none;background:var(--amber);color:#000">0</span>
    </button>
    <button type="button" class="tab" data-tab="debug" role="tab" aria-selected="false" data-action="switchTab" data-args='["debug"]' data-roles="superadmin" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
      Flags
    </button>
  </div>
</div>
```

- [ ] **Step 2: Add CSS for groups + icon sizing**

Add to `app/static/dashboard.css` (near the existing `@media (min-width:1025px)` sidebar block at line 1944 — insert immediately after the closing `}` of that media query, so these new rules only ever apply where the sidebar itself applies):

```css
@media (min-width:1025px){
  .nav-group{ display:flex; flex-direction:column; gap:2px; margin-top:10px; }
  .nav-group:first-of-type{ margin-top:6px; }
  .nav-group-label{
    font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
    color:var(--text-muted); padding:6px 14px 4px;
  }
  .nav-group.nav-group-empty{ display:none; }
  .tab svg{ width:16px; height:16px; flex-shrink:0; opacity:.85; }
  .tab.active svg{ opacity:1; }
}
```

- [ ] **Step 3: Verify HTML is well-formed**

Run: `python3 -c "from html.parser import HTMLParser
class P(HTMLParser):
    errors = 0
    def error(self, m): self.errors += 1; print('ERROR:', m)
p = P(); p.feed(open('app/static/dashboard.html').read()); print('errors:', p.errors)"`
Expected: `errors: 0`

- [ ] **Step 4: Manual browser verification**

Start the preview server (`procta-api` per `.claude/launch.json`), build a standalone harness page (same technique used earlier this session for the select/modal work) that loads the real `tokens.css`/`components.css`/`theme.css`/`dashboard.css` against a copy of just this new `<div class="tabs">` markup, screenshot at desktop width (≥1025px) in both themes, and confirm: group labels render, icons render at consistent size/alignment with text, active-tab accent still shows on the `overview` button.

- [ ] **Step 5: Commit**

```bash
git add app/static/dashboard.html app/static/dashboard.css
git commit -m "feat(dashboard): group desktop sidebar into sections, add icons to every item"
```

---

### Task 3: Overview landing page

**Files:**
- Modify: `app/static/dashboard.html` (new `<div class="panel" id="panel-overview">`, placed as the first panel, right after the `</div>` that closes `.tabs`)
- Modify: `app/static/dashboard-app.js` (new `loadOverview()` function + one line in `_dispatchTabLoad`, one line changing the default active tab)
- Modify: `app/static/dashboard.css` (new `.overview-*` rules)

**Interfaces:**
- Consumes: `liveData`, `examsList` (existing globals, already populated by boot-time code and `refreshLive`), `GET /api/v1/org/billing` response shape (`student_count`), `GET /api/v1/admin/violations/clusters` response shape — same shapes `loadReview`/`loadOrgOverview` already parse.
- Produces: `loadOverview()` — no return value, populates `#overview-*` DOM elements directly (matching the style of every other `load*()` function in this file).

- [ ] **Step 1: Add the panel markup**

In `app/static/dashboard.html`, immediately after the `</div>` that closes the `.tabs` block from Task 2, add:

```html
<!-- OVERVIEW PANEL — new landing page (nav redesign, 2026-07). Reuses data
     already fetched by refreshLive()/loadReview()/loadOrgOverview() rather
     than adding a new endpoint — see docs/superpowers/specs/
     2026-07-08-dashboard-nav-redesign-design.md §4. -->
<div class="panel active" id="panel-overview">
  <div class="overview-wrap">
    <div class="panel-header">
      <div class="panel-glow" aria-hidden="true"></div>
      <div class="panel-header-text">
        <h1 class="panel-title" id="overview-greeting">Welcome back</h1>
        <p class="panel-lede">Here's what's happening across your exams today.</p>
      </div>
    </div>

    <div class="overview-stats">
      <div class="stat-tile"><div class="stat-tile-label">Active Now</div><div class="stat-tile-value accent" id="overview-active-count">--</div></div>
      <div class="stat-tile"><div class="stat-tile-label">Exams</div><div class="stat-tile-value" id="overview-exam-count">--</div></div>
      <div class="stat-tile"><div class="stat-tile-label">Flagged Reviews</div><div class="stat-tile-value" id="overview-flagged-count" style="color:var(--red)">--</div></div>
      <div class="stat-tile"><div class="stat-tile-label">Students Enrolled</div><div class="stat-tile-value" id="overview-student-count">--</div></div>
    </div>

    <div class="overview-grid">
      <div class="overview-card">
        <h3>Continue where you left off</h3>
        <div id="overview-continue-list"></div>
        <div id="overview-continue-empty" style="display:none;color:var(--text-muted);font-size:13px">Nothing needs your attention right now.</div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Write `loadOverview()`**

In `app/static/dashboard-app.js`, add this function near `loadOrgOverview` (defined at line 1356):

```js
// Overview landing page — reuses data other tabs already fetch (see spec
// §4) rather than adding a new endpoint. Called eagerly on first load AND
// every time the Overview tab is opened, same pattern as every other
// _dispatchTabLoad entry.
async function loadOverview(){
  const greetEl = document.getElementById('overview-greeting');
  if(greetEl && currentTeacherProfile){
    const hour = new Date().getHours();
    const part = hour < 12 ? 'morning' : hour < 17 ? 'afternoon' : 'evening';
    const name = (currentTeacherProfile.full_name || '').split(' ')[0] || '';
    greetEl.textContent = `Good ${part}${name ? ', ' + name : ''}`;
  }

  const examCountEl = document.getElementById('overview-exam-count');
  if(examCountEl) examCountEl.textContent = String(examsList.length);

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions${_examQuery('?')}`);
    if(r.ok){
      const d = await r.json();
      const sessions = d.all_sessions || d.sessions || [];
      const activeEl = document.getElementById('overview-active-count');
      if(activeEl) activeEl.textContent = String(sessions.filter(s => s.status === 'in_progress' || s.status === 'active').length);
    }
  }catch(_){}

  try{
    const r = await authFetch(`${BASE}/api/v1/org/billing`);
    if(r.ok){
      const b = await r.json();
      const studEl = document.getElementById('overview-student-count');
      if(studEl) studEl.textContent = String(b.student_count || 0);
    }
  }catch(_){}

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/violations/clusters${_examQuery('?')}`);
    const flaggedEl = document.getElementById('overview-flagged-count');
    const listEl = document.getElementById('overview-continue-list');
    const emptyEl = document.getElementById('overview-continue-empty');
    if(r.ok && listEl){
      const d = await r.json();
      const clusters = d.clusters || d.sessions || [];
      if(flaggedEl) flaggedEl.textContent = String(clusters.length);
      listEl.innerHTML = '';
      if(clusters.length === 0){
        if(emptyEl) emptyEl.style.display = '';
      }else{
        if(emptyEl) emptyEl.style.display = 'none';
        clusters.slice(0, 5).forEach(c => {
          const row = document.createElement('div');
          row.className = 'overview-continue-row';
          row.innerHTML = `<span>${_escapeHtml(c.student_name || c.roll_number || 'Student')} flagged for review</span>`;
          listEl.appendChild(row);
        });
      }
    }
  }catch(_){}
}
```

- [ ] **Step 3: Wire it into `_dispatchTabLoad`**

In `app/static/dashboard-app.js:1141`, add one line to the existing function (do not restructure the rest of it):

```js
function _dispatchTabLoad(tab){
  if(tab==='overview') loadOverview();
  if(tab==='results' && resultsData.length===0) refreshResults();
  // ...(rest of the function is unchanged)
```

- [ ] **Step 4: Make Overview the default landing tab**

Find the initial tab-activation code (search dashboard-app.js for where `switchTab` is first called after auth resolves, or where the URL hash `#tab-` is parsed on boot) and change the fallback default from `'live'` to `'overview'`. Concretely: wherever the code does something like

```js
const initialTab = (window.location.hash.match(/^#tab-(.+)/) || [])[1] || 'live';
```

change the fallback to:

```js
const initialTab = (window.location.hash.match(/^#tab-(.+)/) || [])[1] || 'overview';
```

(Search for the literal string `'live'` used as a fallback tab default — there is exactly one such site; do not change the `data-tab="live"` attribute values, only this one JS default.)

- [ ] **Step 5: Add CSS**

Add to `app/static/dashboard.css`:

```css
.overview-stats{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:20px; }
.overview-grid{ display:grid; grid-template-columns:1fr; gap:20px; }
.overview-card{ background:var(--surface-1); border:1px solid var(--border-subtle); border-radius:14px; padding:20px; }
.overview-card h3{ margin:0 0 14px; font-size:14px; font-weight:700; }
.overview-continue-row{ padding:10px 0; border-bottom:1px solid var(--border-subtle); font-size:13px; }
.overview-continue-row:last-child{ border-bottom:none; }
@media(max-width:768px){
  .overview-stats{ grid-template-columns:repeat(2,1fr); }
}
```

- [ ] **Step 6: Manual browser verification**

Load the real dashboard (with a mocked/stubbed auth response if the local API has no DB, matching this session's earlier verification technique) and confirm: Overview is the panel shown on first load, stat tiles populate from the mocked endpoint responses, "Continue where you left off" shows either real flagged-session rows or the empty-state message.

- [ ] **Step 7: Commit**

```bash
git add app/static/dashboard.html app/static/dashboard-app.js app/static/dashboard.css
git commit -m "feat(dashboard): add Overview landing page, reusing existing endpoints"
```

---

### Task 4: Wire group-auto-hide into `applyOrgRole` + fix the keyboard-nav selector

**Files:**
- Modify: `app/static/dashboard-app.js:1213` (`applyOrgRole`)
- Modify: `app/static/dashboard-app.js:1177` (`_initTabKeyboard`'s selector)

**Interfaces:**
- Consumes: `_groupShouldBeVisible` (Task 1, global function).
- Produces: nothing new — this task only wires existing pieces together.

- [ ] **Step 1: Add group-visibility sync to the end of `applyOrgRole`**

In `app/static/dashboard-app.js`, find the end of `applyOrgRole` (right before its closing `}`, after the `data-billing-owner` forEach block already there) and add:

```js
  // Hide a sidebar section header when every item inside it just got
  // role-gated to display:none (data-roles/data-hide-for-admin/
  // data-billing-owner passes above already ran) — otherwise a plain
  // org-teacher sees an empty "Organization" header with nothing under it.
  document.querySelectorAll('.nav-group').forEach(group => {
    const items = Array.from(group.querySelectorAll('.tab'));
    const displays = items.map(el => el.style.display);
    const visible = typeof _groupShouldBeVisible === 'function'
      ? _groupShouldBeVisible(displays)
      : displays.some(d => d !== 'none');
    group.classList.toggle('nav-group-empty', !visible);
  });
```

(The `typeof _groupShouldBeVisible === 'function'` guard is defensive only in case the script load order from Task 1 Step 5 is ever disturbed — it degrades to the same inline logic rather than throwing.)

- [ ] **Step 2: Fix the keyboard-nav selector**

In `app/static/dashboard-app.js:1177`, the existing code is:

```js
const tabs = document.querySelectorAll('.tabs [role="tab"]');
```

This selector is unaffected by Task 2's change — `.nav-group` divs are still descendants of `.tabs`, so `.tabs [role="tab"]` still matches every `.tab` button regardless of the new wrapper `<div>`s. **No change needed here** — confirmed by re-reading the selector against the new markup from Task 2 before assuming a fix was required. Leave this line untouched.

- [ ] **Step 3: Manual verification**

Using the browser-preview harness, simulate three roles (`teacher`, `admin`, `superadmin`) by setting `currentOrgRole` and calling `applyOrgRole(role)` directly in the console, and confirm:
- `teacher`: Organization group has `nav-group-empty` class (hidden) unless `currentIsBillingOwner` is also set true, in which case only Billing shows and the group is visible.
- `admin`: Organization group fully visible; Exam group's Questions/Chat/Tools/Review are hidden (existing `data-hide-for-admin` behavior, unchanged).
- `superadmin`: Platform group visible; Organization group hidden (no admin-role items match).
- Arrow-key navigation still cycles through all currently-visible tabs in DOM order.

- [ ] **Step 4: Commit**

```bash
git add app/static/dashboard-app.js
git commit -m "feat(dashboard): auto-hide empty sidebar groups after role gating"
```

---

### Task 5: Mobile more-sheet icon parity

**Files:**
- Modify: `app/static/dashboard.html:420-443` (the `.more-sheet-group` blocks)

**Interfaces:**
- Consumes: the same icon markup written out in Task 2 (copy the identical `<svg>...</svg>` string for each tab name — same icon, same tab, same visual language on mobile and desktop).
- Produces: nothing new — purely additive icons on already-existing buttons.

- [ ] **Step 1: Add icons to the more-sheet items**

Current (`app/static/dashboard.html:420-441`, confirmed zero `<svg>` tags in this block today):

```html
<div class="more-sheet-group">
  <h3>Teaching tools</h3>
  <button type="button" class="tab more-sheet-item" data-tab="analytics" data-action="switchTab" data-args='["analytics"]' data-roles="teacher admin">Analytics</button>
  <button type="button" class="tab more-sheet-item" data-tab="questions" data-action="switchTab" data-args='["questions"]' data-roles="teacher" data-hide-for-admin>Questions</button>
  <button type="button" class="tab more-sheet-item" data-tab="tools" data-action="switchTab" data-args='["tools"]' data-roles="teacher" data-hide-for-admin>Tools</button>
  <button type="button" class="tab more-sheet-item" data-tab="review" data-action="switchTab" data-args='["review"]' data-roles="teacher admin" data-hide-for-admin style="display:none">Review<span class="tab-badge" id="appeals-pending-badge-mobile" style="display:none;background:var(--amber);color:#000">0</span></button>
</div>
<div class="more-sheet-group">
  <h3>Organization</h3>
  <button type="button" class="tab more-sheet-item" data-tab="org" data-action="switchTab" data-args='["org"]' data-roles="admin" style="display:none">Org Overview</button>
  <button type="button" class="tab more-sheet-item" data-tab="members" data-action="switchTab" data-args='["members"]' data-roles="admin" style="display:none">Members</button>
  <button type="button" class="tab more-sheet-item" data-tab="billing" data-action="switchTab" data-args='["billing"]' data-billing-owner="1" style="display:none">Billing</button>
  <button type="button" class="tab more-sheet-item" data-tab="org-settings" data-action="switchTab" data-args='["org-settings"]' data-roles="admin" style="display:none">Org Settings</button>
  <button type="button" class="tab more-sheet-item" data-tab="security" data-action="switchTab" data-args='["security"]' data-roles="admin" style="display:none">Security</button>
</div>
<div class="more-sheet-group">
  <h3>Platform</h3>
  <button type="button" class="tab more-sheet-item" data-tab="privacy" data-action="switchTab" data-args='["privacy"]' data-roles="superadmin" style="display:none">Privacy</button>
  <button type="button" class="tab more-sheet-item" data-tab="all-orgs" data-action="switchTab" data-args='["all-orgs"]' data-roles="superadmin" style="display:none">All Orgs</button>
  <button type="button" class="tab more-sheet-item" data-tab="issues" data-action="switchTab" data-args='["issues"]' data-roles="superadmin" style="display:none">Issues<span class="tab-badge" id="issues-open-badge-mobile" style="display:none;background:var(--amber);color:#000">0</span></button>
  <button type="button" class="tab more-sheet-item" data-tab="debug" data-action="switchTab" data-args='["debug"]' data-roles="superadmin" style="display:none">Flags</button>
</div>
```

New (icons inserted, identical to Task 2's per-tab SVGs; no attribute changes):

```html
<div class="more-sheet-group">
  <h3>Teaching tools</h3>
  <button type="button" class="tab more-sheet-item" data-tab="analytics" data-action="switchTab" data-args='["analytics"]' data-roles="teacher admin">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
    Analytics
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="questions" data-action="switchTab" data-args='["questions"]' data-roles="teacher" data-hide-for-admin>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
    Questions
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="tools" data-action="switchTab" data-args='["tools"]' data-roles="teacher" data-hide-for-admin>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/></svg>
    Tools
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="review" data-action="switchTab" data-args='["review"]' data-roles="teacher admin" data-hide-for-admin style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>
    Review<span class="tab-badge" id="appeals-pending-badge-mobile" style="display:none;background:var(--amber);color:#000">0</span>
  </button>
</div>
<div class="more-sheet-group">
  <h3>Organization</h3>
  <button type="button" class="tab more-sheet-item" data-tab="org" data-action="switchTab" data-args='["org"]' data-roles="admin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"/></svg>
    Org Overview
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="members" data-action="switchTab" data-args='["members"]' data-roles="admin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
    Members
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="billing" data-action="switchTab" data-args='["billing"]' data-billing-owner="1" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
    Billing
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="org-settings" data-action="switchTab" data-args='["org-settings"]' data-roles="admin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
    Org Settings
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="security" data-action="switchTab" data-args='["security"]' data-roles="admin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
    Security
  </button>
</div>
<div class="more-sheet-group">
  <h3>Platform</h3>
  <button type="button" class="tab more-sheet-item" data-tab="privacy" data-action="switchTab" data-args='["privacy"]' data-roles="superadmin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
    Privacy
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="all-orgs" data-action="switchTab" data-args='["all-orgs"]' data-roles="superadmin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
    All Orgs
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="issues" data-action="switchTab" data-args='["issues"]' data-roles="superadmin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
    Issues<span class="tab-badge" id="issues-open-badge-mobile" style="display:none;background:var(--amber);color:#000">0</span>
  </button>
  <button type="button" class="tab more-sheet-item" data-tab="debug" data-action="switchTab" data-args='["debug"]' data-roles="superadmin" style="display:none">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
    Flags
  </button>
</div>
```

- [ ] **Step 2: Add CSS for the icon inside `.more-sheet-item`**

Check `app/static/dashboard.css` for the existing `.more-sheet-item` rule and add (if not already covered by a generic `.tab svg` rule from Task 2 — confirm Task 2's `.tab svg` rule is scoped inside `@media(min-width:1025px)` and so does NOT apply to mobile, meaning a mobile-specific rule is needed here):

```css
.more-sheet-item{ display:flex; align-items:center; gap:10px; }
.more-sheet-item svg{ width:18px; height:18px; flex-shrink:0; opacity:.85; }
```

- [ ] **Step 3: Manual verification**

Resize the browser preview to mobile width (<768px), open the bottom-nav "More" sheet, confirm every item now shows an icon + label, grouped exactly as before (no group renamed, no item moved).

- [ ] **Step 4: Commit**

```bash
git add app/static/dashboard.html app/static/dashboard.css
git commit -m "feat(dashboard): add icons to mobile more-sheet items (parity with desktop)"
```

---

### Task 6: Full acceptance pass

**Files:** none (verification only).

- [ ] **Step 1: Run the JS unit tests**

Run: `node --test scripts/nav-group-visibility.test.mjs`
Expected: PASS, 4/4.

- [ ] **Step 2: Run the existing Node test suite to confirm no collateral breakage**

Run: `node --test scripts/*.test.mjs`
Expected: all existing tests still pass (this plan touches no file any existing `.test.mjs` covers, but confirm before calling this done).

- [ ] **Step 3: Run the Python test suite**

Run: `python3 -m pytest -q`
Expected: same pass/skip counts as before this plan started (no backend files touched, so this is a no-op regression check).

- [ ] **Step 4: Manual browser regression checklist**

Using the project's browser-preview tooling against a real or mocked dashboard session, verify (per spec §8):
- [ ] Every tab in spec §3's table still triggers its listed load function/endpoint(s) on first open.
- [ ] `appeals-pending-badge`, `appeals-pending-badge-mobile`, `issues-open-badge`, `issues-open-badge-mobile`, and the live-alert badges still update.
- [ ] Keyboard arrow/Home/End navigation still cycles every currently-visible tab.
- [ ] Mobile more-sheet opens and shows icons + unchanged grouping.
- [ ] As a plain org-teacher (non-owner): Organization group is hidden entirely; as a billing-owner org-teacher: Organization group shows with only Billing visible.
- [ ] As admin: Organization group fully visible; Exam group's Questions/Chat/Tools/Review hidden (existing behavior, unchanged).
- [ ] As superadmin: Platform group visible.
- [ ] Overview is the default landing panel; its stat tiles and continue-list populate correctly.
- [ ] Both dark and light theme render correctly (per this session's earlier finding that hardcoded colors silently break in light theme — re-check every new CSS rule in Tasks 3/4/6 uses tokens, not hardcoded hex).
- [ ] No emoji present in any markup touched by this plan.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(dashboard): nav redesign acceptance pass complete"
```
