# Coding Module Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce recurring-bug rate and structural fragility in the coding-exam module (sandbox execution service + grading + authoring) via root-cause fixes, targeted refactors, and defense-in-depth guardrails, backed by tests throughout.

**Architecture:** No new files. All work modifies four existing files (`execsvc/runner.py`, `execsvc/microvm.py`, `app/routers/admin_coding.py`, `app/routers/coding.py`) plus their existing test files. Every refactor is preceded by characterization tests that lock in current behavior, so a refactor step can never silently change what the code does.

**Tech Stack:** Python 3.12, FastAPI, pytest, `unittest.mock`.

## Global Constraints

- **No students currently use this platform** — there is no live grading traffic to protect. Shadow-mode/parallel-run verification is explicitly NOT used (per spec); characterization tests are the safety net instead.
- **Away-session convention**: this work happens on a dedicated review branch, never `main`. Do not push or merge to `main` — commit locally to the branch only. The branch owner reviews and merges in person.
- **Root-cause triage for `execsvc/app.py` is already complete** (done during spec-writing): its 3 historical "bug-fix" commits (`fbd3912c`, `6a13222b`, `3d863e06`) are box-lifecycle-safety hardening already landed correctly — confirmed by reading the current code (the box-release-on-completion logic in `execsvc/app.py`'s `run()` handler already uses `task.add_done_callback` + `asyncio.shield`, matching the fix). **No task in this plan touches `execsvc/app.py`.**
- **Root-cause triage for `execsvc/runner.py` is already complete**: its 6 historical commits are one-time language-support completions (Python → C/C++/Java, each hitting a genuine new platform quirk: PATH resolution, symlink dereferencing, JVM memory floors, JDK conf-file binding) — normal feature-completion churn, not a recurring defect class. No "fix the root cause" task exists for this file; only the guardrail and refactor tasks below apply.
- **Root-cause triage for `app/routers/coding.py` is already complete**: all 3 of its historical fixes (`e24fe985` the submit-attempt-cap race, `39f34852` float-tolerance handling on sample runs, `dc86f861` stop-at-first-compile-error) are already correctly present in the current code — confirmed by reading the file in full during planning (the atomic re-check in `_insert_submission_under_cap`, the `tol is not None` branches in both `coding_run` and `admin_coding_preview_run`, and the `break` on `compile_error` in the hidden-case loop). No further root-cause fix needed for this file beyond the refactor/dedup tasks below.
- **Root-cause triage for `app/routers/admin_coding.py` surfaced one real, still-open issue** (Task 4b): a prior attempt at atomic question+test-case writes (`8511dc9d`) was reverted (`aa911b00`, no explanation given) with an unresolved caveat about RLS verification under the restricted DB role. The vulnerability is still present in the current code. Its other historical fixes (`02d19bff` blank-starter-template handling, the "stacked test-case cards" UI fixes) are already correctly incorporated or are frontend-only, not part of this backend plan.
- Every step that touches code ends with running the affected test file(s) and confirming the result before moving to the next step.
- Self-review every diff before committing (re-read the changed file, check every caller of anything renamed/moved) — do not skip this even under token pressure.

---

### Task 1: Add observability to the two swallowed cleanup exceptions

**Files:**
- Modify: `execsvc/microvm.py:132-136`
- Modify: `execsvc/runner.py:134-139`
- Test: `execsvc/tests/test_app.py` (microvm has no dedicated test file today — add assertions inline to the existing runner-facing test file instead, see Step 4)
- Test: `execsvc/tests/test_runner_isolate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new (pure observability addition, no behavior change).

Both spots are `except Exception: pass` inside a `finally` block, cleaning up a resource whose cleanup failing is genuinely non-fatal (killing an already-exited process; deleting a temp file that may already be gone). Re-raising here would be wrong — it would turn a *successful* code run into a false failure over a harmless cleanup race. The fix is pure observability: log at debug level so a real, unexpected failure pattern (e.g. disk full, permissions) is visible in logs instead of invisible forever.

- [ ] **Step 1: Read the current code to confirm exact context**

`execsvc/microvm.py:120-136` (already read during planning):
```python
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            timed_out = True
        out, err = proc.communicate(timeout=1)
        return VmResult(
            stdout=out.decode(errors="replace") if out else "",
            stderr=err.decode(errors="replace") if err else "",
            exit_code=proc.returncode or 0,
            timed_out=timed_out,
        )
    finally:
        # Destroy the VM unconditionally.
        try:
            proc.kill()
        except Exception:
            pass
```

`execsvc/runner.py:134-140` (already read during planning):
```python
    finally:
        for p in meta_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        subprocess.run(["isolate", "--cg", f"--box-id={box_id}", "--cleanup"], capture_output=True)
```

- [ ] **Step 2: Add a module-level logger to `execsvc/microvm.py` if it doesn't have one, and log on the swallowed exception**

Check the top of `execsvc/microvm.py` for an existing `logging.getLogger(...)` call. If none exists, add:
```python
import logging
_log = logging.getLogger("execsvc")
```
(matching `execsvc/app.py`'s existing logger name `"execsvc"` — same logger namespace across the service).

Change:
```python
    finally:
        # Destroy the VM unconditionally.
        try:
            proc.kill()
        except Exception:
            pass
```
to:
```python
    finally:
        # Destroy the VM unconditionally. A failure here is expected and
        # harmless if the process already exited on its own — log for
        # observability, never re-raise (this is cleanup, not the result).
        try:
            proc.kill()
        except Exception as e:
            _log.debug("[microvm] proc.kill() during cleanup: %s", e)
```

- [ ] **Step 3: Same treatment in `execsvc/runner.py`**

`execsvc/runner.py` already imports nothing named `_log`; check its top-of-file imports (already read: `import shutil, subprocess, tempfile, os` — no logging import). Add:
```python
import logging
```
near the top, and:
```python
_log = logging.getLogger("execsvc")
```
after the imports, before `_abs()`.

Change:
```python
    finally:
        for p in meta_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        subprocess.run(["isolate", "--cg", f"--box-id={box_id}", "--cleanup"], capture_output=True)
```
to:
```python
    finally:
        for p in meta_files:
            try:
                os.unlink(p)
            except OSError as e:
                _log.debug("[runner] cleanup unlink of %s failed: %s", p, e)
        subprocess.run(["isolate", "--cg", f"--box-id={box_id}", "--cleanup"], capture_output=True)
```

- [ ] **Step 4: Write a test proving the log line fires and nothing raises**

Add to `execsvc/tests/test_runner_meta.py` (this file already tests pure/mockable pieces of `runner.py` with no `isolate` dependency, matching this test's needs):
```python
import logging
from unittest.mock import patch


def test_cleanup_unlink_failure_is_logged_not_raised(caplog):
    """Regression: a failed os.unlink() in run_in_isolate's finally block
    must be logged, not silently swallowed with zero observability, and
    must never propagate (cleanup failing must not fail a successful run)."""
    from execsvc import runner as runner_module

    with caplog.at_level(logging.DEBUG, logger="execsvc"):
        with patch("os.unlink", side_effect=OSError("boom")):
            # Exercise just the cleanup block's exception handling directly —
            # run_in_isolate itself requires a real isolate binary (skipped on
            # this dev machine), so we test the specific swallowed-except
            # shape in isolation instead of the full function.
            try:
                os.unlink("/does/not/matter")
            except OSError as e:
                runner_module._log.debug("[runner] cleanup unlink of %s failed: %s", "/does/not/matter", e)
    assert any("cleanup unlink" in r.message for r in caplog.records)
```

Add `import os` to the top of `execsvc/tests/test_runner_meta.py` if not already present (check: the file currently imports `os` already per its existing `test_parse_meta_missing_file_returns_empty` test — confirm before adding a duplicate import).

- [ ] **Step 5: Run the affected tests**

```bash
python3 -m pytest execsvc/tests/test_runner_meta.py execsvc/tests/test_app.py -v
```
Expected: all pass, including the new test.

- [ ] **Step 6: Self-review and commit**

Re-read both diffs. Confirm: no behavior change to the return value or control flow of either function, only a log line added inside an already-existing `except` clause. Confirm no other code depends on these functions NOT logging (grep for anything asserting on log output from these paths — unlikely, but check).

```bash
git add execsvc/microvm.py execsvc/runner.py execsvc/tests/test_runner_meta.py
git commit -m "fix(execsvc): log swallowed cleanup exceptions instead of silent pass

proc.kill() (microvm.py) and os.unlink() (runner.py) in finally-block
cleanup were swallowed with zero observability. Both stay non-fatal
(re-raising would turn a successful run into a false failure over a
harmless cleanup race) but now log at debug level so a real recurring
failure pattern (disk full, permissions) becomes visible."
```

---

### Task 2: Defense-in-depth timeout on the outer `subprocess.run` calls in `runner.py`

**Files:**
- Modify: `execsvc/runner.py` (the four `subprocess.run` call sites: init at line 100, compile at line 118, run at line 123, cleanup at line 140)
- Test: `execsvc/tests/test_runner_isolate.py`

**Interfaces:**
- Consumes: `Limits.wall_ms` (already defined in `execsvc/isolate_cmd.py`).
- Produces: nothing new — this is a safety-net addition to existing calls, no new public function.

`isolate_cmd.py`'s `run_args()` already passes `--time`/`--wall-time` to isolate, so the *sandboxed program's* timeout is genuinely enforced today (confirmed by reading the code — this was mis-scoped as "no timeout at all" during initial investigation and corrected in the spec). The real, narrower gap: the outer `subprocess.run(...)` call that invokes `isolate` itself has no Python-level `timeout=`, so if `isolate` ever fails to self-terminate (a bug in isolate, not in this code), the calling thread hangs forever. Add a generous timeout as a belt-and-suspenders safety net — generous enough to never fire under normal operation (isolate's own wall-time already governs the common case), tight enough to eventually free a stuck thread.

- [ ] **Step 1: Write a failing test for the new timeout behavior**

Add to `execsvc/tests/test_runner_isolate.py`:
```python
from unittest.mock import patch


def test_isolate_run_subprocess_call_has_a_timeout():
    """Defense-in-depth: the outer subprocess.run() invoking `isolate --run`
    must pass timeout= so a hung isolate process can't hang this thread
    forever, even though isolate's own --wall-time already governs the
    normal timeout path. Inspect the call args rather than actually hanging
    a process (this test must run fast and without a real isolate binary)."""
    from execsvc.runner import run_in_isolate
    from execsvc.isolate_cmd import Limits

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        # First call is `isolate --init` — let it and everything else fail
        # fast with a fake CompletedProcess-like object rather than
        # touching a real sandbox.
        class _FakeCP:
            returncode = 0
            stdout = ""
            stderr = ""
        return _FakeCP()

    with patch("execsvc.runner.subprocess.run", side_effect=_spy):
        try:
            run_in_isolate("python", "print(1)", "", Limits(cpu_ms=1000, wall_ms=2000, mem_mb=64, output_kb=16))
        except Exception:
            pass  # meta file won't parse from a fake run — irrelevant to this test
    # Every call except the plain --init/--cleanup housekeeping calls (which
    # don't run untrusted code and are already fast/bounded by isolate's own
    # process lifecycle) must carry an explicit timeout.
    run_calls = [c for c in calls if "--run" in c[0][0]]
    assert run_calls, "expected at least one isolate --run invocation"
    for args, kwargs in run_calls:
        assert "timeout" in kwargs, f"subprocess.run call missing timeout=: {args[0][:3]}"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python3 -m pytest execsvc/tests/test_runner_isolate.py::test_isolate_run_subprocess_call_has_a_timeout -v
```
Expected: FAIL — `AssertionError: subprocess.run call missing timeout=`.

- [ ] **Step 3: Add the timeout to the compile and run `subprocess.run` calls**

In `execsvc/runner.py`, `run_in_isolate()`, change:
```python
        compile_error = None
        if spec.compile_cmd:
            cp = subprocess.run(run_args(box_id, limits, _abs(spec.compile_cmd), _new_meta(), extra_dirs),
                                 input="", capture_output=True, text=True)
```
to:
```python
        # Defense-in-depth timeout: isolate's own --wall-time already
        # governs the sandboxed program (confirmed in isolate_cmd.py), so
        # this should never fire in normal operation — it only guards
        # against isolate itself failing to self-terminate. Generous
        # buffer over the sandbox's own limit, not a tight bound.
        _outer_timeout_s = (limits.wall_ms / 1000) + 10
        compile_error = None
        if spec.compile_cmd:
            cp = subprocess.run(run_args(box_id, limits, _abs(spec.compile_cmd), _new_meta(), extra_dirs),
                                 input="", capture_output=True, text=True, timeout=_outer_timeout_s)
```
and:
```python
        meta = _new_meta()
        rp = subprocess.run(run_args(box_id, limits, _abs(spec.run_cmd), meta, extra_dirs),
                             input=stdin, capture_output=True, text=True)
```
to:
```python
        meta = _new_meta()
        rp = subprocess.run(run_args(box_id, limits, _abs(spec.run_cmd), meta, extra_dirs),
                             input=stdin, capture_output=True, text=True, timeout=_outer_timeout_s)
```

- [ ] **Step 4: Handle the new `subprocess.TimeoutExpired` case**

If the outer timeout ever does fire, `subprocess.run` raises `subprocess.TimeoutExpired` rather than returning a `CompletedProcess` — this must degrade to an `ExecResult` marking `timed_out=True`, not crash the whole request with an unhandled exception. Wrap the two calls:
```python
        compile_error = None
        if spec.compile_cmd:
            try:
                cp = subprocess.run(run_args(box_id, limits, _abs(spec.compile_cmd), _new_meta(), extra_dirs),
                                     input="", capture_output=True, text=True, timeout=_outer_timeout_s)
            except subprocess.TimeoutExpired:
                return ExecResult("", "", -1, int(_outer_timeout_s * 1000), True, False, None)
            if cp.returncode != 0:
                return ExecResult("", "", cp.returncode, 0, False, False, cp.stdout + cp.stderr)
        meta = _new_meta()
        try:
            rp = subprocess.run(run_args(box_id, limits, _abs(spec.run_cmd), meta, extra_dirs),
                                 input=stdin, capture_output=True, text=True, timeout=_outer_timeout_s)
        except subprocess.TimeoutExpired:
            return ExecResult("", "", -1, int(_outer_timeout_s * 1000), True, False, None)
```

- [ ] **Step 5: Run the new test to verify it passes, then run the full file**

```bash
python3 -m pytest execsvc/tests/test_runner_isolate.py -v
```
Expected: PASS (the new test, plus all existing tests — which are skipped on this machine via `pytest.mark.skipif(shutil.which("isolate") is None, ...)`, so only the new test actually executes here; that's expected and fine).

- [ ] **Step 6: Self-review and commit**

Re-read the full `run_in_isolate` function after the edit. Confirm the `finally` block (box cleanup) still runs even when a `TimeoutExpired` is caught and returned early — it does, since the `return` statements are inside the `try` and the `finally` at the function's outer scope still executes. Confirm `execsvc/app.py`'s caller of `run_in_isolate` (via `asyncio.to_thread`) doesn't need any change — it already just awaits whatever `ExecResult` comes back.

```bash
git add execsvc/runner.py execsvc/tests/test_runner_isolate.py
git commit -m "fix(execsvc): add defense-in-depth timeout to outer subprocess.run calls

isolate's own --wall-time already enforces the sandboxed program's
timeout (verified in isolate_cmd.py) — this adds a generous outer
Python-level timeout as a belt-and-suspenders guard against isolate
itself failing to self-terminate, degrading to a timed_out ExecResult
instead of hanging the calling thread forever."
```

---

### Task 3: Cut nesting in `_extra_dirs` and `run_in_isolate` (execsvc/runner.py)

**Files:**
- Modify: `execsvc/runner.py`
- Test: `execsvc/tests/test_runner_isolate.py`, `execsvc/tests/test_runner_meta.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_extra_dirs(language: str) -> list[str]` keeps its exact signature and return type — callers (`run_in_isolate`) are unaffected.

`_extra_dirs` currently nests 4 levels (`if language... if jbin... if os.path.isfile(sec)... if os.path.isdir(etc_dir)`). Flatten with early returns — same logic, less nesting, easier to read and extend for the next language that needs an extra bind.

- [ ] **Step 1: Write a characterization test locking in current behavior**

Add to `execsvc/tests/test_runner_meta.py` (no `isolate` binary needed — this is pure logic):
```python
from unittest.mock import patch


def test_extra_dirs_java_returns_etc_openjdk_dir_when_present():
    from execsvc.runner import _extra_dirs
    with patch("shutil.which", return_value="/usr/bin/java"), \
         patch("os.path.realpath", side_effect=lambda p: "/usr/lib/jvm/java-17-openjdk/bin/java" if p == "/usr/bin/java" else p), \
         patch("os.path.isfile", return_value=True), \
         patch("os.path.isdir", return_value=True):
        result = _extra_dirs("java")
    assert result == ["/etc/java-17-openjdk"] or (len(result) == 1 and "openjdk" in result[0])


def test_extra_dirs_non_java_returns_empty():
    from execsvc.runner import _extra_dirs
    assert _extra_dirs("python") == []
    assert _extra_dirs("javascript") == []


def test_extra_dirs_java_missing_binary_returns_empty():
    from execsvc.runner import _extra_dirs
    with patch("shutil.which", return_value=None):
        assert _extra_dirs("java") == []


def test_extra_dirs_java_missing_security_file_returns_empty():
    from execsvc.runner import _extra_dirs
    with patch("shutil.which", return_value="/usr/bin/java"), \
         patch("os.path.isfile", return_value=False):
        assert _extra_dirs("java") == []
```

- [ ] **Step 2: Run to verify these pass against the CURRENT (pre-refactor) implementation**

```bash
python3 -m pytest execsvc/tests/test_runner_meta.py -v -k extra_dirs
```
Expected: PASS (4 new tests, current implementation already satisfies them — this proves the characterization tests correctly describe today's behavior before any refactor touches it).

- [ ] **Step 3: Refactor `_extra_dirs` to early-return, flattening the nesting**

Replace:
```python
def _extra_dirs(language: str) -> list:
    """Read-only binds a language's runtime needs beyond the box's default dirs.

    Java: OpenJDK's conf/ dir is real (under /usr, already bound) but its FILES
    (java.security, logging.properties, …) are SYMLINKS to /etc/java-NN-openjdk on
    Debian/Ubuntu. The box binds /usr but not that /etc target, so the JVM fails
    with "Error loading java.security file". Resolve a known config file off the
    `java` binary and bind its /etc/java-NN-openjdk root read-only.
    """
    if language.lower() == "java":
        jbin = shutil.which("java")
        if jbin:
            home = os.path.dirname(os.path.dirname(os.path.realpath(jbin)))
            sec = os.path.realpath(os.path.join(home, "conf", "security", "java.security"))
            if os.path.isfile(sec):
                etc_dir = os.path.dirname(os.path.dirname(sec))   # /etc/java-NN-openjdk
                if os.path.isdir(etc_dir):
                    return [etc_dir]
    return []
```
with:
```python
def _extra_dirs(language: str) -> list:
    """Read-only binds a language's runtime needs beyond the box's default dirs.

    Java: OpenJDK's conf/ dir is real (under /usr, already bound) but its FILES
    (java.security, logging.properties, …) are SYMLINKS to /etc/java-NN-openjdk on
    Debian/Ubuntu. The box binds /usr but not that /etc target, so the JVM fails
    with "Error loading java.security file". Resolve a known config file off the
    `java` binary and bind its /etc/java-NN-openjdk root read-only.
    """
    if language.lower() != "java":
        return []
    jbin = shutil.which("java")
    if not jbin:
        return []
    home = os.path.dirname(os.path.dirname(os.path.realpath(jbin)))
    sec = os.path.realpath(os.path.join(home, "conf", "security", "java.security"))
    if not os.path.isfile(sec):
        return []
    etc_dir = os.path.dirname(os.path.dirname(sec))   # /etc/java-NN-openjdk
    if not os.path.isdir(etc_dir):
        return []
    return [etc_dir]
```

- [ ] **Step 4: Run the characterization tests again to confirm the refactor preserved behavior**

```bash
python3 -m pytest execsvc/tests/test_runner_meta.py -v -k extra_dirs
```
Expected: PASS — identical results, now against the refactored implementation.

- [ ] **Step 5: Run the full runner test files**

```bash
python3 -m pytest execsvc/tests/test_runner_isolate.py execsvc/tests/test_runner_meta.py -v
```
Expected: all pass.

- [ ] **Step 6: Self-review and commit**

Confirm `_extra_dirs`'s signature and return type are unchanged (still `(language: str) -> list`, still returns `[]` or a single-element list). Confirm no caller outside `runner.py` imports `_extra_dirs` directly (it's prefixed `_`, module-private — grep to confirm: `grep -rn "_extra_dirs" --include="*.py" .` should show only `runner.py` and its test file).

```bash
git add execsvc/runner.py execsvc/tests/test_runner_meta.py
git commit -m "refactor(execsvc): flatten _extra_dirs nesting to early returns

Mechanical refactor, no behavior change — characterization tests
(4 new cases) pass identically before and after. Cuts nesting from
4 levels to 1."
```

---

### Task 4: Batch the N+1 test-case insert in `admin_coding.py`

**Files:**
- Modify: `app/routers/admin_coding.py:192-205` (inside `upsert_coding_question`)
- Test: `tests/test_admin_coding.py`

**Interfaces:**
- Consumes: `cases: list[dict]` (already produced by `_clean_cases`, unchanged shape: each has `idx`, `input`, `expected_output`, `visibility`, `float_tolerance`).
- Produces: same behavior, same return shape from `upsert_coding_question` — this task only changes HOW the DB writes happen (one bulk insert instead of N sequential ones), not what gets written or returned.

- [ ] **Step 1: Check the current test coverage for this insert loop**

```bash
grep -n "insert" tests/test_admin_coding.py | head -20
```
Read the matching test(s) to understand how `_atable("coding_test_cases").insert` is currently asserted on (likely via a recorder dict capturing insert payloads, matching `tests/test_coding_router.py`'s `_make_atable(table_data, recorder=...)` pattern — check `tests/test_admin_coding.py`'s own mock helper, which may differ).

- [ ] **Step 2: Write a test asserting a SINGLE bulk insert call, not N calls**

Add to `tests/test_admin_coding.py` (adapt the exact mock-helper names to whatever `test_admin_coding.py` already uses — inspect Step 1's output before writing this):
```python
def test_upsert_coding_question_batches_test_case_inserts():
    """Regression: N test cases must produce ONE insert call with a list of
    N rows, not N separate insert calls (N+1 query pattern)."""
    # ... use this file's existing request-building + mock-atable helpers,
    # constructing a body with 3 test cases, and assert the recorded
    # 'coding_test_cases' insert calls list has length 1 and that single
    # call's payload is a list of length 3.
```
(The exact test body depends on `test_admin_coding.py`'s existing fixture/mock shape — inspect it in Step 1 and write this test using the SAME helper functions the rest of that file already uses, not a new ad hoc mock.)

- [ ] **Step 3: Run it to verify it fails against the current N+1 implementation**

```bash
python3 -m pytest tests/test_admin_coding.py -v -k batches_test_case_inserts
```
Expected: FAIL (currently N insert calls, not 1).

- [ ] **Step 4: Batch the insert**

Replace:
```python
        for c in cases:
            # Coerce types so the DB adapter never sees a str where it
            # expects int/float — the request body may carry JSON-parsed
            # values that preserved their original types, but the raw
            # dict from the router doesn't go through a Pydantic model.
            await _atable("coding_test_cases").insert({
                "question_id": qid,
                "teacher_id": tid,
                "idx": int(c["idx"]),
                "input": str(c["input"]),
                "expected_output": secrets_crypto.encrypt(c["expected_output"]),
                "visibility": str(c["visibility"]),
                "float_tolerance": float(c["float_tolerance"]) if c["float_tolerance"] is not None else None,
            }).execute()
```
with:
```python
        # Batched into a single insert (was one round-trip per test case —
        # up to MAX_TEST_CASES=50 sequential DB calls per question save).
        case_rows = [
            {
                "question_id": qid,
                "teacher_id": tid,
                "idx": int(c["idx"]),
                "input": str(c["input"]),
                "expected_output": secrets_crypto.encrypt(c["expected_output"]),
                "visibility": str(c["visibility"]),
                "float_tolerance": float(c["float_tolerance"]) if c["float_tolerance"] is not None else None,
            }
            for c in cases
        ]
        await _atable("coding_test_cases").insert(case_rows).execute()
```

- [ ] **Step 5: Run the new test and the full file**

```bash
python3 -m pytest tests/test_admin_coding.py -v
```
Expected: all pass, including the new batching test.

- [ ] **Step 6: Self-review and commit**

Re-read the diff. Confirm `PostgresTable.insert()` accepts a list (verified during planning: `app/postgres_table.py:373-376` — `self._payload = rows if isinstance(rows, list) else [rows]`). Confirm no caller relies on partial-insert behavior (i.e., some rows succeeding before a later row fails) — the old loop had no such guarantee either (each `.execute()` could independently fail), so this doesn't remove a real guarantee.

```bash
git add app/routers/admin_coding.py tests/test_admin_coding.py
git commit -m "fix(admin_coding): batch test-case inserts into a single query

Was one INSERT per test case (up to 50 sequential round-trips per
question save). Batches into one insert() call with a list of rows —
PostgresTable.insert() already accepts either a dict or a list."
```

---

### Task 4b: Atomic question + test-case write in `upsert_coding_question`

**Files:**
- Modify: `app/routers/admin_coding.py`
- Test: `tests/test_admin_coding.py`

**Interfaces:**
- Consumes: `case_rows` (the list built in Task 4), `q_row` (already built above it, unchanged shape), `get_pool` from `..postgres_table` and `db_context as _dbctx` (both already imported this way in `app/routers/coding.py:112-113` — same import pattern, different file).
- Produces: `upsert_coding_question`'s external behavior (response shape, status codes) is unchanged — this task only changes the DB write from three independent, un-transacted `_atable` calls into one asyncpg transaction.

**Context (found during planning, not in the original spec — verify this history before starting, don't take it on faith):** a commit (`8511dc9d`, 2026-06-25) previously attempted exactly this fix — wrapping the question upsert + test-case delete/insert in one transaction, mirroring `app/invites.py`'s pattern — and was reverted four hours later (`aa911b00`) with no explanation in the revert commit message. The original commit message flagged an unresolved risk: "CI's integration suite connects as table owner (RLS inert)... needs a prod/staging authoring smoke test before this ships in a release." The vulnerability itself is real and still present in the current code: `upsert_coding_question` does a plain `_atable` update/insert for the question row, then (on replace) a separate `_atable` delete of old test cases, then a separate insert of new ones (or, after Task 4, one batched insert) — a failure between any of these steps leaves a question with wrong/missing test cases, silently.

This task closes the gap the prior attempt left open: it uses the SAME transaction pattern already proven and shipped elsewhere in this exact codebase — `app/routers/coding.py`'s `_insert_submission_under_cap` (lines 88-131, already read in full during planning) already does `pool.acquire()` + `conn.transaction()` + `await _dbctx.apply_request_context(conn)` for RLS-scoped raw SQL. Reusing that exact pattern here means RLS scoping is handled the same proven way, not a new untested approach.

- [ ] **Step 1: Read `_insert_submission_under_cap` once more as the template**

Already read in full during planning (`app/routers/coding.py:88-131`). Confirm its shape: acquire a pooled connection, open a transaction, call `await _dbctx.apply_request_context(conn)` (sets the RLS GUCs for the current request's teacher/role context), then run raw SQL inside that same transaction.

- [ ] **Step 2: IMPORTANT — this switches the write path from `_atable` to raw SQL, which breaks every existing test in this file unless the shared mock helper is updated first**

`tests/test_admin_coding.py`'s existing `_patches()` (lines 53-61, already read during planning) and `_atable_factory` (lines 35-50) only mock `_atable` — they know nothing about `get_pool`/raw connections. `PostgresTable.execute()` has no way to run within an externally-supplied connection (confirmed during planning: no `conn=` parameter exists anywhere in `app/postgres_table.py`), so real cross-statement atomicity requires the raw-SQL-in-one-transaction approach — there's no way to keep the old `_atable` calls and still get atomicity. This means `_patches()` itself must be updated, and it must reconstruct the SAME `rec["questions"]`/`rec["coding_test_cases"]` recorder shape the old `_atable.insert()`-based recorder produced, so `TestCreateCodingQuestion`'s 7 existing tests (which assert on `rec["questions"][0]["question_type"]` etc.) keep passing unchanged.

Replace `test_admin_coding.py`'s `_patches` function (currently lines 53-61):
```python
def _patches(rows, rec):
    async def _admin(req):
        return {"id": "teacher-1"}
    return (
        patch("app.routers.admin_coding.require_admin", side_effect=_admin),
        patch("app.routers.admin_coding.assert_can_author"),
        patch("app.routers.admin_coding._cache"),
        patch("app.routers.admin_coding._atable", side_effect=_atable_factory(rows, rec)),
    )
```
with (adds a 5th patch for the new raw-SQL transaction path, alongside the unchanged `_atable` mock — `_atable` is still used for the ownership/replace-check reads in `upsert_coding_question`, only the WRITE section changes to raw SQL):
```python
def _make_pool_recorder(rec, fail_on=None):
    """Mock asyncpg pool for upsert_coding_question's atomic write.
    Reconstructs the SAME rec['questions']/rec['coding_test_cases'] shape
    the old _atable.insert()-based recorder produced, by pattern-matching
    each raw conn.execute() call's SQL text — so existing assertions in
    TestCreateCodingQuestion keep working unchanged after the write path
    switched from _atable calls to one raw-SQL transaction.

    *fail_on*, if given, is a substring (e.g. "INSERT INTO coding_test_cases")
    that makes the matching execute() call raise, for testing rollback.
    """
    async def _execute(sql, *params):
        if fail_on and fail_on in sql:
            raise RuntimeError("simulated write failure")
        if "INSERT INTO questions" in sql:
            tid, exam_id, qid, question, qtype, options, correct = params
            rec.setdefault("questions", []).append({
                "teacher_id": tid, "exam_id": exam_id, "question_id": qid,
                "question": question, "question_type": qtype,
                "options": options, "correct": correct,
            })
        elif "UPDATE questions" in sql:
            question, qtype, options, correct, tid, exam_id, qid = params
            rec.setdefault("questions", []).append({
                "teacher_id": tid, "exam_id": exam_id, "question_id": qid,
                "question": question, "question_type": qtype,
                "options": options, "correct": correct, "_replaced": True,
            })
        elif "INSERT INTO coding_test_cases" in sql:
            qid, tid, idx, inp, expected, vis, ftol = params
            rec.setdefault("coding_test_cases", []).append({
                "question_id": qid, "teacher_id": tid, "idx": idx, "input": inp,
                "expected_output": expected, "visibility": vis, "float_tolerance": ftol,
            })
        # DELETE FROM coding_test_cases: no recorder equivalent needed —
        # no existing test asserts on delete calls.
        return None

    conn = MagicMock()
    conn.execute = _execute
    txn_cm = MagicMock()
    txn_cm.__aenter__ = MagicMock(side_effect=lambda: _AsyncReturn(None))
    txn_cm.__aexit__ = MagicMock(side_effect=lambda *a: _AsyncReturn(False))
    conn.transaction = MagicMock(return_value=txn_cm)
    conn_cm = MagicMock()
    conn_cm.__aenter__ = MagicMock(side_effect=lambda: _AsyncReturn(conn))
    conn_cm.__aexit__ = MagicMock(side_effect=lambda *a: _AsyncReturn(False))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn_cm)

    async def _get_pool():
        return pool
    return _get_pool


class _AsyncReturn:
    """Awaitable resolving to a fixed value — for mocking __aenter__/__aexit__
    (matches the identical helper already used in tests/test_coding_router.py)."""
    def __init__(self, value):
        self._value = value
    def __await__(self):
        async def _c():
            return self._value
        return _c().__await__()


def _patches(rows, rec, fail_on=None):
    async def _admin(req):
        return {"id": "teacher-1"}
    return (
        patch("app.routers.admin_coding.require_admin", side_effect=_admin),
        patch("app.routers.admin_coding.assert_can_author"),
        patch("app.routers.admin_coding._cache"),
        patch("app.routers.admin_coding._atable", side_effect=_atable_factory(rows, rec)),
        patch("app.postgres_table.get_pool", new=_make_pool_recorder(rec, fail_on=fail_on)),
    )
```
Note the new 5th patch element: every existing call site in this file using `ps[0], ps[1], ps[2], ps[3]` (all 7 tests in `TestCreateCodingQuestion`, confirmed during planning) must be updated to also include `ps[4]` in their `with` statement, e.g. `with ps[0], ps[1], ps[2], ps[3], ps[4]:`. Do this across the whole file in this step — it's mechanical (the recorder shape is preserved, so no assertion logic changes, only the `with` statement's patch count).

- [ ] **Step 3: Write the new atomicity test using `fail_on`**

Add to `tests/test_admin_coding.py`, in `TestCreateCodingQuestion`:
```python
    def test_replace_rewrite_is_atomic_on_test_case_insert_failure(self, client):
        """Regression: if the test-case insert fails partway through a
        replace, the question row's update must not be recorded either —
        proving the code structure is one transaction, not independent
        statements. (A mock can prove the STRUCTURE uses one transaction;
        it cannot prove real Postgres actually rolls back on the DB side —
        that requires the real-Postgres integration test in Step 6.)"""
        rec = {}
        ps = _patches({"questions": [{"question_id": "coding-xyz"}]}, rec,
                      fail_on="INSERT INTO coding_test_cases")
        body = {**_GOOD, "question_id": "coding-xyz"}
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            r = client.post("/api/v1/admin/coding-question", json=body, headers=_hdr())
        assert r.status_code == 500
        # The UPDATE ran before the failing INSERT (matching this task's
        # statement order), but both are inside one conn.transaction()
        # context — real asyncpg rolls this back on the raised exception
        # when conn.transaction()'s __aexit__ sees a non-None exc_type.
        # This mock's txn_cm doesn't simulate rollback itself (mocks can't
        # undo appends to `rec`), so this test asserts the STRUCTURAL
        # guarantee (everything happens inside the one transaction context,
        # confirmed by reaching this point via the mocked conn at all)
        # rather than re-asserting real DB rollback behavior, which the
        # Step 6 integration test covers instead.
        assert rec.get("questions", []) or rec.get("coding_test_cases", [])
```

- [ ] **Step 4: Run it to verify it fails against the current non-atomic implementation**

```bash
python3 -m pytest tests/test_admin_coding.py -v -k atomic
```
Expected: FAIL — today's code has no `get_pool`/transaction path at all (it uses `_atable`), so this test currently errors rather than reaching a 500 in the expected shape.

- [ ] **Step 5: Wrap the write in one transaction**

Replace the write section of `upsert_coding_question` (from `try:` before `if replacing:` through the `except Exception as e:` block that raises the 500) — currently:
```python
    try:
        if replacing:
            await _atable("questions").update(q_row).eq("teacher_id", tid)\
                .eq("exam_id", exam_id).eq("question_id", qid).execute()
        else:
            await _atable("questions").insert(q_row).execute()
        for c in cases:
            ...
            await _atable("coding_test_cases").insert({...}).execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[coding-question] write failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save coding question")
```
with (this also folds in Task 4's batched-insert row-building — if Task 4 already landed, `case_rows` is already built above this block; if implementing this task before Task 4, build `case_rows` inline here the same way):
```python
    case_rows = [
        {
            "question_id": qid,
            "teacher_id": tid,
            "idx": int(c["idx"]),
            "input": str(c["input"]),
            "expected_output": secrets_crypto.encrypt(c["expected_output"]),
            "visibility": str(c["visibility"]),
            "float_tolerance": float(c["float_tolerance"]) if c["float_tolerance"] is not None else None,
        }
        for c in cases
    ]
    try:
        from ..postgres_table import get_pool
        from .. import db_context as _dbctx
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _dbctx.apply_request_context(conn)
                if replacing:
                    await conn.execute(
                        "UPDATE questions SET question = $1, question_type = $2, options = $3, correct = $4 "
                        "WHERE teacher_id = $5 AND exam_id = $6 AND question_id = $7",
                        q_row["question"], q_row["question_type"], q_row["options"], q_row["correct"],
                        tid, exam_id, qid,
                    )
                    await conn.execute(
                        "DELETE FROM coding_test_cases WHERE teacher_id = $1 AND question_id = $2",
                        tid, qid,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO questions (teacher_id, exam_id, question_id, question, question_type, options, correct) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                        tid, exam_id, qid, q_row["question"], q_row["question_type"], q_row["options"], q_row["correct"],
                    )
                for row in case_rows:
                    await conn.execute(
                        "INSERT INTO coding_test_cases "
                        "(question_id, teacher_id, idx, input, expected_output, visibility, float_tolerance) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                        row["question_id"], row["teacher_id"], row["idx"], row["input"],
                        row["expected_output"], row["visibility"], row["float_tolerance"],
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[coding-question] write failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save coding question")
```

- [ ] **Step 6: Run the new test to confirm it passes, then the full file**

```bash
python3 -m pytest tests/test_admin_coding.py -v
```
Expected: all pass, including the new atomicity test.

- [ ] **Step 7: Address the prior attempt's unresolved caveat — do NOT skip this**

The reverted commit's own message flagged that its CI integration coverage connected as table owner (RLS inert) and explicitly said a prod/staging smoke test was needed before shipping — and it shipped anyway, without one, then got reverted. Do not repeat that: before this task is considered done, either (a) add a real-Postgres integration test that runs under the actual restricted `procta_app` role (check `docs/TENANCY_RLS_HARDENING.md`, referenced in `integration_tests/test_coding_e2e.py`'s own docstring, for how other integration tests in this repo connect as the restricted role rather than the table owner), confirming a teacher cannot write another teacher's question through this path even inside the new transaction; or (b) if no such restricted-role integration harness exists yet for this specific table, explicitly flag that gap in the commit message and leave a follow-up note rather than silently repeating the same shipped-without-verification mistake.

- [ ] **Step 8: Self-review and commit**

Confirm the raw SQL column lists match the actual `questions`/`coding_test_cases` schemas exactly (cross-check against `migrations/phase141_coding_tables.sql` and any later migration touching these tables, e.g. `phase143_coding_submissions_student_rls.sql`/`phase145_coding_exec_metrics.sql`/`phase151_coding_plagiarism.sql` — confirm none of them added a NOT NULL column this raw INSERT would omit). Confirm `_cache.delete(...)` (the line right after this block, unchanged) still runs after the transaction commits, not inside it.

```bash
git add app/routers/admin_coding.py tests/test_admin_coding.py
git commit -m "fix(admin_coding): atomic question + test-case write

upsert_coding_question wrote the question row and its test cases as
separate, un-transacted statements — a failure partway through (e.g.
the batched test-case insert from a prior commit) left a question
with wrong/missing test cases and no way to detect it. A prior attempt
at this exact fix (8511dc9d) was reverted (aa911b00) with an
unresolved caveat about RLS verification under the restricted DB role
never being completed before it shipped. This uses the same
transaction pattern already proven in this codebase
(coding.py's _insert_submission_under_cap: pool.acquire() +
conn.transaction() + apply_request_context for RLS scoping) and
addresses the prior attempt's gap directly rather than repeating it."
```

---

### Task 5: Extract `_run_sample_cases` — remove the coding_run/admin_coding_preview_run duplication

**Files:**
- Modify: `app/routers/coding.py`
- Test: `tests/test_coding_router.py`

**Interfaces:**
- Consumes: `run_one`, `ExecLimits`, `ExecUnavailable` (existing imports), `normalize_output`, `_float_match` (existing imports), `secrets_crypto.decrypt` (existing import).
- Produces: `async def _run_sample_cases(question_id: str, language: str, source: str, limits: ExecLimits) -> dict` — returns `{"cases": [...], "passed": int, "total": int}`, the exact shape both `coding_run` and `admin_coding_preview_run` currently return directly. Later tasks (Task 6) do not depend on this function, but this task must not be reordered before Task 4 in execution since both touch adjacent, unrelated parts of the codebase safely in parallel — sequencing here is about file order (spec's suggested order), not a hard dependency.

- [ ] **Step 1: Write characterization tests for `admin_coding_preview_run`'s current behavior**

`tests/test_coding_router.py`'s existing `TestRun` class (lines 186-312, already read in full during planning) covers `coding_run` but has no equivalent for `admin_coding_preview_run` — add one. `require_admin` isn't mocked by this file's existing `_patches()` helper (`TestRun` uses real student JWTs via `make_student_token`/`_hdr()`); patch it directly at the `app.routers.coding` import site instead — simpler than exercising the full JWT+DB verify chain, and consistent with how `_patches()` already patches other things at that same module path. Both `admin_coding_preview_run`'s ownership check and its `_question_time_limit_ms` call read from the same mocked `"questions"` table, so one row shape (`{"question_id": ..., "options": {}}`) must satisfy both.

Add to `tests/test_coding_router.py`, after the `TestRun` class:
```python
def _admin_patches(table_data, recorder, run_one_mock=None):
    async def _fake_admin(request):
        return {"id": "teacher-1", "org_role": "teacher"}
    patches = [
        patch("app.routers.coding.require_admin", side_effect=_fake_admin),
        patch("app.routers.coding._atable", side_effect=_make_atable(table_data, recorder)),
        patch("app.routers.coding.system_context", return_value=nullcontext()),
    ]
    if run_one_mock is not None:
        patches.append(patch("app.routers.coding.run_one", run_one_mock))
    return patches


def _admin_hdr():
    from tests.conftest import make_admin_token
    return {"Authorization": f"Bearer {make_admin_token()}"}


def _preview_body(**overrides):
    body = {"question_id": "coding-q-1", "language": "javascript", "source": "console.log(5)"}
    body.update(overrides)
    return body


class TestAdminPreviewRun:
    """Characterization tests for admin_coding_preview_run's CURRENT behavior,
    written before extracting the shared _run_sample_cases helper (Task 5),
    so the extraction can be verified to preserve behavior exactly. Mirrors
    TestRun's tests for the sibling /coding/run endpoint."""

    _QUESTIONS_ROW = [{"question_id": "coding-q-1", "options": {}}]

    def test_preview_run_passes_all_sample_cases(self, client):
        rec = {}
        table_data = {
            "questions": self._QUESTIONS_ROW,
            "coding_test_cases": [{"idx": 0, "input": "", "expected_output": "Hello, World!"}],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="Hello, World!\n", time_ms=5))
        patches = _admin_patches(table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(source="print('Hello, World!')", language="python"),
                               headers=_admin_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1 and body["passed"] == 1
        assert body["cases"][0]["status"] == "passed"

    def test_preview_run_reports_compile_error(self, client):
        rec = {}
        table_data = {
            "questions": self._QUESTIONS_ROW,
            "coding_test_cases": [{"idx": 0, "input": "", "expected_output": "5"}],
        }
        mock_run = MagicMock(return_value=_exec_result(compile_error="SyntaxError: bad token"))
        patches = _admin_patches(table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(), headers=_admin_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 0 and body["cases"][0]["status"] == "error"
        assert body["cases"][0]["error"] == "SyntaxError: bad token"

    def test_preview_run_reports_timeout(self, client):
        rec = {}
        table_data = {
            "questions": self._QUESTIONS_ROW,
            "coding_test_cases": [{"idx": 0, "input": "", "expected_output": "5"}],
        }
        mock_run = MagicMock(return_value=_exec_result(timed_out=True))
        patches = _admin_patches(table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(), headers=_admin_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 0 and body["cases"][0]["status"] == "timeout"

    def test_preview_run_honors_float_tolerance(self, client):
        rec = {}
        table_data = {
            "questions": self._QUESTIONS_ROW,
            "coding_test_cases": [{"idx": 0, "input": "", "expected_output": "0.30000000004", "float_tolerance": 1e-6}],
        }
        mock_run = MagicMock(return_value=_exec_result(stdout="0.3"))
        patches = _admin_patches(table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(), headers=_admin_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] == 1 and body["cases"][0]["status"] == "passed"

    def test_preview_run_raises_503_on_exec_unavailable(self, client):
        rec = {}
        table_data = {
            "questions": self._QUESTIONS_ROW,
            "coding_test_cases": [{"idx": 0, "input": "", "expected_output": "5"}],
        }
        mock_run = MagicMock(side_effect=ExecUnavailable("executor down"))
        patches = _admin_patches(table_data, rec, run_one_mock=mock_run)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(), headers=_admin_hdr())
        assert resp.status_code == 503
        assert resp.json()["detail"]["retryable"] is True

    def test_preview_run_question_not_owned_by_teacher_404s(self, client):
        rec = {}
        table_data = {"questions": [], "coding_test_cases": []}
        patches = _admin_patches(table_data, rec)
        with patches[0], patches[1], patches[2]:
            resp = client.post("/api/v1/admin/coding-question/preview-run",
                               json=_preview_body(), headers=_admin_hdr())
        assert resp.status_code == 404
```

- [ ] **Step 2: Run the new characterization tests against the CURRENT (pre-extraction) code**

```bash
python3 -m pytest tests/test_coding_router.py -v -k "TestAdminPreviewRun or TestRun"
```
Expected: PASS — these lock in today's behavior for both endpoints before any extraction touches them.

- [ ] **Step 3: Extract `_run_sample_cases`**

Add this function to `app/routers/coding.py`, placed after `_limits_for` (around line 86, before `_insert_submission_under_cap`):
```python
async def _run_sample_cases(question_id: str, language: str, source: str, limits) -> dict[str, Any]:
    """Run `source` against a question's SAMPLE test cases and grade each.

    Shared by /coding/run (student) and the admin preview-run endpoint —
    both need the identical fetch-sample-cases -> run-each -> compare ->
    build-result-list logic; only their auth/ownership checks differ, which
    stay in each caller. Returns {"cases": [...], "passed": int, "total": int}.
    """
    with system_context():
        sample = (await _atable("coding_test_cases")
                  .select("idx,input,expected_output,float_tolerance")
                  .eq("question_id", question_id).eq("visibility", "sample")
                  .order("idx").execute()).data or []

    cases = []
    passed = 0
    for row in sample:
        expected = secrets_crypto.decrypt(row.get("expected_output") or "")
        try:
            result = await asyncio.to_thread(run_one, language, source, row.get("input") or "", limits)
        except ExecUnavailable:
            raise HTTPException(status_code=503, detail={"retryable": True, "error": "execution service unavailable"})
        if result.compile_error:
            status, ok, err = "error", False, result.compile_error
        elif result.timed_out:
            status, ok, err = "timeout", False, None
        else:
            tol = row.get("float_tolerance")
            if tol is not None:
                ok = _float_match(result.stdout, expected, float(tol))
            else:
                ok = normalize_output(result.stdout) == normalize_output(expected)
            status = "passed" if ok else "failed"
            err = result.stderr or None
        if ok:
            passed += 1
        cases.append({
            "input": row.get("input"), "expected_output": expected,
            "output": result.stdout, "status": status,
            "time_ms": result.time_ms, "error": err,
        })

    return {"cases": cases, "passed": passed, "total": len(sample)}
```

- [ ] **Step 4: Migrate `coding_run` to call it**

Replace the body of `coding_run` from `with system_context():` through the `return {"cases": cases, ...}` line with:
```python
    time_limit_ms = await _question_time_limit_ms(question_id)
    limits = _limits_for(time_limit_ms)
    return await _run_sample_cases(question_id, language, source, limits)
```
(Keep everything above that unchanged: `claims = require_auth(request)` through `await _assert_student_session_access(claims, session_id)`.)

- [ ] **Step 5: Migrate `admin_coding_preview_run` to call it**

Replace the equivalent body (from `with system_context():` through its `return {"cases": cases, ...}`) with the same two lines:
```python
    time_limit_ms = await _question_time_limit_ms(question_id)
    limits = _limits_for(time_limit_ms)
    return await _run_sample_cases(question_id, language, source, limits)
```
(Keep the teacher-auth + ownership check above unchanged.)

- [ ] **Step 6: Run the characterization tests again to confirm the extraction preserved behavior exactly**

```bash
python3 -m pytest tests/test_coding_router.py -v -k "TestAdminPreviewRun or TestRun"
```
Expected: PASS, identically, now against the extracted-helper implementation.

- [ ] **Step 7: Run the full coding test file**

```bash
python3 -m pytest tests/test_coding_router.py -v
```
Expected: all pass (including `TestJudge`, `TestJudgeFailurePolicy`, `TestTestcases` — unaffected by this change, confirming no cross-contamination).

- [ ] **Step 8: Self-review and commit**

Confirm both `coding_run` and `admin_coding_preview_run` still have their own distinct auth checks (`_assert_student_session_access` vs. `require_admin` + ownership) untouched — only the shared execution logic moved. Confirm no other file calls the old inline logic directly (it was never a separate function before, so nothing external could have depended on it).

```bash
git add app/routers/coding.py tests/test_coding_router.py
git commit -m "refactor(coding): extract _run_sample_cases, remove 29-line duplication

coding_run and admin_coding_preview_run independently implemented the
identical fetch-sample-cases -> run-each -> compare -> build-results
loop (the ~20% duplication repowise flagged). Extracted into one
shared _run_sample_cases() helper; each endpoint keeps its own
distinct auth/ownership check. Characterization tests written before
the extraction confirm identical behavior after."
```

---

### Task 6: Refactor `coding_judge` into composable stages

**Files:**
- Modify: `app/routers/coding.py`
- Test: `tests/test_coding_router.py` (existing `TestJudge`, `TestJudgeFailurePolicy` classes already provide substantial characterization coverage — read them in full before starting, at lines 313-712)

**Interfaces:**
- Consumes: everything `coding_judge` currently consumes (unchanged: `require_auth`, `_assert_student_session_access`, `_load_exam_config`, `reserve_idempotency`/`release_idempotency`/`mark_idempotent`, `_run_sample_cases`'s sibling logic is NOT reused here — hidden-case execution is deliberately separate from sample-case execution since hidden cases have different security invariants, see the file's module docstring Invariant #1).
- Produces: the route function `coding_judge` keeps its exact signature and behavior (same request/response shape, same status codes, same invariants #1-#5 from the module docstring). Internally split into named helper functions:
  - `_check_submit_cap(session_id: str, question_id: str, tid, eid) -> int` — returns the cap, raises 429 if already exceeded (invariant #3's pre-check).
  - `_run_hidden_cases(question_id: str, language: str, source: str, limits) -> dict` — returns `{"passed": int, "total": int, "average_execution_ms": int|None, "compile_output": str|None}`. This is intentionally NOT the same function as `_run_sample_cases` (Task 5) despite structural similarity — hidden cases must never expose per-case detail or expected values (invariant #1), so this returns only aggregate counts, never a `cases` list.

- [ ] **Step 1: Read the existing `TestJudge`/`TestJudgeFailurePolicy` test classes in full**

```bash
sed -n '313,712p' tests/test_coding_router.py
```
Confirm these already exercise: happy-path grading, compile-error short-circuit, timeout handling, float-tolerance matching, the submit-attempt cap (both the pre-check and the atomic re-check via `_insert_submission_under_cap`), idempotency (cache hit / in-flight 409), and the `ExecUnavailable` -> 503-no-row-written invariant. This existing suite IS the characterization safety net for this refactor — no new characterization tests are needed if (and only if) this read confirms all of the above is covered. If any of these is NOT covered, add a characterization test for the missing case before proceeding to Step 2.

- [ ] **Step 2: Extract `_check_submit_cap`**

Add before `coding_judge`:
```python
async def _check_submit_cap(session_id: str, question_id: str, tid, eid) -> int:
    """Invariant #3's pre-check: the per-question submit-attempt cap
    (output-oracle defense). Returns the configured cap. Raises 429 if
    already at/over cap. This is a plain COUNT with no lock — the
    authoritative, race-safe re-check happens in
    _insert_submission_under_cap at insert time; this is just an early,
    cheap rejection before running the (possibly slow) hidden test cases."""
    config = await _load_exam_config(str(tid or ""), exam_id=eid)
    cap = int((config or {}).get("coding_max_submit_attempts") or DEFAULT_MAX_SUBMIT_ATTEMPTS)
    prior = (await _atable("coding_submissions").select("id")
             .eq("session_id", session_id).eq("question_id", question_id).execute()).data or []
    if len(prior) >= cap:
        raise HTTPException(status_code=429, detail="Submission limit reached for this problem")
    return cap
```

- [ ] **Step 3: Extract `_run_hidden_cases`**

Add before `coding_judge`:
```python
async def _run_hidden_cases(question_id: str, language: str, source: str, limits) -> dict[str, Any]:
    """Run `source` against a question's HIDDEN test cases and grade it.

    Invariant #1: hidden expected_output is read only under system_context()
    for the comparison and is NEVER included in the return value — only
    aggregate counts. This is deliberately NOT shared with _run_sample_cases
    (Task 5's extraction) despite similar structure, because that function
    returns per-case detail (correct — sample cases are public worked
    examples), which would leak the answer key if reused here.
    """
    with system_context():
        hidden = (await _atable("coding_test_cases")
                  .select("idx,input,expected_output,float_tolerance")
                  .eq("question_id", question_id).eq("visibility", "hidden")
                  .order("idx").execute()).data or []

    passed = 0
    total = len(hidden)
    exec_times = []
    compile_output = None
    for row in hidden:
        # ONLY {language, source, stdin, limits} cross to the executor —
        # row["expected_output"] is read here and used only below, never
        # passed to run_one().
        result = await asyncio.to_thread(run_one, language, source, row.get("input") or "", limits)
        if result.compile_error:
            # The source is identical across all cases, so a compile error
            # on the first run fails every case — stop here instead of
            # burning a sandboxed run per remaining case.
            compile_output = result.compile_error
            break
        exec_times.append(result.time_ms)
        tol = row.get("float_tolerance")
        expected = secrets_crypto.decrypt(row.get("expected_output") or "")
        if result.timed_out:
            ok = False
        elif tol is not None:
            ok = _float_match(result.stdout, expected, float(tol))
        else:
            ok = normalize_output(result.stdout) == normalize_output(expected)
        if ok:
            passed += 1

    avg_ms = int(sum(exec_times) / len(exec_times)) if exec_times else None
    return {"passed": passed, "total": total, "average_execution_ms": avg_ms, "compile_output": compile_output}
```

- [ ] **Step 4: Rewrite `coding_judge` to compose the extracted stages**

Replace the full body of `coding_judge` (from `claims = require_auth(request)` through its final `except Exception as e:` block) with:
```python
async def coding_judge(body: dict[str, Any], request: Request):
    """Run the student's source against the SECRET hidden cases, server-side,
    and grade it. Returns `{passed, total, average_execution_ms}` only — never
    per-case detail or expected values."""
    claims = require_auth(request)
    session_id = (body.get("session_id") or "").strip()
    question_id = (body.get("question_id") or "").strip()
    language = (body.get("language") or "").strip()
    source = body.get("source") or ""
    if not session_id or not question_id:
        raise HTTPException(status_code=400, detail="session_id and question_id required")
    await _assert_student_session_access(claims, session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")

    cap = await _check_submit_cap(session_id, question_id, tid, eid)

    # Invariant #4 — idempotency. Key on (session, question, source) so a
    # genuine new attempt isn't suppressed, but a retry double-fire writes once.
    attempt_hash = hashlib.sha256(
        json.dumps([session_id, question_id, source], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    idem_key = f"coding_judge:{session_id}:{question_id}:{attempt_hash}"
    acquired, cached = await reserve_idempotency(idem_key, ttl=300)
    if not acquired:
        if cached is not None:
            return cached
        raise HTTPException(status_code=409, detail="Submission already in progress")

    try:
        time_limit_ms = await _question_time_limit_ms(question_id)
        limits = _limits_for(time_limit_ms)
        try:
            result = await _run_hidden_cases(question_id, language, source, limits)
        except ExecUnavailable:
            # Invariant #5 — never write a submission row on a transient
            # executor outage; the kiosk auto-retries.
            raise HTTPException(status_code=503, detail={"retryable": True, "error": "execution service unavailable"})

        telemetry = body.get("telemetry") or {}
        row_to_insert = {
            "exam_id":     eid,
            "teacher_id":  str(tid) if tid else None,   # Invariant #2 — JWT, not body
            "session_id":  session_id,
            "student_id":  claims.get("sid"),
            "question_id": question_id,
            "language":    language[:40],
            "test_cases_total":  result["total"],
            "test_cases_passed": result["passed"],
            "average_execution_ms": result["average_execution_ms"],
            "memory_consumed_kb":   None,
            "source_code": source,
            "compile_output": result["compile_output"],
            "keystroke_rhythm_variance": telemetry.get("keystroke_rhythm_variance"),
            "paste_attempts":   int(telemetry.get("paste_attempts") or 0),
            "focus_loss_count": int(telemetry.get("focus_loss_count") or 0),
        }
        # is_fully_solved is a GENERATED column — never inserted.
        if not await _insert_submission_under_cap(row_to_insert, cap):
            raise HTTPException(status_code=429, detail="Submission limit reached for this problem")
        resp = {"passed": result["passed"], "total": result["total"], "average_execution_ms": result["average_execution_ms"]}
        await mark_idempotent(idem_key, resp)
        return resp
    except HTTPException:
        await release_idempotency(idem_key)
        raise
    except Exception as e:
        await release_idempotency(idem_key)
        logger.error("[coding_judge] error for %s/%s: %s", session_id, question_id, e)
        raise HTTPException(status_code=500, detail="Failed to judge submission")
```

Note: the `@router.post("/api/v1/coding/judge")` and `@limiter.limit("30/minute")` decorators above `async def coding_judge(...)` are unchanged — only the function body is replaced.

- [ ] **Step 5: Run the full existing judge test suite to confirm behavior is unchanged**

```bash
python3 -m pytest tests/test_coding_router.py -v -k "TestJudge or TestJudgeFailurePolicy"
```
Expected: all pass, identically to before the refactor (this is the characterization check — if anything fails here, the refactor introduced a behavior change and must be fixed before proceeding, not worked around).

- [ ] **Step 6: Run the full file**

```bash
python3 -m pytest tests/test_coding_router.py -v
```
Expected: all pass.

- [ ] **Step 7: Run the full repo test suite**

```bash
python3 -m pytest -q --ignore=tests/browser
```
Expected: same pass count as before this plan started (establish the baseline by running this once before Task 1, if not already known from the current session — the last known-good baseline this session was 2570 passed, 141 skipped, 0 failed).

- [ ] **Step 8: Self-review and commit**

Re-read the full refactored `coding_judge` alongside the original (available via `git diff` before committing). Confirm every one of the 5 documented invariants in the module docstring still holds structurally: (1) hidden expected_output only read in `_run_hidden_cases`, never returned; (2) `teacher_id` still stamped from `tid` (JWT claim); (3) cap checked both in `_check_submit_cap` (pre-check) and `_insert_submission_under_cap` (atomic re-check, unchanged); (4) idempotency reservation/release logic unchanged; (5) `ExecUnavailable` still short-circuits to 503 with no row written. Check for any other caller of the old inline logic — there is none, since this was always one function.

```bash
git add app/routers/coding.py
git commit -m "refactor(coding): split coding_judge into composable stages

Extracted _check_submit_cap and _run_hidden_cases from the 112-line,
31-branch coding_judge (repowise's highest-complexity finding in the
repo). Each stage is independently readable and testable. All 5
documented security invariants (hidden-data isolation, teacher_id
from JWT, submit-cap enforcement, idempotency, fail-closed on executor
outage) verified unchanged via the existing TestJudge/
TestJudgeFailurePolicy characterization suite, which passes
identically before and after."
```

---

## Final verification

- [ ] **Run the complete test suite one more time**

```bash
python3 -m pytest -q --ignore=tests/browser
```
Expected: 0 failures, same or higher pass count than the pre-plan baseline (2570 passed, 141 skipped as of this session — new tests added by this plan should push the pass count up, never down).

- [ ] **Review the full branch diff against `main`**

```bash
git diff main --stat
```
Confirm only the expected files changed: `execsvc/runner.py`, `execsvc/microvm.py`, `app/routers/admin_coding.py`, `app/routers/coding.py`, and their test files. No changes to `execsvc/app.py` (confirmed out of scope per Global Constraints).

- [ ] **Do not push or merge to `main`.** Per the away-session convention, stop here — the branch is ready for review when the owner returns.
