# dashboard-app.js Hardening — Design

## Goal

`app/static/dashboard-app.js` is the repo's largest frontend file (10,854
lines and growing) and its most bug-prone (51+ fix commits in 6 months, now
122 total commits touching it). Close off the specific recurring bug
classes, remove a live landmine that already caused one full-dashboard
crash, and fix the real code duplication with `student-app.js`. Preventive
maintenance, no deadline.

## Background

`app/static/dashboard-app.js`: cyclomatic complexity up to 54, nesting up to
9, 23 dependents. Verified findings from real commit history and reading the
current file structure directly (not assumed from raw metrics):

1. **Exam-scoping leaks — the largest single bug family, 5+ commits, same
   root cause each time.** `6b94fbf3` (a live SSE stream stayed bound to the
   previous `exam_id` after switching exams, because the filter is baked
   into the `EventSource` URL at connect time, not re-checked), `3f624650`
   (exam selector bar shown on tabs that aren't exam-scoped), `b3c823fd`
   (ID-verification queue silently hid pending students — filtered by
   `exam_id` on a step that happens *before* exam selection), `5853550d`/
   `1257e001` (stale question/picker data not reloaded on exam switch).
   Root cause: `currentExamId` is read fresh in some code paths (polling)
   but baked into a connection or cached DOM state at a different time in
   others (SSE URL, picker state) — the two drift out of sync.
2. **A live landmine: dashboard-app.js redefines its own `_escHtml`,
   duplicating `_safe.js`'s version — the exact collision class that
   already crashed the entire dashboard once.** `9a4b58f5`: a `const esc`
   in dashboard-app.js collided with `_safe.js`'s `function esc`
   (`_safe.js` loads first) — a parse-time crash that broke login and
   everything else, fixed the day after `d3e13d9c` had *added* that same
   `const esc = _escHtml` to repair 7 broken call sites. Today,
   dashboard-app.js (line ~6720) still independently redefines `_escHtml`
   — it doesn't collide right now only because both are `function`
   declarations (last one silently wins instead of throwing). This is not
   a hypothetical risk; it's the same landmine sitting live in the file
   right now.
3. **Modal/state-lifecycle ordering bugs — a reset helper's side effects
   outlive the call site's assumptions.** `f2607fd6`: `openTimeline()`
   called `closeModal()` to dismiss an unrelated modal, but `closeModal()`
   also nulls `currentSessionId` as a side effect, so the very next line
   fetched `/timeline/null` on every single open. Same shape as
   `b0b66624`'s auth-expiry loop.
4. **Real, verified logic duplication with `student-app.js`.** 10 commits
   co-touch both files; `b0b66624` explicitly states it's porting a fix
   that "landed in student-app.js weeks ago." `doLogout`, `_escHtml`, and
   status-badge maps exist independently in both files
   (student-app.js:887/1449, dashboard-app.js:3601/10751) — not shared code
   with two callers, but copy-drift where a fix to one doesn't propagate
   until someone hits the same bug twice in the other.
5. **Hand-typed SSE payload contract with no shared schema.** `bf69a1c9`:
   the SSE event shape (`{"kind": "submitted", "session_id": ...,
   "all_sessions": [...]}`) is independently produced by two Python call
   sites (`exam.py`'s student-submit path, `admin_sessions.py`'s
   force-submit path) and consumed by JS listeners with no shared type —
   the bug fixed was exactly a field mismatch between the two producers.

**Structural verdict on the 10,854-line size**: not a disorganized dumping
ground — it's a single top-level IIFE with 201 top-level functions,
consistently verb-prefixed and grep-navigable, and most of the 122 commits
are genuine `feat:`/`chore:` work (cohort filtering, exam archiving,
guardian consent, evidence viewer), not rework. The real structural risk is
narrower than "split this file": everything shares one flat global scope
with zero per-domain isolation, which is precisely what makes finding #2
(the `_escHtml` collision) and finding #4 (student-app.js duplication)
possible at all.

## Scope

**In scope:**
- Generalize the `_sseExamId` stamp-and-compare pattern (already the
  correct fix shape, introduced in `6b94fbf3`) into a single reusable
  scope-token helper, and migrate the other exam-scoped fetches/long-lived
  connections (picker state, ID-verification queue, question loading) onto
  it, rather than leaving each as an ad hoc, independently-solved instance
  of the same problem.
- Remove dashboard-app.js's local `_escHtml` redefinition; use `_safe.js`'s
  version exclusively. Add an ESLint `no-redeclare`/`no-shadow`-style check
  (or a grep-based guard script, matching this repo's existing guard-script
  convention) that fails CI if dashboard-app.js or student-app.js
  redefines a name `_safe.js` already exports, so this exact landmine class
  can't reappear silently.
- Extract the duplicated `doLogout`/`_escHtml`/status-badge-map logic
  shared with `student-app.js` into `_safe.js` (which already partially
  plays this shared-utility role), and migrate both files to use the
  shared version.
- A shared schema/contract check for the SSE payload shape (`bf69a1c9`'s
  bug) — a Python dataclass or shared JSON-schema doc referenced by both
  `exam.py` and `admin_sessions.py`'s emit sites, checked against what the
  JS listener actually reads.
- Regression test for the `closeModal()` side-effect-ordering bug shape:
  capture-before-reset, and a convention note (or lint check) that shared
  state resets shouldn't live inside a generically-named "hide this modal"
  utility.

**Out of scope for this spec**: a full module/bundler split of the file.
The investigation found the internal organization (verb-prefixed, grep-
navigable) is not itself the problem — the flat global scope is. Fixing the
specific collision/duplication classes above addresses the actual risk
without the cost and regression surface of restructuring a 10,854-line file
wholesale. If a future spec wants to revisit a real module split, it should
be scoped separately once these narrower fixes have had time to prove
whether they're sufficient.

## Approach

1. **Fix the live landmine first** (remove the duplicate `_escHtml`,
   confirm `_safe.js`'s version is used everywhere) — lowest effort,
   highest immediate risk reduction, since this is an active, already-once-
   fired hazard, not a theoretical one.
2. **Generalize the exam-scope-token pattern** and migrate the known
   drift-prone call sites (SSE, picker, ID-verify queue, question loading)
   onto it. Test each migrated call site against the specific historical
   bug it corresponds to (switch exams mid-session, confirm no stale data).
3. **Extract the student-app.js-shared logic** (`doLogout`, escaping,
   status-badge maps) into `_safe.js`, migrate both files, delete the
   duplicates. Test both dashboard and student flows still behave
   identically post-migration.
4. **Add the SSE payload shared-schema check** and the `closeModal()`
   regression test + convention note.
5. **Add the CI guard script** for the redefinition-collision class, as
   the last step — once the known instance is already fixed, the guard
   just needs to prove it would have caught it.

## Testing & sequencing

Landmine fix → scope-token generalization → student-app.js duplication
extraction → SSE schema check + modal regression test → CI guard script.
Each step gets its own self-review and a run of the relevant existing
tests (`tests/browser/test_dashboard_state.py` plus any new tests) before
moving to the next, matching this session's established discipline. Given
this file has no per-domain test isolation today, expect to write focused
new tests alongside each fix rather than relying on broad existing coverage
alone.

## Success criteria

- The duplicate `_escHtml` is gone; a CI guard prevents its class of bug
  from reappearing silently.
- The exam-scope-token pattern is used by every exam-scoped fetch/
  connection identified in this investigation, not just the original
  SSE case.
- `doLogout`/`_escHtml`/status-badge logic has one implementation, shared
  by dashboard-app.js and student-app.js.
- The SSE payload shape has a shared schema check catching producer/
  consumer drift.
- Full existing test suite passes with zero regressions, checked
  incrementally per change.
