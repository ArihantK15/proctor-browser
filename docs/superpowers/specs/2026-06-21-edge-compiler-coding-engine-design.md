# Edge Compiler — Client-Side Coding-Assessment Engine (Design Spec)

Status: Design approved (brainstorm) · 2026-06-21
Owner: Arihant
Source idea: `~/Desktop/Edge Compiler Architecture Plan.pdf` (this spec supersedes
it — it corrects the PDF's forgeable client-side grading and the "12 MB GCC" /
all-crypto-layers claims).

## Purpose & beachhead

A coding-assessment engine (HackerRank/LeetCode-style) where **student code
executes 100% on the device**, the server acts as **judge + ledger**, and the
whole thing rides Procta's *existing* exam, proctoring, RLS, and scoring rails.
Strategic goal: open a **new buyer** beyond coaching institutes.

**Beachhead (v1 designed for):** college / engineering CS labs — DS&A-style
problems, C++ the priority language, Java/Python close behind, JS/TS broadening
reach (bootcamps/web courses).

## Locked decisions (from the brainstorm)

1. **Problem format:** stdin/stdout is the v1 execution substrate. Function-signature
   problems come later as a *wrapper-generation layer* that compiles down to the
   same stdin/stdout execution — no second engine.
2. **Execution model:** in-browser **WASM/JS sandbox** (Web Worker). Chosen over
   native toolchains so untrusted student code is host-isolated for free and the
   engine is OS-agnostic (no per-OS native sandbox, no compiler bundling).
3. **Languages (v1, 5):** JavaScript (native), TypeScript (transpile→JS),
   Python (Pyodide), C++ (Clang-WASM), Java (DoppioJVM — spiked first).
4. **Exam composition:** `coding` is modelled as a **new `question_type`**, not a
   separate exam mode. A pure coding exam = all-`coding` questions; a mixed exam =
   `coding` + MCQ. Both fall out for free.
5. **Grading & integrity:** **Approach A — client executes, server judges.** Code
   runs client-side (zero server execution cost); the program's *outputs* go to the
   server; the server holds the **secret expected outputs** and decides pass/fail.
   Forging a pass requires actually solving the problem — not patching a client
   boolean. Pass/fail is **server-authoritative**.
6. **Expected outputs are generated, never typed** — by running a *reference
   solution* through the same runtime. Guarantees correctness; speeds authoring.
7. **Anti-cheat:** the existing **proctoring stack is the real integrity layer**;
   coding telemetry (keystroke/paste/focus) is soft evidence. The PDF's crypto
   signature handshake is **redundant under Approach A** (existing JWT auth covers
   transport) and the screen-validation engine is **deferred** — both out of v1.

## Architecture & integration seams

The engine is a new question type on existing rails. Five touch points:

| # | Seam | Change |
|---|------|--------|
| 1 | `question_type = "coding"` ([repositories/questions.py:62](../../../app/repositories/questions.py)) | New type; non-secret config in the existing `options` JSON (no questions-table migration). |
| 2 | **Kiosk** question renderer ([renderer/index.html:2517](../../../renderer/index.html), `renderQ()`) | On a `coding` question, mount **Monaco editor + language selector + Run/Submit**. **Add `'coding'` to the `question_type` allowlist at [renderer/index.html:2468](../../../renderer/index.html)** (currently `['mcq_single','mcq_multi','true_false','short_answer','numeric']`). New `coding-runtime.js` owns the sandbox. **NOT** `app/static/student-app.js` — that is the web *dashboard* (no `renderQ`); the proctored exam runs in the Electron kiosk renderer. See "Surface decision" below. |
| 3 | Judge endpoint (new `app/routers/coding.py`) | Receives outputs+telemetry, compares against secret expected outputs, returns pass/fail counts (no expected leaked). Idempotent via the existing `reserve_idempotency` atomic pattern (see Error handling). |
| 4 | Scoring ([scoring.py:142](../../../app/services/scoring.py)) — **genuine new branch, not a one-liner** | Today scoring is **count-based**: `score = sum(1 for q in auto_qs if answers_match(…))`, `total = len(auto_qs)`, keyed off `q["correct"]`, reading the `answers` table. Coding grading is **fractional + weighted** (`passed/total × question_marks`, or all-or-nothing per question) and its result lives in **`coding_submissions`, not `answers`**. So the scorer needs a new branch that joins `coding_submissions` and supports partial/weighted marks. Given the recent scoring double-canonicalization bug, this path gets **its own tests**, not a casual edit. |
| 5 | Two new tables | `coding_test_cases` (server-secret cases) + `coding_submissions` (results+telemetry). |

**Reused as-is (zero new work):** kiosk Layer-1 lock (DevTools/shortcut blocking —
[kiosk-manager.js:425](../../../lib/kiosk-manager.js)), proctoring + webcam + YOLO
detection + risk score, JWT auth, RLS tenancy, scorecard PDF, AI question generation
([llm.py:166](../../../app/llm.py)), the answer-autosave snapshot path.

**Blast radius:** 1 new kiosk module (`coding-runtime.js`), 1 new router, 2 new
tables, a renderer-dispatch edit (small) + a **scoring branch (not small — its own
tests)**. Everything else is reuse.

### Surface decision (v1 = kiosk only)

The integrity story is "code runs inside the proctored kiosk," so v1 mounts coding
**only in the Electron kiosk renderer** (`renderer/index.html`). The non-kiosk **web
path is a separate, explicitly out-of-scope surface** — adding it later means a
second renderer integration AND a different Monaco/WASM bundling story (a browser tab
can't bundle runtimes in app resources the way Electron can). Decide it deliberately
when/if a non-proctored web use-case appears; do **not** let it sneak in.

## Runtime contract & sandbox

`coding-runtime.js` exposes one function:
```
runTestCases(language, source, [stdin₁, stdin₂, …]) → [output₁, output₂, …]
```
Behind it, one adapter per language implementing `compile(source)` + `run(stdin, limits)`:
- **JavaScript** — native; run in the nullified Web Worker. No download.
- **TypeScript** — `sucrase`/`esbuild-wasm` transpile → JS, then the JS adapter.
- **Python** — Pyodide (loaded once, cached in IndexedDB), `exec` with stdin redirect.
- **C++** — Clang-WASM compiles source→wasm, runs it (~tens of MB cached once).
- **Java** — DoppioJVM runs `javac` then the `.class` (spike-gated).

**Sandbox (Web Worker):** nullified context (`self.fetch=null`, `self.WebSocket=null`);
per-test **wall-clock watchdog** kills infinite loops (test → `timeout`); WASM
memory cap; worker is terminable/respawnable. WASM runtimes are inherently
host-isolated (no fs/network).

## Performance & delivery on low-end laptops (make-or-break)

This is the same wall the proctor governor hit — if it doesn't hold on a weak box,
the feature is pointless. Two first-class constraints:

**1. Concurrent resource load with live proctoring (the real test condition).**
On exam day the laptop is *already* running `proctor.py` (YOLO + gaze + RetinaFace,
throttled to ~3–7 fps on weak machines) plus the webcam. Adding a Clang-WASM compile
or a JVM in the renderer's Web Worker on the same 4-core / 4-GB box can starve one or
the other. **Phase-0 spikes must measure each runtime running *concurrently with live
proctoring*, not in isolation.** The go/no-go gate for C++ and Java is: **proctor fps
holds, the compile/run finishes within the time limit, and no OOM** — measured
together, on a representative low-end machine. Cold-start size alone is not the gate.

**2. Build size vs. the runtimes (direct conflict with recent installer-shrinking).**
We just shrank the Electron installer (fp16 gaze, dropped Hindi vosk, killed SAHI).
Clang-WASM (tens of MB) + Pyodide (~10 MB+) + DoppioJVM/`rt.jar` (heavy) pull hard the
other way. And "pre-load at exam start, cache in IndexedDB" **assumes a working
network** — exam halls are frequently locked-down/offline, so downloading tens of MB
at the bell is a failure mode, not a fallback. **Per-language delivery decision (own
the size number):**
- **Monaco editor (~5 MB)** — a *fixed* cost paid the moment any coding question
  exists, independent of language. Count it in the budget up front; it ships in the
  Electron bundle for all coding exams.
- **JS / TS** — bundled, tiny (no runtime payload beyond Monaco). Always available offline.
- **Python / C++ / Java** — **bundle-vs-download behind a per-language feature flag.**
  For the kiosk path, prefer **bundling the runtime in Electron resources** (like the
  proctor models are baked in) so it works offline, accepting the installer-size hit;
  download-on-demand only where bundling is infeasible (Java/`rt.jar`), and then it
  must be **pre-provisioned before exam day**, never fetched at the bell.

The implementation plan must produce a concrete installer-size budget per enabled
language before C++/Java ship.

## Run vs Submit (maps onto Approach A)

- **Run** → *sample/visible* cases only. Their expected outputs are public (worked
  examples), so this grades **client-side instantly** with a full input/expected/
  actual **diff**. No server involvement.
- **Submit** → full **hidden** test set. Outputs POST to `/coding/judge`; server
  judges against secret expected outputs; student sees **counts only** ("7/10").

**Submit cycle:** worker compiles once → runs each hidden stdin (with timeout) →
captures stdout + time/memory → POST `{session_id, question_id, language, source,
outputs[], metrics, telemetry}` → server normalizes + compares (trim trailing
whitespace; per-question float tolerance) → writes `coding_submissions` → returns
pass/fail counts. **Source is stored** (for teacher review + plagiarism), not used
for judging.

**Honest residual:** a *fully cracked* client could submit outputs it didn't compute
— but knowing the correct outputs means solving the problem, and stored
source-vs-output consistency + live proctoring catch the rest. Far smaller than
"patch `isPassed=true`." Future hardening (deferred): server re-runs a sampled
submission.

## Authoring & where secrets live

A coding question: statement (existing `question` field), allowed languages, optional
starter code, time/memory limits, test cases `{input, visibility: sample|hidden,
float_tolerance?}`, marks + policy.

**Expected outputs are generated by running a reference solution** through the same
runtime — not hand-typed, not LLM-computed. Teacher (or AI) supplies a reference
solution + inputs; the tool executes it to fill expected outputs.

**AI-assist** extends `llm.py:generate_questions`: LLM drafts statement + reference
solution + test inputs; the system *runs the reference solution* to produce expected
outputs (LLM never computes outputs). **Teacher reviews/edits before publish.**

**Storage split:**
- `questions.options` JSON (client-readable): languages, starter, limits, marks policy.
- **`coding_test_cases`** (server-authoritative, RLS): `question_id, idx, input,
  expected_output, visibility, float_tolerance`. Sample cases' input+expected sent
  to client; hidden cases' **input delivered at submit time, `expected_output`
  NEVER serialized to the client**.

  **This is a QUERY-LAYER invariant, not a client convention.** It only holds if the
  read path enforces it: the **student role has no RLS `SELECT`** on
  `coding_test_cases` at all (the table's policy is teacher/`app.*`-scoped for
  authoring + system for delivery). Test-case delivery to the student is a
  **server-side elevated read** (the `system_context()` pattern, as used for
  email_otps) that returns sample `input`+`expected` and hidden `input` only —
  hidden `expected_output` is *projected out in the SELECT* before the response is
  built, so it never enters a payload the client can reach. A code reviewer must be
  able to point at the exact SELECT column list and confirm hidden `expected_output`
  is absent.

**Cross-language output formatting (authoring caveat).** A problem that allows
multiple languages can produce **different stdout for the same logic** — float
precision (`0.3` vs `0.30000000000000004`), list/array formatting, trailing newlines.
Server normalization (trim + per-question float tolerance) covers space-separated-int
DS&A output but **not** structured output. So for multi-language problems, either
**generate expected outputs from a per-language reference solution** (each language's
reference produces that language's expected set) **or** constrain the statement's
output format hard (e.g. "print one integer per line"). The authoring UI must surface
this — a single reference solution's output is **not** automatically valid for every
allowed language.

**Limitation (inherent to client-side execution):** hidden test *inputs* are exposed
to a determined student (the code must run on them); only expected *outputs* are
protected — which is what preserves grading integrity. Problems where knowing the
input leaks the answer are unsuitable for this model (flagged at authoring).

## Anti-cheat posture & telemetry

Layered; each layer does what it's good at:
1. **Server-authoritative grading (A)** — can't fake a pass without solving.
2. **Kiosk Layer-1** (built) — DevTools/shortcuts blocked.
3. **Proctoring** (built) — webcam/screen/YOLO/risk score = the *real* anti-cheat.
4. **Coding telemetry (new, soft evidence, never auto-fail):** `keystroke_rhythm_variance`
   (paste/macro), `paste_attempts`, `focus_loss_count` → existing violation/risk system.

**Submit is an output ORACLE — bound it.** "Submit → counts only (7/10)" reads like a
protection, but **unbounded** it is a *leak*: a student can flip one element of
`outputs[]` at a time across repeated submissions and read the count delta to infer
each hidden `expected_output` bit-by-bit, never solving the problem. Rate-limiting
only *slows* this. Defenses (all three): **(a) a hard cap on Submit attempts per
question per session** (configurable, default low e.g. 10 — distinct from the
per-minute rate limit); **(b)** the judge persists every attempt to
`coding_submissions`, so oracle-probing shows up as a burst of near-identical
submissions for teacher/risk review; **(c)** live proctoring. The attempt cap is the
primary control — name it `coding_max_submit_attempts` in exam config.

**Paste policy:** per-exam `coding_paste_policy: log | block` (**default `log`** —
hard-blocking all paste in Monaco also blocks a student moving their *own* code
around, which is hostile; strict exams opt into `block`). The attempt is **always
logged** either way.

**Deferred (with reasons):** crypto signature handshake (redundant under A + existing
JWT auth); screen-validation engine (proctoring already covers the threat; heavy,
platform-specific).

**`coding_submissions` table:** `id, exam_id, teacher_id, session_id, student_id,
question_id, language, test_cases_total, test_cases_passed, is_fully_solved
(GENERATED), average_execution_ms, memory_consumed_kb, source_code,
keystroke_rhythm_variance, paste_attempts, focus_loss_count, submitted_at` + index
`(exam_id, student_id)`. **`teacher_id` is a real column** (not derived) — it makes
the RLS policy a direct `app.teacher_id()` check and lets the offboarding guard
categorize the table (which enumerates *teacher_id* tables). It is stamped by the
judge endpoint from the authenticated JWT, never the client body.

**Compliance & migration mechanics (concrete, hard requirements):**
- **Migrations:** two new **phase-numbered** files — next free is **`phase141` /
  `phase142`** (highest on disk is `phase140`).
- **Schema-ref guards:** add both tables to **`schema/columns.json`** and
  **`integration_tests/schema.sql`**, or the schema-ref guard fails CI.
- **RLS — phase124 `app.*` model only** (NOT `auth.uid()`/`get_my_teacher_id()`, which
  `test_rls_policy_model_guard.py` rejects):
  - `coding_submissions` carries a direct tenancy column → policy uses
    `app.teacher_id()` / `app.visible_teacher_ids()` directly.
  - `coding_test_cases` has **no direct `teacher_id`** — it scopes via
    `question_id → questions.teacher_id`, so its policy needs an **`EXISTS` subquery**
    joining `questions`, and must **match types carefully** (the legacy helper returns
    TEXT; the `app.*` helpers + join columns need consistent `::text` casts).
- **Offboarding guard — COORDINATION, not an edit I make:** `coding_submissions`'
  `teacher_id` scoping means it needs a MOVE/KEEP entry in
  `test_all_teacher_id_tables_are_categorized` — which lives in
  **`tests/test_teacher_transfer.py`, currently another session's uncommitted WIP.**
  Per the no-concurrent-edits rule, this is a **dependency to sequence with that
  session**, not a file this work touches.
- Judge endpoint rate-limits/logs submit attempts.

## Error handling (rule: never lose the student's code)

- Runtimes **pre-load at exam start** from **bundled Electron resources** (not the
  network — see Performance & delivery; IndexedDB caching is the out-of-scope web
  path). The pre-load verifies each enabled runtime initialises before the first
  question, surfacing a corrupt/missing bundle early with a retry, never mid-exam.
- Compile error / runtime crash / timeout = normal feedback → show `stderr`, mark
  tests failed/timed-out; worker killed+respawned on hang.
- **Source continuously autosaved** via the existing `cache_autosave_snapshot` path.
- **Judge call idempotent + retry-queued** — a network blip queues locally and
  retries (same pattern as answer autosave). Idempotency uses the existing **atomic
  `reserve_idempotency`** pattern ([idempotency.py], the one that fixed the
  grade-confirm TOCTOU) — **not** a naive get-then-set, since the judge is
  retry-queued and a double-fire must not double-write `coding_submissions`.

## Testing (reuses existing harnesses)

- **Unit:** judge comparison (normalization, float tolerance, pass-count) + scoring fold-in.
- **Per-language adapter:** known program + known I/O per runtime in CI.
- **Integration (real PG):** extend `integration_tests/` — submit → judge → assert
  `coding_submissions` row + score + **cross-tenant RLS scoping**.
- **Sandbox:** infinite-loop→kill, fork/malloc-bomb contained, no network egress.
- **Guard compliance:** new tables pass the RLS policy-model + offboarding guards.

## Build sequencing (each phase independently testable)

- **Phase 0 — Spikes (de-risk, go/no-go gates):** DoppioJVM Java + Clang-WASM C++ on
  a *low-end* laptop, **run concurrently with live `proctor.py`** (the real exam-day
  condition). **Gate = proctor fps holds + compile/run finishes within the time limit
  + no OOM**, measured together — *not* cold-start size in isolation. Pyodide/JS need
  no spike. Failing runtime → documented fallback (CheerpJ license / local-OpenJDK for
  Java). This phase also produces the **per-language installer-size budget** (see
  Performance & delivery) before C++/Java are greenlit.
- **Phase 1 — Vertical slice in JavaScript:** `coding-runtime.js` + JS adapter +
  Monaco renderer + Run/Submit + `/coding/judge` + server comparison + 2 tables +
  RLS + minimal authoring (seed a question) + basic auto-grade into score. **Proves
  the whole pipeline with zero runtime friction.**
- **Phase 2 — Python (Pyodide) + TypeScript (transpile).** Both light.
- **Phase 3 — C++ (Clang-WASM).** The priority language, on the proven spine.
- **Phase 4 — Java (DoppioJVM or fallback).**
- **Phase 5 — Authoring UI + AI-assist** (reference-solution-generates-outputs;
  `llm.py` extension; teacher review gate).
- **Phase 6 — Telemetry + polish** (keystroke/paste/focus, paste policy, mixed-exam
  scorecard integration).

## Out of scope (v1)

Function-signature problem format (later layer); crypto signature handshake; screen-
validation engine; server-side re-execution; macOS/Linux-specific work (WASM is
OS-agnostic so not needed); the PDF's native-toolchain path.
