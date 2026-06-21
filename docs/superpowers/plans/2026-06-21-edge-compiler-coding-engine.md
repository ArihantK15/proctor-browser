# Edge Compiler — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a client-side coding-assessment engine as a new `coding` question_type on Procta's existing exam/proctoring/RLS/scoring rails — student code executes in-kiosk (WASM/JS), the server judges outputs against secret expected outputs.

**Architecture:** Approach A (client executes, server judges). Phase 1 proves the whole spine in **JavaScript only** (zero runtime-download friction); later phases add Python/C++/Java adapters onto the proven spine. Server never executes student code — it normalizes + compares outputs it holds the secret answers for.

**Tech Stack:** FastAPI + asyncpg + Postgres (phase-numbered migrations, phase124 `app.*` RLS), Electron renderer (`renderer/index.html`) + **CodeMirror 6** (lighter than Monaco — chosen for the low-end-laptop concurrency budget + assessment-integrity; see "Editor decision") + Web Workers (**same-origin flat files**, not Blob — CSP, see P1-T6), WASM runtimes (Pyodide / Clang-WASM / DoppioJVM — later phases, same worker-file pattern).

**Spec:** `docs/superpowers/specs/2026-06-21-edge-compiler-coding-engine-design.md` (read it first — this plan implements it).

---

## Ownership & parallelization (standby: other-Claude + DeepSeek)

Three lanes. **Phase 1 Task 1 (schema) and Task 2 (judge comparison contract) are the
gate — they MUST land first** because every other task depends on the table columns
and the `judge_outputs()` signature. After that, the server lane and client lane run
in parallel.

| Lane | Owner | Tasks |
|------|-------|-------|
| **Server / SQL / Python** (well-specified, mechanical) | **DeepSeek** → I review as senior eng before merge | P1-T1 (schema+RLS), P1-T2 (judge compare), P1-T3 (judge router), P1-T4 (test-case delivery), P1-T5 (scoring branch), P1-T8 (authoring seed), P1-T9 (integration test) |
| **Client / JS / WASM / renderer** (needs judgment) | **other-Claude** → I review | P1-T6 (coding-runtime.js + JS adapter + sandbox), P1-T7 (Monaco mount + Run/Submit) |
| **Spikes, review gate, integration glue, security invariants** | **me** | P0 spikes sign-off, the RLS/expected-output review gates, wiring P1-T6↔T7↔T3 |

**Hard rule for delegated work:** the security invariants are non-negotiable and I
review them line-by-line before merge: (1) hidden `expected_output` never in a
client-reachable SELECT; (2) `teacher_id` stamped from JWT, never client body;
(3) submit-attempt cap enforced server-side; (4) new tables pass
`test_rls_policy_model_guard.py`. A task that touches these is **not** "done" until I
sign off the exact SELECT/policy.

**Do not touch** `tests/test_teacher_transfer.py` (another session's WIP) — the
offboarding-categorization entry for `coding_submissions` is sequenced with that
session, tracked in P1-T1 as a follow-up note, not edited here.

---

## File structure

**Created:**
- `migrations/phase141_coding_tables.sql` — `coding_test_cases` + `coding_submissions` + RLS.
- `app/services/coding_judge.py` — pure `judge_outputs()` comparison (normalization, float tolerance, counts).
- `app/routers/coding.py` — `POST /api/v1/coding/judge`, `GET /api/v1/coding/testcases`.
- `renderer/coding-runtime.js` — `runTestCases()` API (imported by the kiosk renderer).
- `renderer/coding-worker.js` — the worker body, a flat **same-origin** file (CSP-clean; not a Blob). All assets that workers load stay flat in `renderer/` (no `vendor/` subdir — the kiosk protocol clamps to one path component).
- `tests/test_coding_judge.py`, `tests/test_coding_router.py`, `tests/test_coding_scoring.py`.
- `integration_tests/test_coding_e2e.py` — submit→judge→score→RLS (real PG).
- `scripts/seed_coding_question.py` — minimal authoring (seed one question + cases).

**Modified:**
- `schema/columns.json` + `integration_tests/schema.sql` — the two new tables (schema-ref guard).
- `app/services/scoring.py` — coding-scoring branch (~L142 region).
- `app/repositories/questions.py` — accept `coding` in `question_type` handling (already defaults unknown→mcq_single; coding must pass through).
- `renderer/index.html` — add `'coding'` to the allowlist (L2468) + `renderQ()` coding branch + Run/Submit + load `coding-runtime.js`.
- `app/models/exam.py` — exam-config fields `coding_paste_policy`, `coding_max_submit_attempts` (if surfaced via create/update).

---

## Phase 0 — Spikes (go/no-go gates, BEFORE C++/Java) — owner: me

Not TDD; these are timeboxed measurements. **Phase 1 (JS) does NOT depend on Phase 0** — start them in parallel.

- [ ] **P0-T1: Clang-WASM C++ under concurrent proctoring.** On a representative low-end laptop (4-core/4-8GB), run `proctor.py` live (webcam + calibration) AND compile+run a medium C++ DS&A program in a Web Worker. **Gate:** proctor fps stays ≥ its throttled floor (3 fps), compile+run finishes < per-test time limit, no OOM. Record cold-start size + peak RAM. Fail → fallback note (smaller Clang build / defer C++).
- [ ] **P0-T2: DoppioJVM Java, same concurrent condition.** Gate identical. Fail → evaluate CheerpJ (license) or local-OpenJDK provisioning. Java is the most likely to fail the gate — decide go/no-go here.
- [ ] **P0-T3: Per-language installer-size budget.** From T1/T2 + Monaco (~5 MB) + Pyodide (~10 MB+), produce a concrete added-MB number per enabled language and a bundle-vs-download decision per the spec's Performance section. **Output:** a one-page budget appended to the spec.

---

## Phase 1 — JavaScript vertical slice (the spine)

### Task P1-T1: Schema + RLS (the gate — land first)

**Files:**
- Create: `migrations/phase141_coding_tables.sql`
- Modify: `schema/columns.json`, `integration_tests/schema.sql`

- [ ] **Step 1: Write the migration** (`migrations/phase141_coding_tables.sql`):

```sql
-- phase141: Edge Compiler coding-assessment tables.
-- coding_test_cases  — server-authoritative test cases. expected_output for HIDDEN
--                      cases is NEVER serialized to the student (query-layer
--                      invariant; students get NO RLS select — delivery is a
--                      system-context read that projects expected_output out).
-- coding_submissions — per-(student,question) judged result + telemetry. teacher_id
--                      is a REAL column, stamped server-side from the JWT, so RLS is
--                      a direct app.teacher_id() check and the offboarding guard can
--                      categorize it. See spec §"Anti-cheat" + §"Compliance".
DO $$
BEGIN
  CREATE TABLE IF NOT EXISTS coding_test_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id     TEXT NOT NULL,
    teacher_id      UUID,
    idx             INTEGER NOT NULL,
    input           TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    visibility      TEXT NOT NULL DEFAULT 'hidden',   -- 'sample' | 'hidden'
    float_tolerance DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_test_cases_q ON coding_test_cases(question_id, idx);

  CREATE TABLE IF NOT EXISTS coding_submissions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id                  TEXT,
    teacher_id               UUID,
    session_id               TEXT,
    student_id               UUID,
    question_id              TEXT,
    language                 TEXT,
    test_cases_total         INTEGER,
    test_cases_passed        INTEGER,
    is_fully_solved          BOOLEAN GENERATED ALWAYS AS
                               (test_cases_total > 0 AND test_cases_passed = test_cases_total) STORED,
    average_execution_ms     INTEGER,
    memory_consumed_kb       INTEGER,
    source_code              TEXT,
    keystroke_rhythm_variance DOUBLE PRECISION,
    paste_attempts           INTEGER DEFAULT 0,
    focus_loss_count         INTEGER DEFAULT 0,
    submitted_at             TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_submissions_exam_student
    ON coding_submissions(exam_id, student_id);
  CREATE INDEX IF NOT EXISTS coding_submissions_attempts
    ON coding_submissions(session_id, question_id);   -- submit-cap count query
EXCEPTION WHEN duplicate_table THEN
  RAISE NOTICE 'phase141 skip: tables exist';
END $$;

-- RLS — phase124 app.* model EXACTLY (mirrors phase137). Inert until cutover.
DO $$
BEGIN
  -- coding_submissions: direct teacher_id scoping.
  PERFORM app._drop_all_policies('coding_submissions'::regclass);
  ALTER TABLE coding_submissions ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_submissions_sel ON coding_submissions FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_submissions_ins ON coding_submissions FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_submissions_upd ON coding_submissions FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_submissions_del ON coding_submissions FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());

  -- coding_test_cases: teacher_id scoping for authoring; students get NO policy
  -- (deny-all under cutover) — delivery is a system_context() read. is_privileged()
  -- covers the system/superadmin read.
  PERFORM app._drop_all_policies('coding_test_cases'::regclass);
  ALTER TABLE coding_test_cases ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_test_cases_sel ON coding_test_cases FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_test_cases_ins ON coding_test_cases FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_test_cases_upd ON coding_test_cases FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_test_cases_del ON coding_test_cases FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
EXCEPTION WHEN undefined_function OR undefined_table THEN
  RAISE NOTICE 'phase141 RLS skip (app.* helpers not present yet): %', SQLERRM;
END $$;
```

- [ ] **Step 2: Mirror both tables into `integration_tests/schema.sql`** (NO RLS there — it's a plain fixture). Use a plain `BOOLEAN` for `is_fully_solved` (the fixture doesn't need the GENERATED expression). Add both `CREATE TABLE IF NOT EXISTS` blocks.
- [ ] **Step 3: Add every column of both tables to `schema/columns.json`** (alphabetical within each table key), or `scripts/check_schema_refs.py` fails CI.
- [ ] **Step 4: Run the schema guard** — `python3 scripts/check_schema_refs.py` → expect "all column references exist".
- [ ] **Step 5: Commit** `git commit -m "feat(coding): phase141 tables + RLS (test_cases + submissions)"`.
- [ ] **Follow-up note (NOT this commit):** `coding_submissions.teacher_id` must get a MOVE/KEEP entry in `test_all_teacher_id_tables_are_categorized` (`tests/test_teacher_transfer.py`) — sequence with that session's owner.

### Task P1-T2: Judge comparison (pure function)

**Files:** Create `app/services/coding_judge.py`, `tests/test_coding_judge.py`

- [ ] **Step 1: Write the failing test** (`tests/test_coding_judge.py`):

```python
from app.services.coding_judge import judge_outputs, normalize_output

def test_trailing_whitespace_and_newlines_ignored():
    r = judge_outputs(actual=["5 \n", "10\n\n"], expected=["5", "10"], tolerances=[None, None])
    assert r["passed"] == 2 and r["total"] == 2

def test_float_tolerance():
    r = judge_outputs(actual=["0.30000000004"], expected=["0.3"], tolerances=[1e-6])
    assert r["passed"] == 1

def test_float_outside_tolerance_fails():
    r = judge_outputs(actual=["0.5"], expected=["0.3"], tolerances=[1e-6])
    assert r["passed"] == 0

def test_mismatch_counts_per_case():
    r = judge_outputs(actual=["1", "WRONG", "3"], expected=["1", "2", "3"], tolerances=[None]*3)
    assert r["passed"] == 2 and r["total"] == 3 and r["per_case"] == [True, False, True]

def test_length_mismatch_is_safe():
    # fewer actual than expected → missing ones fail, never throws
    r = judge_outputs(actual=["1"], expected=["1", "2"], tolerances=[None, None])
    assert r["passed"] == 1 and r["total"] == 2
```

- [ ] **Step 2: Run it, verify it fails** (`pytest tests/test_coding_judge.py -q` → ImportError).
- [ ] **Step 3: Implement** (`app/services/coding_judge.py`):

```python
"""Pure output-judging for coding submissions (spec Approach A). No code execution
here — the client ran the code; we only compare its outputs to the secret expected
outputs the server holds. Normalization is deliberately conservative."""

def normalize_output(s: str) -> str:
    # Trim trailing whitespace per line + trailing blank lines. Do NOT touch interior
    # spacing (could be significant for some problems); authors print canonical output.
    lines = (s or "").replace("\r\n", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)

def _float_match(a: str, e: str, tol: float) -> bool:
    try:
        af = [float(x) for x in a.split()]
        ef = [float(x) for x in e.split()]
    except (ValueError, AttributeError):
        return False
    if len(af) != len(ef):
        return False
    return all(abs(x - y) <= tol for x, y in zip(af, ef))

def judge_outputs(actual: list[str], expected: list[str], tolerances: list) -> dict:
    total = len(expected)
    per_case = []
    for i in range(total):
        e = normalize_output(expected[i])
        a = normalize_output(actual[i]) if i < len(actual) and actual[i] is not None else None
        tol = tolerances[i] if i < len(tolerances) else None
        if a is None:
            per_case.append(False)
        elif tol is not None:
            per_case.append(_float_match(a, e, tol))
        else:
            per_case.append(a == e)
    return {"passed": sum(per_case), "total": total, "per_case": per_case}
```

- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** `feat(coding): output judge comparison + normalization`.

### Task P1-T3: Judge router

**Files:** Create `app/routers/coding.py`, `tests/test_coding_router.py`; register router in `app/main.py`.

- [ ] **Step 1: Write failing tests** (`tests/test_coding_router.py`) — mirror `tests/test_rough_sheet_endpoints.py` mocking style (patch `_assert_student_session_access`, `_load_exam_config`, `_atable`, and `reserve_idempotency`). Assert:
  - happy path returns `{passed, total}` only (NO `per_case`/`expected` leaked to body);
  - `teacher_id` written to the persisted row comes from the JWT claim, not the request body (pass a bogus `teacher_id` in body → ignored);
  - over the submit cap → 429 and no new row;
  - `coding_submissions` insert goes through `reserve_idempotency` (a duplicate idempotency key → single write).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `POST /api/v1/coding/judge` (`app/routers/coding.py`). Key invariants, in order:
  1. `claims = require_auth(request)`; `await _assert_student_session_access(claims, session_id)`.
  2. Load exam config; read `coding_max_submit_attempts` (default 10).
  3. **Submit cap:** `SELECT count(*) FROM coding_submissions WHERE session_id=? AND question_id=?` ≥ cap → `HTTPException(429, "submit limit reached")`.
  4. **Load hidden expected** under `with system_context():` — `SELECT idx, expected_output, float_tolerance FROM coding_test_cases WHERE question_id=? AND visibility='hidden' ORDER BY idx`. This is the only place expected outputs are read; they never go in the response.
  5. `result = judge_outputs(body["outputs"], expected_list, tol_list)`.
  6. Build the row with `teacher_id = str(claims.get("tid"))` (NOT body), `is_fully_solved` is generated, store `source_code`, telemetry from body.
  7. **Idempotent insert:** `acquired, cached = await reserve_idempotency(f"coding_judge:{session_id}:{question_id}:{attempt_hash}")`; if not acquired return cached; else insert + `release`/finalize per the existing pattern.
  8. Return `{"passed": result["passed"], "total": result["total"]}` — nothing else.
  - Decorate `@limiter.limit("30/minute")` AND enforce the per-question cap (cap is the real control; rate-limit is secondary).
- [ ] **Step 4: Run tests → PASS. Register the router in `app/main.py`.**
- [ ] **Step 5: Commit** `feat(coding): judge endpoint (server-authoritative, capped, idempotent)`.

### Task P1-T4: Test-case delivery (sample full, hidden inputs only)

**Files:** add `GET /api/v1/coding/testcases` to `app/routers/coding.py`; extend `tests/test_coding_router.py`.

- [ ] **Step 1: Failing test** — sample case returns `{input, expected_output}`; hidden case returns `{input}` with **no `expected_output` key**; assert the response JSON for a hidden case has no `expected_output` anywhere.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Under `with system_context():` select **explicit columns** — for sample: `idx, input, expected_output`; for hidden: `idx, input` (expected_output **omitted from the SELECT column list**, not filtered in Python). Return `{"sample": [...], "hidden_inputs": [...]}`. **Review gate (me):** confirm the hidden SELECT column list literally excludes `expected_output`.
- [ ] **Step 4: PASS. Step 5: Commit** `feat(coding): test-case delivery (hidden expected never serialized)`.

### Task P1-T5: Scoring branch

**Files:** Modify `app/services/scoring.py`; create `tests/test_coding_scoring.py`.

- [ ] **Step 1: Failing test** — an exam with 1 MCQ (correct) + 1 coding question (7/10 passed, `question_marks=10`, policy=partial) scores `1 + 7 = 8`; with policy=all-or-nothing scores `1 + 0`. Coding questions must NOT go through `answers_match` (they have no `correct`).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** In the auto-grade loop: split questions into `mcq_qs` (existing `answers_match` path) and `coding_qs`. For `coding_qs`, read the latest `coding_submissions` row per question for the session; marks = `round(passed/total * question_marks, 2)` (partial) or `question_marks if fully_solved else 0` (all-or-nothing), per the question's `options.marks_policy`. Add to `score`; add `question_marks` to `total`. **Guard:** keep the MCQ count-based path byte-for-byte unchanged (regression risk — recent double-canonicalization bug). Add coding marks as a separate accumulation, then combine.
- [ ] **Step 4: PASS + re-run existing `tests/` scoring suites to prove no MCQ regression.** **Step 5: Commit** `feat(coding): fold coding pass-ratio into scoring`.

### Task P1-T6: `coding-runtime.js` + JS adapter + sandbox — owner: other-Claude

**Files:** Create `renderer/coding-runtime.js` (the `runTestCases` API the renderer
imports) **and** `renderer/coding-worker.js` (the worker body, a flat same-origin file).

> **DECISION (2026-06-22, locked by Arihant) — same-origin worker FILE, not a Blob.**
> The kiosk CSP (`renderer/index.html:5`) is `default-src 'self'` with no `worker-src`,
> so `new Worker(blob:…)` is BLOCKED. We do **not** relax the CSP (never widen the
> proctored-exam page; `blob:` is an exfil vector). Instead the worker is a flat
> same-origin file loaded as `new Worker('coding-worker.js')` — runs under the existing
> CSP with zero relaxation. The "Blob" in the original instruction was about the
> *sandbox*, which a same-origin file preserves identically (still nullified). **This is
> the pattern ALL Phase 2–4 runtime workers (Pyodide/Clang/Doppio) must follow** — flat
> same-origin worker files + flat same-origin assets (the kiosk protocol handler clamps
> to a single path component, so NO `vendor/` subdir; keep any build/`node_modules` dir
> OUT of `renderer/` so `renderer/**/*` packaging doesn't pull it in).

- [ ] **Step 1:** Define the contract `runTestCases(language, source, stdins[]) → Promise<{outputs[], metrics[]}>` in `renderer/coding-runtime.js`. It does `new Worker('coding-worker.js')` (same-origin file — NOT a Blob), posts `{source, stdins, limits}`, awaits results.
- [ ] **Step 2: Worker sandbox** (`renderer/coding-worker.js`) — first lines: `self.fetch=null; self.WebSocket=null; self.XMLHttpRequest=null; self.importScripts=…guard`. JS adapter: wrap `source` so `console.log`/`print` capture to a string buffer; feed `stdin` via a readline shim. **Per-test wall-clock watchdog:** main thread `setTimeout(limit)` → if no result, `worker.terminate()`, mark that test `timeout`, respawn for the next test.
- [ ] **Step 3: Test UNDER THE REAL KIOSK CSP** — this is the verification gap that hid the Blob problem: the first pass ran in a plain page with no CSP. Verify in the actual Electron renderer (or a page serving the same CSP meta): a known JS program against known I/O; infinite loop → `timeout`; no network egress; **and the worker actually spawns under the CSP**. Capture console/screenshots per the verify skill.
- [ ] **Step 4: Commit** `feat(coding): coding-runtime.js + same-origin coding-worker.js (CSP-clean)`.

### Task P1-T7: Monaco mount + Run/Submit — owner: other-Claude

**Files:** Modify `renderer/index.html`.

- [ ] **Step 1:** Add `'coding'` to the allowlist at `renderer/index.html:2468`.
- [ ] **Step 2:** In `renderQ()` (~L2517), branch on `qtype==='coding'`: mount Monaco (bundled) with the question's `starter` code + language selector (allowed languages from `options`); render **Run** and **Submit** buttons; load `coding-runtime.js`.
- [ ] **Step 3: Run** → `runTestCases(lang, source, sampleInputs)` client-side, diff against sample `expected_output`, show input/expected/actual. **Submit** → run hidden inputs (from `GET /coding/testcases`), POST outputs+telemetry to `/coding/judge`, show "passed/total" counts only. Reuse the autosave path for the source; queue+retry the judge POST on network blip.
- [ ] **Step 4: Verify** on a built kiosk: Run shows diff, Submit shows counts, source autosaves, offline submit queues. Screenshot evidence.
- [ ] **Step 5: Commit** `feat(coding): kiosk Monaco editor + Run/Submit`.

### Task P1-T8: Minimal authoring seed — owner: DeepSeek

**Files:** Create `scripts/seed_coding_question.py`.

- [ ] Seed one `coding` question (statement + `options` languages/starter/marks_policy) + N `coding_test_cases` (mix sample/hidden) for a given exam/teacher, computing expected outputs by **running the reference solution through the same JS runtime** (Node for the seed script is acceptable in Phase 1 since the language is JS). Commit `chore(coding): seed script for a JS coding question`.

### Task P1-T9: Integration test (real PG) — owner: DeepSeek

**Files:** Create `integration_tests/test_coding_e2e.py`.

- [ ] Against the real-PG `integration_tests` harness: seed question+cases → POST `/coding/judge` with correct outputs → assert `coding_submissions` row (`test_cases_passed==total`, `is_fully_solved=true`, `teacher_id` set) → recompute score → assert fold-in. **Cross-tenant RLS:** a second teacher's context cannot SELECT the first's `coding_submissions` / `coding_test_cases`. Commit `test(coding): e2e submit→judge→score + RLS scoping`.

**Phase 1 done = a JS coding question is authored, taken in-kiosk, judged server-side, and scored into the existing scorecard, with RLS proven.**

---

## Phases 2–6 (roadmap — same spine, delegated per lane)

Each adds onto P1 interfaces; detailed task plans authored when P1 lands.

- **Phase 2 — Python (Pyodide) + TypeScript (transpile)** [other-Claude]: two new adapters behind the same `runTestCases` contract. Pyodide loaded once from **bundled** resources (not network). TS = `sucrase` transpile → JS adapter. No server change.
- **Phase 3 — C++ (Clang-WASM)** [other-Claude, gated on P0-T1]: priority language; adapter only. Ship the installer-size budget number first.
- **Phase 4 — Java (DoppioJVM or CheerpJ/OpenJDK fallback)** [other-Claude, gated on P0-T2].
- **Phase 5 — Authoring UI + AI-assist** [split]: dashboard coding-question editor (test-case grid, visibility, marks policy, paste policy, submit cap) [DeepSeek server endpoints + me/other-Claude dashboard JS — recall dashboard is HTML/JS only, CSP no-inline]; extend `llm.py:generate_questions` to draft statement+reference+inputs, then **run the reference to fill expected** (LLM never computes outputs); teacher review gate.
- **Phase 6 — Telemetry + polish** [split]: keystroke/paste/focus capture in the renderer → existing violation/risk system; `coding_paste_policy` enforcement; mixed-exam scorecard rendering.

---

## Self-Review

**Spec coverage:** every spec section maps to a task — seams 1–5 → P1-T1/T3/T5/T6/T7; Approach A judge → T2/T3; expected-output invariant → T4 (+ review gate); submit oracle cap → T3; scoring fold-in → T5; runtime/sandbox → T6; size/concurrency gates → P0; authoring/AI → P1-T8 + Phase 5; telemetry → Phase 6; RLS/guard compliance → T1 + T9.

**Placeholder scan:** server-side code is complete (T1/T2/T3 have real SQL/Python). Client WASM internals (T6) give the full JS-adapter + sandbox contract — correct for Phase 1 (JS only); heavier WASM adapters are Phase 2+ where they're each "one adapter on the proven contract," planned when P1 lands. No "TBD".

**Type/interface consistency:** `judge_outputs(actual, expected, tolerances) → {passed,total,per_case}` is used identically in T2 (def), T3 (caller), T9 (assert). `coding_submissions` columns identical across T1 (DDL), T3 (insert), T5 (read), T9 (assert). `runTestCases(language, source, stdins[])` identical in T6 (def) and T7 (caller).

**Security gates explicit:** hidden `expected_output` excluded from SELECT (T4 + my review); `teacher_id` from JWT (T3 + test); submit cap server-side (T3 + test); RLS policy-model guard (T1 + T9).
