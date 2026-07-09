# auth.py Hardening — Design

## Goal

Reduce the recurring-bug rate in `app/routers/auth.py` — the single most
bug-prone file in the repo (56 bug-fix commits in 6 months) — by closing off
the *classes* of mistake that keep recurring, not just patching individual
instances. Preventive maintenance, no deadline.

## Background

`app/routers/auth.py`: 2590 lines, cyclomatic complexity up to 51, nesting up
to 7, 41 dependents, 56 bug-fixes in the last 6 months (highest in the repo).
Despite ~80+ existing tests spread across `tests/test_cookie_auth.py`,
`test_auth_lockout.py`, `test_local_auth.py`, `test_auth_and_sessions.py`,
`test_api_auth.py`, `test_billing_gate_authoring.py` (repowise's "no test
file" signal was a naming-convention false positive, same pattern seen
elsewhere in this repo tonight), the file keeps needing fixes. Real,
commit-verified root causes:

1. **Inline reimplementation of logic that already exists in a shared
   helper, with a mapping bug each time.** `45b2fbd4`: refresh-token replay
   revocation used an inline `UPDATE` instead of the existing
   `_revoke_auth_sessions_for_user` helper, and got the `user_kind` mapping
   wrong (`"student"` vs `"student_account"`) — 0 rows matched, so a
   detected-stolen token stayed valid for ~15 minutes. Same shape as
   `3d87b87b` (`assert_session_accessible` returned early without checking
   org ownership) and `2c626251` (self-signup insert omitted `status`,
   silently landing in the wrong verification branch). Three separate
   incidents, same underlying pattern: a correct shared helper existed right
   next to each buggy inline duplicate.
2. **Raw asyncpg connections bypassing the RLS-aware `PostgresTable`
   wrapper.** `774402c5`: three code paths acquired a raw pool connection
   directly, skipping the `app.*` RLS GUCs `PostgresTable.execute` normally
   sets — signup, invite-cap, and the TTL sweep all silently failed under
   the restricted DB role (0-row matches, not errors, so nothing crashed
   loudly).
3. **Duplicated verify-token logic between `verify_admin_token` and
   `verify_student_auth_token`** (`app/auth/admin_auth.py`) — both
   independently do decode → jti/cache-revocation-check → status check →
   idle-timeout touch in near-parallel structure. This is the concrete
   source of the 11% code-duplication finding. `3d87b87b` also fixed a
   string-matching-instead-of-typed-exception bug here (`"expired" in msg`
   instead of catching `jwt.ExpiredSignatureError`), which silently breaks
   if the underlying library ever changes its error message format.
4. **A real, load-bearing hidden-coupling case with `exam.py`:** `ed55af37`
   — `exam.py`'s `validate_student` independently re-derives
   `teacher_id`/`exam_id` from `student_invites` to enforce the enrollment
   window, duplicating enrollment-resolution logic that auth.py's
   signup/invite-accept path also implements, with no shared function and
   no import edge between the two files. A change to invite semantics in
   one is easy to miss in the other.

**Explicitly not real coupling** (verified, not assumed): the co-changes
with `admin_sessions.py` and `public.py` are mostly cross-cutting
security/observability sweeps (`e33bfec0` log-injection CodeQL sweep,
`9cba0999` PII-masking sweep, `bdb1eb32` centralized reauth gate) that touch
every router by design — not evidence of a silent dependency. The
`admin_sessions.py`/`public.py` relationship is closer to API-contract drift
with the frontend (route paths, response shapes) than a backend coupling
problem, and isn't addressed by a Python-side refactor — flagged as a
separate, smaller finding, not part of this spec's fix list.

## Scope

**In scope:**
- A CI-enforced convention (grep-based lint check, matching this repo's
  existing pattern of guard scripts gating the `tests/pytest` CI job) that
  flags: (a) any raw SQL/inline auth-session mutation outside
  `_revoke_auth_sessions_for_user`, (b) any raw pool `.acquire()` call inside
  `app/routers/` or `app/auth/` outside the `PostgresTable` wrapper.
- Extract `resolve_student_enrollment()` as a shared helper used by both
  `auth.py`'s signup/invite-accept path and `exam.py`'s `validate_student`,
  eliminating the independent re-derivation.
- Consolidate `verify_admin_token`/`verify_student_auth_token`'s shared
  decode → jti-check → status-check → idle-timeout-touch sequence into one
  parameterized helper both call, removing the 11% duplication.
- Regression test for each of the three historical bugs above (the
  `user_kind` mapping bug, the RLS-bypass silent failure, the string-match
  JWT-expiry check) so the exact failure mode can't reappear unnoticed.

**Out of scope:** the `admin_sessions.py`/`public.py` API-contract-drift
finding (frontend/backend response-shape mismatch) — real, but it's a
different problem needing a different fix (shared schema/typed contract,
not a Python refactor) and touches the frontend, which is dashboard-app.js's
spec's territory, not this one's.

## Approach

1. **Root-cause fixes first** (the 3 historical bug patterns above), each
   with a regression test proving the specific failure mode is closed.
2. **Extract `resolve_student_enrollment()`**, migrate both call sites
   (auth.py, exam.py) to use it, delete the duplicated logic. Test: a
   single behavior change to enrollment-window logic is now impossible to
   apply to only one of the two call sites, because there's only one.
3. **Consolidate the two verify-token functions** into one parameterized
   helper (student vs. admin as a parameter or two thin wrappers over one
   shared core), test both existing call sites' behavior is unchanged.
4. **Add the two CI guard scripts** (inline-auth-mutation grep,
   raw-pool-acquire grep), matching the style of this repo's existing guard
   scripts in `.mypy.ini`-adjacent CI gating. Test: each guard script has a
   test fixture that intentionally violates the rule and confirms the
   script catches it (matching the existing pattern for other guard
   scripts in this repo, e.g. `scripts/check_pg_select_syntax.py`).

## Testing & sequencing

Root-cause fixes + regression tests first (lowest risk, highest immediate
value) → shared-helper extractions (medium risk, touches two call sites
each — run full auth+exam test suites after each) → CI guard scripts last
(pure addition, easiest to verify in isolation). Self-review every diff,
check all callers before committing, per this session's established
discipline.

## Success criteria

- The 3 historical bug patterns each have a fix + regression test.
- `resolve_student_enrollment()` exists with a single implementation used by
  both auth.py and exam.py.
- `verify_admin_token`/`verify_student_auth_token` share one core
  implementation.
- Two new CI guard scripts are wired into the `tests/pytest` gate, each
  with its own self-test.
- Full existing test suite (2570+) passes with zero regressions, checked
  incrementally per change.
