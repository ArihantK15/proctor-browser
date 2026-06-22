# Edge Compiler — on-device verification pass (Electron)

Branch: **`feat/edge-phase1`** (server + client integrated). Run this on the
Windows kiosk box. Two stages — **Stage A is the critical one** (5 min, settles the
`procta-lobby://` worker/CSP residual the off-device run couldn't). Stage B is the
full Run→Submit→score flow and needs a backend with the coding endpoints.

Env overrides you'll use (from `config.js`):
- `PROCTOR_SERVER_URL=http://localhost:8000` — point the kiosk at a local backend.
- `PROCTOR_DEBUG=1` (or `--no-kiosk`) — relax kiosk lockdown so devtools is reachable.

---

## Stage A — worker/CSP under procta-lobby:// (do this first, ~5 min)

This is the only thing the Chromium (http://) run couldn't confirm. It does **not**
need the coding backend or a seeded question — `coding-worker.js` ships in the
build, so any exam page works.

1. **Build the kiosk from `feat/edge-phase1`.**
   - Quick dev run: in a checkout of the branch, `npm install` then
     `PROCTOR_DEBUG=1 npm start`.
   - Or a packaged build: `npm run build:win` (electron-builder) and launch the
     `--dir` output.
2. **Reach any exam page.** Launch an exam the normal way (it can point at prod —
   `coding-worker.js` is bundled regardless of backend). Get past calibration to
   the question screen (origin is `procta-lobby://`).
3. **Open devtools** (works because `PROCTOR_DEBUG=1` / `--no-kiosk`).
4. **Paste the contents of `scripts/coding-csp-selftest.js`** into the console.
   - **✅ PASS** printed + **zero CSP violations** in the console → the residual is
     settled: `new Worker('coding-worker.js')` spawns and evals under the real
     scheme + CSP. Edge Compiler's worker model is confirmed on-device.
   - **❌ FAIL** with a CSP error → the worker is blocked under `procta-lobby://`.
     Do **not** relax the CSP yourself — report it; the fix is a scoped
     `worker-src 'self'` addition, which the team should confirm is needed before
     widening the proctored-page CSP.

If Stage A passes, the architecture is proven; Stage B is then just exercising the
real data path.

---

## Stage B — full Run → Submit → score (needs the coding backend)

The coding endpoints (`/api/v1/coding/judge`, `/coding/testcases`) and the
scoring fold-in live on `feat/edge-phase1`, **not on prod**. So run that backend
locally and point the kiosk at it.

### B1 — backend up with the coding schema
1. From a `feat/edge-phase1` checkout, with your dev `.env` (DATABASE_URL etc.):
   apply migrations so `coding_test_cases` / `coding_submissions` /
   `exam_config.coding_max_submit_attempts` exist —
   `python3 scripts/run_postgres_migrations.py` (runs phase141 + phase142).
2. Start the API: `uvicorn app.main:app --port 8000` (however you normally run it).

### B2 — seed a coding question
`python3 scripts/seed_coding_question.py` (needs `node` on PATH — it computes the
expected outputs by running the reference solution; same `.env` as the backend so
it writes to the same DB). It prints the **Teacher ID / Exam ID / Question PK** and
a curl line. Note the exam_id — you'll join that exam. (If the exam needs an access
code / a registered roll to enter, add one the way you do for a normal test exam;
the seed creates the `exam_config` + question + test cases.)

### B3 — launch the kiosk at the local backend
`PROCTOR_SERVER_URL=http://localhost:8000 PROCTOR_DEBUG=1 npm start`
Join the seeded exam, get to the coding question.

### B4 — verification checklist
- [ ] **Editor mounts** — CodeMirror with syntax highlighting + language selector.
- [ ] **Run** (sample) — shows per-case input / expected / actual diff and a
      `passed/total` for the sample cases (graded client-side, instant).
- [ ] **Submit** — runs the hidden inputs, POSTs to `/coding/judge`, shows
      `Passed N/total` (counts only — no per-case, no expected leaked).
- [ ] **Source autosave** — type code, reload the page mid-edit → the source
      survives (rides the existing bulk-answer autosave at `answers[q.id]`).
- [ ] **Offline submit** — kill the network, Submit → it queues; restore network →
      it retries and lands.
- [ ] **Submit cap** — Submit more than `coding_max_submit_attempts` (default 10)
      times → server returns 429 "Submission limit reached".
- [ ] **Score fold-in** — finish the exam; the coding marks
      (`passed/total × question_marks`) appear in the score/scorecard alongside MCQ.
- [ ] **devtools console: zero CSP violations** throughout (the procta-lobby://
      confirmation again, now through the real UI).

### B5 — sanity on the server side (optional)
After a Submit, check the DB: `coding_submissions` has a row with `teacher_id`
stamped (from the JWT, not the body), `test_cases_passed/total`, `is_fully_solved`
computed, and `source_code` stored.

---

## If something fails
- Worker won't spawn (Stage A ❌) → CSP under custom scheme; report, don't relax CSP.
- `/coding/testcases` 400 → it requires `session_id` (the client now sends it; if a
  stale build doesn't, that's the fix already folded into the integration).
- Coding score is 0 despite passes → question-id keying; the integration unified
  everything on `questions.id` (the seed sets an explicit UUID PK) — confirm the
  seed ran from `feat/edge-phase1`, not an older copy.
- Camera/calibration blocks before you reach the question → that's the proctor
  flow, not the coding feature; the calibration-misfire fix is a separate branch
  (`fix/proctor-calibration-misfires`).
