# Server-side coding execution — design

Date: 2026-06-23
Status: design approved (brainstorm), pending spec review → implementation plan
Supersedes the execution half of: `2026-06-21-edge-compiler-coding-engine-design.md`
(Approach A, client-side execution) and `2026-06-22-edge-compiler-phase2-runtimes-design.md`
(Pyodide/sucrase client runtimes).

## 1. Summary

Move code **execution** off the student's laptop into a hostile-by-default
server-side service. Proctoring (webcam/screen/audio) stays **100% on-device,
unchanged**. The CodeMirror editor stays. The entire client execution layer is
deleted.

This resolves, in one move, the problems the client-side path kept generating:
low-end-laptop CPU contention, the Pyodide/WASM-under-CSP saga, compiled-language
(C++/Java) pain, and — most important — **forgeable client-side grading**. Server
execution is authoritative: the student controls neither the run nor the
comparison.

## 2. Why this is policy-clean (the gating question)

The "never leaves the device" promise is, by the actual wording of our own
policy and marketing, **scoped to biometric/proctoring data only**:

- DPDP blog: *"Video never leaves the student's device. Only structured violation
  metadata is transmitted… eliminates the biometric data processing trigger."*
- Signup / Features: *"raw video never leaves it"*, *"raw audio never leaves the
  device."*
- DPIA §2: the on-device mandate is a **data-minimisation control for the
  surveillance stream** (webcam/screen/audio = biometric/personal).

Student **code is an exam answer, not proctoring/biometric data**, and it
**already transits to and is stored on the server today** (`app/routers/coding.py`
stores `source_code` in `coding_submissions`). Running that code server-side
processes data that is already server-side. **No privacy-policy or marketing-claim
conflict**, provided the webcam/screen/audio stream stays on-device (it does —
unchanged).

What we give up is the **"Edge Compiler / zero-server-compute" *positioning***,
which was a strategic/cost choice, not a legal constraint. The portability
requirement (§7) turns that loss into a stronger story: an on-prem self-hostable
execution appliance.

## 3. Three trust zones (the whole architecture)

1. **Client (kiosk) — executes nothing.** CodeMirror editor only. On Run/Submit
   it POSTs `{session_id, question_id, language, source}` to the app.
2. **App / orchestrator (FastAPI) — trusted, holds all secrets.** Owns test
   cases, **secret expected outputs**, the per-question attempt cap, idempotency,
   and scoring. Calls the execution service once per input, **does the stdout vs
   expected-output comparison itself**, writes `coding_submissions`, returns
   `{passed, total}`.
3. **Execution service — standalone, fully hostile, owns nothing.** One narrow
   endpoint:
   `POST /run {language, source, stdin, cpu_ms, wall_ms, mem_mb, output_kb}`
   → `{stdout, stderr, exit_code, time_ms, mem_kb, timed_out, oom, compile_error}`.
   Network-isolated, no DB/S3/secret access, no egress. **Never receives or sees
   expected answers** — so there is nothing to steal even before isolation.

## 4. Execution model (per run)

A single run is fully ephemeral:

1. Pull a fresh **Firecracker microVM** (KVM, ~125 ms boot) from a warm pool. The
   rootfs is pre-baked with the language toolchains (§ open item 9.2).
2. Inside the VM, **`isolate`** runs the program as a non-root user under
   cgroups + seccomp + rlimits: CPU time, wall time, memory, output size, process
   count, no new privileges.
3. Compiled languages (C++/Java) **compile inside the same VM**, with the compile
   step also time/memory-capped.
4. Capture stdout/stderr/exit/metrics. **Destroy the VM.**

Defense-in-depth: a sandbox (`isolate`) escape lands inside the microVM; a microVM
escape would still be a network-dead, credential-less, ~125 ms-lived box. A breach
is engineered to be **worthless**, not merely unlikely.

## 5. Data flow (Run and Submit)

Run and Submit are the same path; they differ only in *which* inputs and *what is
returned*:

- **Run** → sample cases. App may return per-case diffs (sample expected outputs
  are public by design).
- **Submit** → hidden cases. App returns **only `passed/total`** (output-oracle
  defense — the per-question attempt cap from the current `coding.py` is kept).

Flow: `client → app → (for each input) execution service /run → app compares
stdout to secret expected → app stores + returns counts`. Reuses
`coding_test_cases` (inputs + expected) and `coding_submissions` (results); add
columns for compile output and execution metrics (§ open item 9.3).

## 6. Security requirements (the "no slightest hiccup" bar)

Treated as acceptance criteria, each with an explicit test:

- **No network egress** — the microVM has no usable NIC / all egress blocked.
- **No host or credential access** — execution host carries no DB/S3/JWT secrets;
  the only inbound is the app→service `/run` API over a private link.
- **Ephemeral, non-root, seccomp + rlimits** — enforced by `isolate`; FS is a
  per-run throwaway overlay.
- **Executor holds no expected outputs** — comparison is in the trusted app only.
- **Resource caps enforced** — CPU/wall/mem/output/pid, on both compile and run.

**Escape-attempt test suite (part of "done"):** network egress attempt (must
fail), host/file access attempt (must fail), fork bomb, unbounded memory (OOM),
infinite loop (wall timeout), oversized stdout (truncated/killed), compile-bomb.
This suite gates every release of the execution service.

## 7. Portability / enterprise self-host

The execution service ships as **one self-contained, versioned artifact**
(Firecracker + rootfs images + job queue + orchestrator). The same artifact is
what we **redeploy, fail over to, or license to an enterprise to run on-prem** —
the differentiator that replaces "edge." Implication: the service must not assume
co-location with our app beyond the single authenticated `/run` API, and must be
configurable (limits, language set, pool size) without code changes.

## 8. What is removed / reused

- **Removed (client execution layer):** `renderer/coding-runtime.js`,
  `renderer/coding-worker.js`, the planned Pyodide path + `coding-worker-py.js`,
  `renderer/sucrase.bundle.js` + `tools/sucrase-build/`, and the CSP worker
  relaxations they required. TypeScript transpilation moves server-side (`tsc`/
  esbuild in the VM). The CodeMirror editor + its TS highlighting **stay**.
- **Changed:** `/api/v1/coding/judge` becomes an **orchestrator/executor**
  (runs + compares) instead of a pure comparator. `/api/v1/coding/testcases`
  delivery is unchanged.
- **Reused:** `coding_test_cases`, `coding_submissions`, the attempt-cap +
  idempotency + RLS logic in `app/routers/coding.py`, the dashboard authoring
  form, the scoring fold-in (`app/services/scoring.py`).

## 9. Decisions + open items

**Decided:**

- **Language set (v1):** **JavaScript, TypeScript, Python, C, C++, Java.** C is
  included — it is core to Indian higher-education CS curricula. The rootfs bakes
  `node`, `tsc`/esbuild, CPython, `gcc` (C), `g++` (C++), and a JDK (`javac`/
  `java`).
- **Failure/degradation policy = "make them wait" (LeetCode-style).** A Submit is
  never silently dropped and never auto-fails on congestion. On submit the student
  sees a **"running on the server — please wait"** state (spinner) while the job
  queues + executes; the kiosk already shows this (`coding-ui.js`). The server
  queues with backpressure; if the service is briefly down the client retry-queue
  resubmits automatically. A submission only resolves to Accepted/Wrong-Answer
  after the server has actually run + graded it — exactly like LeetCode "Judging…".

**Open (explicitly unresolved):**

1. **Exam-burst scaling numbers.** Worker-pool size + queue backpressure must be
   sized against *real* exam concurrency (class sizes, simultaneous exams). No
   hardware commitment until we have those numbers. ("Server size: let's see.")
2. **Rootfs sizes / build pipeline.** The language set is fixed (above); confirm
   the resulting rootfs image size and the build/update pipeline.
3. **`coding_submissions` schema additions.** Columns for compile output and
   execution metrics; migration phase number TBD.
4. **Queue bounds.** Max wait before the student is told "still judging, hang on"
   vs. a hard ceiling; per-student Run throttle to bound pool load.

## 10. Testing strategy

- **Execution service:** per-language correctness (hello world, stdin, compile
  error), resource-limit enforcement, and the §6 escape-attempt suite. The
  security suite is mandatory and gating.
- **App orchestrator:** comparison correctness, attempt cap, idempotency, and a
  test asserting the executor is **never** sent expected outputs.
- **End-to-end:** kiosk editor → app → execution service → scorecard, per
  language.

## 11. Sequencing (high level — detailed plan follows in writing-plans)

1. Execution service skeleton: `/run` + Firecracker+isolate for one language
   (Python), network-isolated host, + the escape-attempt suite **first**.
2. App orchestrator: rewire `/coding/judge` to call `/run` + compare; schema
   additions.
3. Add languages (JS, TS, C, C++, Java) to the rootfs; per-language tests.
4. ~~Remove the client execution layer; simplify the kiosk to editor + POST.~~
   **DONE** ahead of the plan (commit `09ba4839`): client execution stack
   deleted, `coding-ui.js` rewired to POST source to `/run` + `/judge`.
5. Package the service as the portable self-host artifact.
6. Scaling pass against real exam-size numbers.
