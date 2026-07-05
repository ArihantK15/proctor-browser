# Code plagiarism detection for the coding module (design)

## Context

The coding-exam module (`app/routers/coding.py`, `coding_submissions` table,
`execsvc` sandbox for python/javascript/typescript/c/cpp/java) has zero
code-level plagiarism/similarity detection today — confirmed via grep at the
start of this session. Integrity for coding exams currently relies entirely
on the webcam/window-focus/paste-detection layer, not on the submitted code
itself: a student who pastes correct code while behaving normally on camera
is currently invisible. Competitors (HackerRank, CodeSignal, Codility) all
treat code-level integrity checking as a core pillar — HackerRank
specifically describes an "ML-based plagiarism detection system... analyzes
coding behavior, attempt submission patterns, and question features."

Two pieces of relevant existing infrastructure, found during this design's
exploration, were unused until now:
- `coding_submissions` already stores `source_code`, `language`,
  `question_id`, `exam_id`, `student_id` per submission — everything needed
  to compare code across students on the same question.
- `coding_submissions.keystroke_rhythm_variance` and `.paste_attempts` are
  already collected client-side (`renderer/coding-ui.js`) and stored, but
  never factored into anything — confirmed via grep of `app/services/scoring.py`.
- There is currently no teacher-facing UI that shows submitted source code
  at all (confirmed via grep of the dashboard JS/HTML) — this design adds
  the first one.

## Decision: Dolos (open-source, MIT), invoked headlessly as a subprocess

Researched three approaches:
- **Dolos** (chosen) — purpose-built for exactly this use case (academic
  plagiarism detection across a student cohort), MIT-licensed, actively
  maintained, ships with tree-sitter parsers for all 6 required languages
  out of the box (Bash, C, C++, C#, Elm, JavaScript, Java, PHP, Python,
  TypeScript — covers python/javascript/typescript/c/cpp/java). Its
  documented headless mode, `dolos run -f csv -l <language> <files>`, is
  explicitly recommended by Dolos's own docs for automated-pipeline
  integration — exactly our use case.
- **Hand-rolled tree-sitter AST comparison** — rejected. Would mean
  reimplementing what Dolos already does maturely, with real risk of ending
  up less accurate, for the sole benefit of avoiding one subprocess
  dependency (which this codebase already has a precedent for — `execsvc`
  is architected the same way, as an isolated subprocess-based service).
- **ML embedding-based semantic similarity** — rejected for v1. Better at
  catching heavily-obfuscated copying, but needs hosting a separate model,
  careful threshold calibration, and produces a similarity score without
  Dolos's clear "these exact lines match" fragment highlighting — much less
  actionable for a teacher. Could be a future layer on top of Dolos, not a
  v1 replacement.

**Explicitly out of scope:** AI-generated-code detection (catching
ChatGPT-written code, as distinct from copied-from-a-peer code). This is a
materially different technique (needs its own classifier/heuristic or an
LLM-based judge) that HackerRank's system also does, but folding it into
this spec would be scope creep into an unresearched, separate detection
problem. Flagged here as a known, real, deliberately-deferred gap — not
silently dropped.

## Architecture / data flow

**Trigger:** background job enqueues automatically when an exam transitions
to "ended" (reusing whatever mechanism already triggers post-exam AI
grading), plus a manual "Re-run plagiarism check" button on the teacher
dashboard.

**Job flow, per exam:**
1. For each coding question in the exam, pull all `coding_submissions` rows
   for that `(exam_id, question_id)`.
2. Group by `language` — only compare submissions in the same language.
3. For each language group with ≥2 submissions: write each `source_code` to
   a temp file named by `submission_id` (for traceability), run
   `dolos run -f csv -l <language> <files>` as a subprocess with a hard
   timeout.
4. Parse Dolos's CSV output for pairwise similarity scores + matched
   line/fragment ranges.
5. For pairs at or above the similarity threshold (env-var-overridable
   constant, `CODING_PLAGIARISM_THRESHOLD`, default 0.7 — same
   `os.getenv(...)`-backed constant pattern as `EYE_OPEN_RATIO_THRESHOLD`
   and other tunable thresholds already in this codebase):
   compute a **corroboration flag** from the existing-but-unused behavioral
   telemetry — `paste_attempts_a > 0 OR paste_attempts_b > 0` and/or
   anomalously low `keystroke_rhythm_variance` on either submission (typing
   with near-zero variance across an otherwise-correct solution is
   consistent with pasting rather than composing). This is a **simple
   rule-based combination, not a trained ML score** — deliberately not
   over-engineered given we have no historical distribution of these values
   yet to calibrate against. The exact "anomalously low" variance threshold
   needs real field calibration once submissions accumulate; ship with a
   conservative starting guess and revisit once real data exists, the same
   way `EYE_OPEN_RATIO_THRESHOLD` and other new thresholds were treated
   earlier this session.
6. Store all pairs at/above threshold, with their corroboration flag, in a
   new table.

**New table — `coding_plagiarism_matches`:**
```
id                UUID PK
exam_id           TEXT
question_id       TEXT
teacher_id        UUID          -- same RLS scoping as coding_submissions
submission_a_id   UUID FK -> coding_submissions.id
submission_b_id   UUID FK -> coding_submissions.id
student_a_id      UUID
student_b_id      UUID
similarity_score  DOUBLE PRECISION
matched_regions   JSONB          -- Dolos's matched line/fragment data
corroborated      BOOLEAN        -- behavioral-telemetry signal agrees
status            TEXT DEFAULT 'unreviewed'   -- 'unreviewed' | 'confirmed' | 'dismissed'
reviewed_by       UUID NULL
reviewed_at       TIMESTAMPTZ NULL
created_at        TIMESTAMPTZ DEFAULT now()
```
RLS: identical pattern to `coding_submissions` (own-or-org read/write via
`teacher_id`).

**Runtime dependency:** Dolos needs Node.js + `@dodona/dolos`. Given this
repo already runs a Docker Compose stack, add Dolos as its own
service/image rather than installing Node.js packages onto the Python API
container — same sandboxing philosophy as `execsvc` being its own isolated
service, not bolted into the FastAPI process.

## Teacher-facing UI

New "Plagiarism" sub-section under the existing Results tab in
`dashboard.html` (no existing surface shows coding submissions at all today
— this is the first one):
- **List view:** flagged pairs for the exam — Question, Student A, Student
  B, Similarity %, Corroborated (yes/no badge), Status, sorted by
  similarity descending, corroborated-first.
- **Detail view:** side-by-side code viewer with matched lines/fragments
  highlighted, using the stored `matched_regions` JSONB. External JS file
  (`dashboard-app.js` pattern) — no inline scripts, per this project's CSP
  rule.
- **Teacher actions:** mark a pair "confirmed" or "dismissed" — a judgment
  call the teacher makes; no automatic score/risk-score impact (matches the
  earlier decision to flag for review only, avoiding false-positive damage
  from two students independently writing an obvious simple solution).

## Error handling

Matches this codebase's fail-open philosophy throughout:
- Dolos subprocess missing/crashing/timing out → log it, mark that
  question's check as "failed," never block the exam-close flow or any
  other question's check.
- A question with only 1 submission, or empty/missing source code → skip
  silently, nothing to compare.
- Hard timeout on the subprocess so one hung Dolos run can't block the
  whole worker.
- Realistic class sizes (tens to low hundreds of submissions) are well
  within what Dolos handles cleanly — no scaling concern yet; Dolos's own
  docs flag >1000 files as needing extra care, not a case we hit.

## Testing plan

1. Unit tests: CSV-parsing logic (mocked Dolos output → correct match
   records), threshold filtering, corroboration-flag logic (given known
   paste_attempts/variance values → expected boolean), RLS policy tests
   (same pattern as existing `coding_submissions` tests).
2. Integration test: real Dolos subprocess (not mocked) against two
   near-identical synthetic code files (expect a match) and two genuinely
   different ones (expect no match) — this is the actual detection logic
   that matters, worth testing against the real tool.
3. Manual E2E: trigger an exam close, confirm matches appear correctly in
   the teacher UI, confirm mark-confirmed/dismissed persists.

## Out of scope

- AI-generated-code detection (see above) — explicitly deferred, not
  silently dropped.
- Cross-exam / historical question-bank comparison, and checking against
  public online solutions (LeetCode/GitHub) — both explicitly deferred per
  earlier scoping discussion; this design covers same-exam-only comparison.
- Any automatic score or risk-score impact from a flagged pair — teacher
  review only.
