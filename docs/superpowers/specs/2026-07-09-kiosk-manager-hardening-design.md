# kiosk-manager.js Hardening — Design

## Goal

`lib/kiosk-manager.js` manages the kiosk-lockdown exam window — the actual
anti-cheat enforcement layer students are inside during a real exam. It has
the highest cyclomatic complexity of any function in the entire repo
(`createExamWindow`, CCN 94) and **zero automated test coverage** on any of
its window-lifecycle logic. Reduce both: cut the complexity where it's
genuinely reducible, and build a real regression harness for the
highest-stakes, currently-unverified code in the product. Preventive
maintenance, no deadline.

## Background

`lib/kiosk-manager.js`: 732 lines, sole-owned by Arihant (bus factor 1 —
an accepted, known tradeoff for this project, not itself a finding to act
on). `createExamWindow()`: CCN 94, nesting 4, modified across 23 commits.
`createLobbyWindow()`: CCN 31, nesting 3, modified across 12 commits.
`releaseKiosk()` also nests 4 levels. 22 bug-fix commits in 6 months.

Investigation findings (verified against real commits and the current code,
not assumed):

1. **The CCN-94 number is real but structurally inflated, not intrinsic.**
   `_armExamLockdown` — the ~380-line lockdown-arming routine (panic/
   emergency-shortcut registration with rollback-on-partial-failure,
   blur/focus tracking with its own screen-capture-evidence state machine,
   platform-gated focus-steal, mac-only menu/dock suppression) — is defined
   as a nested closure *inside* `createExamWindow` rather than a top-level
   function. That alone means its ~15-20 branches count toward the outer
   function's complexity score. The closure variables it needs
   (`mainWindow`, `currentSessionId`, `studentToken`, `powerBlockId`, etc.)
   are already accessed via module-level getters elsewhere in the file, so
   pulling it out to a module-level function taking them as parameters is a
   mechanical, low-risk refactor — not a rewrite of the platform logic.
   Concrete evidence some of the complexity IS genuinely irreducible: the
   macOS `app.focus({steal:true})` vs. Windows `moveTop()` branch is
   preceded by ~30 lines explaining why `AllowSetForegroundWindow`/
   `AttachThreadInput` were rejected — a real per-OS API difference, not
   padding. Same for the ~40-line `setContentProtection` comment explaining
   why macOS's `ScreenCaptureKit` ignores `NSWindow.sharingType` entirely.
   Verdict: extract `_armExamLockdown` to module scope (halves reported CCN,
   zero behavior change); leave the genuine platform-branching as-is.
2. **Window-loading reliability was fixed in escalating layers instead of
   solved once** — a 5-commit chain (`e08c95d5`→`4140649a`→`d34926a9`→
   `e839eccf`, plus `1410006e`) where each fix revealed the next failure
   mode: blank window → CDN served HTML as `text/plain` so Chromium refused
   to render it → custom `procta-lobby://` protocol fixed the lobby but not
   the exam window specifically → a transient server outage left an
   already-open lobby permanently stuck (the fallback-tier state machine
   only stepped forward, never back). Root cause: packaged-app resource
   loading (asar/`file://`/CDN across Win+Mac) is inherently fragile, and
   nothing tests "does the packaged build actually load" before release —
   each failure mode was discovered live, in the field.
3. **The fail-open invariant (a student must never be trapped in an
   unclosable window with no live renderer for the panic-chord) has been
   re-derived/tightened repeatedly** — it's the most heavily-commented,
   most re-verified logic in the file, evidence it's been nearly gotten
   wrong more than once (`b5b081d7` and the surrounding unarmed-until-
   `did-finish-load`/12s-fallback/main-frame-only-guard scaffolding).
4. **Zero automated test coverage on any of this.** `tests/test_kiosk_
   attestation.py` (463 lines) tests the *server-side* HMAC attestation
   verification (`app/services/kiosk_attest.py`) — it never imports or
   references `lib/kiosk-manager.js`. The Node `--test` suite
   (`scripts/*.test.mjs`) doesn't call `createExamWindow`, `createLobbyWindow`,
   `releaseKiosk`, or `handlePanicUnlock` either. Every one of the 22 fixes
   was validated exclusively by shipping to production and testing on real
   hardware — there is no regression harness at all for window
   construction, fail-open load handling, lockdown-arming rollback, or
   blur/refocus timing.

**Explicitly not a bug pattern**: the macOS platform-quirk commits
(Mission Control/Launchpad/DND), including the ones reverted after
real-hardware testing disproved them, are the normal cost of black-box
testing against undocumented OS behavior with no CI signal — not a fixable
defect class. The negative results are already documented in-code so they
aren't re-attempted; nothing further to do there.

## Scope

**In scope:**
- Extract `_armExamLockdown` (and its nested blur/screen-capture-evidence
  state machine) to module-level functions, cutting `createExamWindow`'s
  reported CCN roughly in half with no behavior change.
- A real automated lifecycle test harness: window construction, fail-open
  load-failure handling (the exact `did-fail-load`/`did-finish-load`/12s-
  fallback race), lockdown-arming with simulated partial-registration
  failure (rollback path), `releaseKiosk`'s cleanup/reopen sequencing, and
  `handlePanicUnlock`. Mocked Electron `BrowserWindow`/IPC, matching the
  existing `Module._load` electron-mock pattern already used in
  `scripts/*.test.mjs` for `main.js`.
- A packaged-build smoke test in CI: unpack the built asar, launch it
  (headless-ish), assert `did-finish-load` actually fires — closing the gap
  that caused the 5-commit window-loading escalation chain.
- Regression tests for the specific historical failure modes: the
  `text/plain`-CDN-serving case, the exam-window-vs-lobby asar path
  divergence, and the stuck-lobby-after-outage case (fallback tier must be
  able to step back, not just forward).

**Out of scope:** the genuine platform-specific branching (macOS focus-steal
vs. Windows, screen-capture-protection limitations) — this is irreducible
and already correctly documented in-code, not a target for simplification.

## Approach

1. **Mechanical refactor first**: extract `_armExamLockdown` to module
   scope. This is the lowest-risk, highest-clarity change (no behavior
   change, just where the code lives) and makes the subsequent test-writing
   step easier — a module-level function is easier to unit test in
   isolation than a closure buried inside a 94-CCN function.
2. **Build the lifecycle test harness** against the now-extracted
   structure: window construction, fail-open race, lockdown-arming +
   rollback, release/reopen, panic-unlock. This is the highest-value work
   in this spec — it's the first regression coverage this code has ever
   had.
3. **Add the packaged-build smoke test to CI**, closing the gap that
   caused the 5-commit escalation chain (test the actual failure surface —
   packaged resource loading — not just unit-level logic).
4. **Regression tests for the 3 historical failure modes** listed above,
   so each specific bug that already happened once literally cannot happen
   silently again.

## Testing & sequencing

Extract `_armExamLockdown` → write lifecycle tests against the refactored
structure → add historical-failure-mode regression tests → add the
packaged-build CI smoke test last (it's the most infrastructure-heavy piece
and benefits from the unit-level harness existing first, to isolate
"packaging problem" from "logic problem" if the smoke test ever fails).
Self-review every diff; given this is the anti-cheat enforcement layer,
manual real-hardware verification (Mac + Windows) remains necessary before
any release even with the new automated coverage — the tests catch
regressions, they don't replace the platform-quirk verification process
already in place.

## Success criteria

- `createExamWindow`'s reported cyclomatic complexity is roughly halved via
  the `_armExamLockdown` extraction, with zero behavior change (verified by
  the new lifecycle tests passing identically before and after — write them
  against the pre-refactor structure first if needed, then confirm they
  still pass post-refactor).
- A real, mocked-Electron lifecycle test suite exists covering window
  construction, fail-open handling, lockdown-arming + rollback, release,
  and panic-unlock — first automated coverage this code has ever had.
- A packaged-build smoke test runs in CI per platform.
- The 3 historical failure modes each have a dedicated regression test.
- Full existing test suite passes with zero regressions.
