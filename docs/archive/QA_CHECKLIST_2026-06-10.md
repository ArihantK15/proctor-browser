# Manual QA Checklist — uncommitted batch (2026-06-10)

Backend is covered by 894 automated tests. This checklist targets the **UI/runtime
surfaces tests can't reach**. Click through each on a deployed/dev build.

## Setup you'll need
- **Two teachers in the same org** (Teacher A, Teacher B) + **one Org Admin** for
  that org — to test the tenancy roll-up.
- **One Superadmin** account — to test the monitor-only lockdown.
- A real **question-paper PDF** (typed, with an answer key — e.g. a JEE/MathonGo
  paper) and a **study-guide PDF/DOCX/PPTX** (prose notes).
- `LLM_API_KEY` set on the server (for the AI-generation tests only).

---

## 1. Question Bank — Import from PDF/Word
Dashboard → Questions → AI/Bank panel → **Import** area.

- [ ] Click **"Import from PDF/Word"** → file picker opens; pick a typed PDF.
- [ ] Spinner shows "Reading your document… on-device".
- [ ] Review modal opens with a summary line ("N found — X ready, Y need attention").
- [ ] Clean MCQs are **pre-checked**; flagged rows are highlighted with a reason
      (`no_answer`, `few_options`, etc.) and their checkbox is disabled.
- [ ] Math-heavy/diagram questions show an **image thumbnail** (preserved as image).
- [ ] Edit a flagged row's stem/options/correct → row **unlocks** (highlight clears,
      checkbox enables) once valid.
- [ ] **"Add to bank"** is disabled until at least one valid row is checked.
- [ ] Click **Add to bank** → success message, modal closes, **bank list refreshes**
      with the new questions.
- [ ] Upload a **scanned/image-only PDF** → friendly "scanned PDF not supported yet".
- [ ] Upload a **.txt** → "Only PDF and Word (.docx) supported".
- [ ] Upload a **DOCX** of questions → extracts and reviews the same way.

## 2. Numeric / Integer (range) question type
Dashboard → Questions → editor.

- [ ] Add a question → type dropdown now has **"Numeric / Integer (range)"**.
- [ ] Select it → options disappear, **Min / Max** number inputs appear.
- [ ] Enter min=9.75, max=9.85 → no validation error on Save.
- [ ] Leave min or max blank → **Save** shows "needs a min and max value".
- [ ] Enter min>max → "min must be ≤ max".
- [ ] Toggle **Preview** → numeric question shows "Student types a number. Accepted:
      9.75 to 9.85".
- [ ] The question-list **type filter** has a **"Numeric"** chip that filters correctly.
- [ ] Save, reload the page → the numeric question persists with its range intact.
- [ ] **Student exam (renderer):** start an exam containing a numeric question →
      a **number input** appears (not radio options). Type a value within range →
      submit → scored **correct**. Repeat with a value outside range → **incorrect**.
- [ ] A numeric answer of **"0"** is accepted/counted as answered (not treated empty).

## 3. AI — Generate questions from notes  *(needs LLM key)*
Dashboard → Questions → AI/Bank panel → **Generate** pane.

- [ ] The intro line shows the disclosure: "AI features send your text to our AI provider."
- [ ] Set count/difficulty → click **"Generate from file"** → pick a PDF/DOCX/PPTX of notes.
- [ ] Status: "Reading your file and calling AI…".
- [ ] Generated questions render as **editable preview cards** (same as topic-generate).
- [ ] If the file is large (>~20k chars) → notice "Used the first ~15 pages…".
- [ ] Edit + **Add to Bank** → questions land in the bank.
- [ ] PPTX with speaker notes → content from notes is reflected in generated questions.
- [ ] Upload a near-empty file → "Couldn't find enough text…" (422).

## 4. Tenancy roll-up (Org Admin viewing co-teacher data)
Log in as **Org Admin**; pick **Teacher B** in the teacher filter.

- [ ] **Results** tab shows Teacher B's sessions; open one → **Scorecard** and
      **Report (PDF)** download successfully.
- [ ] On a result row, open the **answer-detail** modal → answers + correctness
      show (this was a gap — must NOT be empty for a co-teacher's session).
- [ ] **Screenshots/evidence** in the timeline load (not broken images).
- [ ] On a **live** co-teacher session, open **Insight / triage** → it loads
      (was a gap — must not 404).
- [ ] As a **plain teacher** (Teacher A), confirm you still see **only your own**
      sessions and data — no cross-teacher leakage.

## 5. Live-control policy
- [ ] As **Teacher A** (owner): pause / resume / warn / reset / force-submit /
      recalibrate / **terminate** your own live session → all work.
- [ ] As **Org Admin** on **Teacher B's** live session: **terminate** works
      (emergency recovery) — and the forensic timeline records it was an **admin**
      (not owner) who terminated.
- [ ] As **Org Admin** on Teacher B's session: pause / reset / warn / force-submit /
      recalibrate → **blocked** (404/not allowed). Only terminate is shared.

## 6. Superadmin = monitor-only
Log in as **Superadmin**.

- [ ] Dashboard loads; you can **view** flags + active sessions (reads work).
- [ ] Any **write/action** button (terminate, approve room-cam, save question,
      add member, confirm grade, resolve issue, etc.) → **403 "Superadmin is
      monitor-only"** (it should fail, by design).
- [ ] You can still **log in, refresh, and log out** (identity routes exempt).
- [ ] **Chat:** superadmin cannot open/send in the teacher chat (connection rejected).

## 7. Regression spot-checks (shared code we touched)
- [ ] **Existing topic-based Generate** (no file) still works end-to-end.
- [ ] **Existing CSV/JSON bank import** still works.
- [ ] A **normal MCQ exam** grades correctly (we changed the grader for ranges —
      confirm letter answers still score right).
- [ ] **Exam switch** (change selected exam) refreshes the active tab's data
      without needing a manual tab toggle.
- [ ] Question editor: existing MCQ / multi / true-false / short-answer types
      all still author + save normally.

---

**Priority order if time is short:** 7 (regression) → 1 (PDF import) → 2 (numeric
exam+grading) → 6 (superadmin lockdown) → 4 (tenancy) → 5 (control) → 3 (AI gen).
