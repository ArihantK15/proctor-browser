# Coding Module Hardening — Design

## Goal

Make the coding-exam module (sandbox execution + grading + authoring) reliably
resilient long-term: fewer recurring production incidents, no silent
crash/hang failure modes, and lower structural complexity in the highest-risk
functions so future changes are safer. This is a preventive-maintenance pass
(no deadline forcing it) — depth over speed.

## Background

A repo-wide code-health scan (repowise) plus manual verification identified
the coding module as containing 2 of the repo's top-5 hotspots by combined
churn/complexity/bug-history risk score (`app/routers/coding.py` at 99%,
`execsvc/runner.py` at 84%). Key data points, verified against real code and
git history (not taken at face value from the tool):

- `coding_judge()` (`app/routers/coding.py:299-417`) — cyclomatic complexity
  31, nests 5 levels, cognitive complexity 56, modified in 8 of the last
  dozen commits touching the file. This is the exam-answer grading logic.
- `execsvc/runner.py` — 6 bug-fixes in the last 6 months; `run_in_isolate`
  nests 4 levels.
- `execsvc/app.py` — 3 bug-fixes in 6 months; 90-day churn rewrote 183% of
  the file (near-total-rewrite pace), but complexity itself is low (CCN 4) —
  the churn is repeat *patching*, not structural debt.
- `admin_coding.py` — 4 bug-fixes in 6 months; three functions
  (`upsert_coding_question`, `_clean_cases`, `_clean_options`) each flagged
  for complexity (CCN 15-17).
- Smaller, concretely-verified issues: two intentionally-swallowed
  exceptions in `execsvc/microvm.py:135` (`proc.kill()` in a `finally`,
  killing an already-dead process can raise) and `execsvc/runner.py:138`
  (`os.unlink()` on a temp meta file in a `finally`) — both are
  defensible best-effort cleanup, not bugs, but currently have zero
  observability if they ever actually fire; an N+1 DB call inside
  `admin_coding.py`'s question-upsert loop (one `INSERT` per test case,
  up to 50); ~20% code duplication in `coding.py` (a 29-line
  clone).

Correction to the raw tool signal: repowise flagged all four files as having
"no paired test file." This is false — `coding.py` has 31 existing tests
(`tests/test_coding_router.py`), `admin_coding.py` has 22
(`tests/test_admin_coding.py` + `tests/test_admin_coding_validation.py`),
`execsvc/runner.py` has 12 (`execsvc/tests/test_runner_isolate.py` +
`test_runner_meta.py`), `execsvc/app.py` has 8 (`execsvc/tests/test_app.py`).
The tool's "no test file" check only matches an exact `test_<filename>.py`
naming convention this repo doesn't always follow. The real signal that
matters is: **despite existing coverage, these files keep needing repeat
fixes** — meaning the coverage isn't catching the actual failure modes, and/or
the underlying logic is too complex to reason about safely. That's the actual
justification for this pass, not "zero tests."

`dolos-svc` (plagiarism detection) was evaluated and excluded — it scores
9.15/10 on the same health metric, no meaningful signal.

## Scope

**In scope**, one coherent pass covering three layers of work per file:

| File | Root-cause triage | Structural refactor | Defense-in-depth guardrails |
|---|---|---|---|
| `app/routers/coding.py` (`coding_judge`) | Yes | Yes — full refactor into composable stages | Yes |
| `execsvc/runner.py` (`run_in_isolate`) | Yes | Yes — lighter refactor (cut nesting from 4 to ≤2; much lower starting complexity than `coding_judge`, CCN 8 not 31) | Yes |
| `execsvc/app.py` | Yes | No — complexity is already low (CCN 4); the churn is from repeat patches, not structural debt, so a rewrite wouldn't address the actual cause | Yes |
| `app/routers/admin_coding.py` (`upsert_coding_question`, `_clean_cases`, `_clean_options`) | Yes | Yes — all three flagged functions | Yes |

**Out of scope** (explicitly deferred to their own future specs, same
process, not bundled here to keep this spec coherent and shippable):
- The repo's other top hotspots: `app/static/dashboard-app.js`,
  `lib/kiosk-manager.js`, `app/routers/auth.py`. (`proctor.py`, also a
  top-5 hotspot, already received targeted test coverage in a prior session
  and is not re-scoped here.)
- The mobile app (teacher scheduling + lightweight student proctoring) —
  separate, larger, net-new-product spec to follow this one.
- `dolos-svc` — already healthy, no changes.

## Approach

### 1. Root-cause triage (all 4 files)

Read-only investigation before any code changes. For each file's real
bug-fix commit history (via `git log` + `git show` on the actual fix
commits, not guesswork), classify each recorded fix as either:
- **A genuine recurring pattern** — the same underlying mistake or missing
  guarantee causing multiple fixes → fix the true root cause once, add a
  regression test that would have caught it.
- **Normal churn** — unrelated one-off issues that happened to land in a
  frequently-touched file → note briefly, don't invent a problem that isn't
  there.

This mirrors the pattern already used successfully earlier this session on
the repo-wide recurring-bug-file sweep (real finding: `behavioral_analysis.py`
had a live bug from an incompletely-applied prior fix; several other flagged
files turned out to already be resolved). Same rigor here: verify against
current code, not assumptions.

### 2. Structural refactor — `coding_judge` and `run_in_isolate`

No students are currently using the platform, so there is no live grading
traffic to protect — shadow-mode/parallel-run verification (comparing old vs.
new judge output on real traffic before cutover) was considered and is
**not needed**. Instead:

1. Write **characterization tests** that lock in the function's current
   observable behavior across its real branches (test-case pass/fail
   combinations, timeout handling, malformed-submission handling, partial
   credit if applicable) — this is the safety net, proving the refactor
   preserves behavior even without live traffic to compare against.
2. Refactor `coding_judge` into composable stages: parse submission → run
   test cases → compare outputs → compute score. Each stage becomes an
   independently named, independently testable unit instead of one 112-line,
   31-branch function.
3. Refactor `run_in_isolate` to cut its nesting (4 levels → ≤2) using the
   same early-return/extracted-helper techniques — lighter treatment since
   its starting complexity (CCN 8) is far lower than `coding_judge`'s (CCN 31).
4. Confirm the characterization tests still pass after each refactor step.
5. "Don't permanently break anything" is the hard constraint throughout:
   every step is committed incrementally, tests run after each change, and
   nothing is force-pushed or destructively reset.

`execsvc/app.py` and the three flagged `admin_coding.py` functions get the
same characterization-test-first treatment where refactored, scaled to their
lower complexity (no dedicated multi-stage pipeline needed — straightforward
extract-function refactors are sufficient).

### 3. Defense-in-depth guardrails

Concrete, verified failure modes and their fixes:

| Failure mode | Current behavior | Fix |
|---|---|---|
| Swallowed exception in `execsvc/microvm.py:135` (`proc.kill()` in `finally`) | Silent — correctly non-fatal (killing an already-dead process can raise) but zero observability if it ever fires | Add a debug-level log line; do NOT re-raise — re-raising from this `finally` would turn a successful run into a false failure over a harmless cleanup race |
| Swallowed exception in `execsvc/runner.py:138` (`os.unlink()` in `finally`) | Same shape — correctly non-fatal, zero observability | Same fix: log, don't re-raise |
| Sandbox subprocess spawn (`execsvc/runner.py:100,118,123,140`) | `isolate_cmd.py` confirms `--time`/`--wall-time` ARE passed to isolate, so the sandboxed program's timeout is genuinely enforced already (verified, not assumed) — but the outer `subprocess.run()` calls that invoke `isolate` itself have no Python-level `timeout=` | Add a defense-in-depth `timeout=` to the outer `subprocess.run` calls (wall_ms + a small buffer), guarding against `isolate` itself failing to self-terminate — narrower and lower-priority than originally framed, since the primary timeout already works |
| N+1 DB call in `admin_coding.py`'s `upsert_coding_question` loop | One DB round-trip per test case being saved | Batch into a single query |
| ~20% code duplication in `coding.py` (29-line clone) | `coding_run` and `admin_coding_preview_run` independently implement the same sample-case-execution loop (fetch cases → run each → compare → build result list) | Extract a shared `_run_sample_cases(question_id, language, source, time_limit_ms)` helper; each endpoint keeps its own distinct auth/ownership check, only the execution loop is shared |

Each guardrail fix gets a test exercising the specific failure mode it
addresses (e.g., a test that a KeyboardInterrupt-shaped exception is no
longer swallowed by the narrowed except clause; a test that a
never-returning sandbox execution produces a timeout result, not a hang).

## Testing & sequencing

Per file, in this order: (1) root-cause triage read pass, (2) write
characterization tests for any function being refactored, (3) fix verified
root causes + add regression tests, (4) apply the structural refactor where
in scope, (5) apply defense-in-depth guardrails with a test per guardrail.
Self-review the diff and run the full relevant test suite after each file
before moving to the next — same discipline used earlier this session (git
status before touching anything; re-read every changed file; check all
callers before committing).

Suggested file order: `execsvc/app.py` first (smallest, no refactor step,
fastest to build momentum and confirm the process works) → `execsvc/runner.py`
→ `admin_coding.py` → `app/routers/coding.py` / `coding_judge` last (largest,
riskiest, benefits most from the process being proven on the earlier three
files first).

## Success criteria

- Every root-cause-classified "genuine recurring pattern" has a fix and a
  regression test.
- `coding_judge` and `run_in_isolate` are refactored with characterization
  tests passing before and after.
- All 5 verified guardrail gaps (2 swallowed-exception log additions, the
  defense-in-depth outer subprocess timeout, the N+1 query, the code
  duplication) are closed, each with a dedicated test.
- Full existing test suite (currently 2570+ passing) still passes with zero
  regressions throughout, checked incrementally per file, not just at the end.
- Nothing is force-pushed, destructively reset, or left in a broken
  intermediate state at any commit.
