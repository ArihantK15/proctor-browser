# Coding-question authoring form — dashboard (Phase 5 UI) spec

The Phase 5 BACKEND is done (`app/routers/admin_coding.py`, `llm.generate_coding_question`).
This spec is the remaining DASHBOARD form that teachers use to create coding questions
(no more SQL seeding). DeepSeek builds it; Arihant reviews.

## Endpoints to call (already built + tested — do NOT change them)
- **Create/replace:** `POST` (create) or `PUT` (replace) `/api/v1/admin/coding-question`
  body: `{exam_id, question, options:{allowed_languages[],marks,marks_policy,time_limit_ms,starter_code}, test_cases:[{input,expected_output,visibility,float_tolerance?}], question_id?}`
  - `question` = problem statement (markdown). `question_id` only for replace.
  - Returns `{question_id, exam_id, replaced, test_cases, sample, hidden}`.
  - Server rules (mirror in client validation): `allowed_languages` ⊆ {javascript,typescript,python}, non-empty; `marks` 1..100; `marks_policy` ∈ {partial, all_or_nothing}; **≥1 hidden test case**; every case needs `expected_output`; ≤50 cases.
- **Read (for editing):** `GET /api/v1/admin/coding-question?question_id=` → `{question_id,exam_id,question,options,test_cases}` (incl. hidden expected — teacher view).
- **AI draft:** `POST /api/v1/admin/coding-question/generate` body `{topic, difficulty?, language?, grade_level?}`
  → `{question, options, reference_solution, test_cases, ai_generated:true, needs_verification:true}` (exact shape the create endpoint accepts). `503` if AI unconfigured, `400` without topic.

## Keying (important)
The server **mints** `question_id` on create — the client does NOT set it. The whole
coding chain keys on that label (renderer/judge/scoring). On replace, pass the existing
`question_id` from GET.

## Form behaviour
1. Entry point: in the exam editor, a "**+ Coding question**" action opening a coding
   authoring panel/modal (mirror the existing question-bank/exam-question editor + the
   AI-generate flow at `dashboard-app.js` ~`/question-bank/generate`).
2. Fields: statement (textarea, markdown), language multi-select (js/ts/python), marks,
   marks_policy (partial/all-or-nothing), time_limit_ms, starter_code (textarea),
   and a **test-case grid**: rows of {input, expected_output, visibility(sample/hidden)},
   add/remove rows. Show a live "needs ≥1 hidden" hint.
3. **"Generate with AI"** button: prompt for a topic (+ difficulty/language), call
   `/generate`, prefill the whole form from the draft, and show a clear
   **"AI-drafted — verify expected outputs before saving"** banner (the draft includes
   `reference_solution` — display it read-only so the teacher can sanity-check).
4. **Save**: client-validate (same rules as server), then POST (or PUT if editing) the
   authoring endpoint. On success, show the minted `question_id` + counts; the coding
   question now appears in that exam.
5. Edit existing: GET to prefill, PUT to replace.

## Hard constraints
- **CSP: dashboard is server-served under `script-src 'self'` (NO unsafe-inline).** All
  JS in `app/static/dashboard-app.js`; wire events via the delegated `data-action` /
  `data-change-action` pattern (see `_resolveDelegatedAction`) — **no inline `onclick`**.
  Markup in `app/static/dashboard.html`. (Dashboard is HTML/JS only — never `app/dashboard-ui/**`.)
- **No code execution in the dashboard.** Expected outputs are typed by the teacher or
  AI-drafted; the form does NOT run code (avoids the worker/eval CSP + RCE concerns).
  "Run the reference to auto-verify expected" is a FUTURE enhancement (needs the runtime
  in the dashboard) — out of scope here.
- Don't auto-publish AI drafts — they land in the editable form for review first.

## Tests
- Frontend: manual/structural (load dashboard, open the form, generate, save) — note it.
- Reuse the backend tests (`tests/test_admin_coding.py`) — don't duplicate server logic.
