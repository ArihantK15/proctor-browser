# Procta — Pending Steps & Backlog

Single source of truth for everything that's been *built but not deployed* and
everything that's been *deferred but acknowledged*. Update as items move.

---

## 1. Deploy steps (consolidated, do in one go)

These accumulate from the recent feature sprint. None are individually urgent;
batch them whenever you do the next prod push.

### 1.1 Database migrations

```bash
psql "$DB_URL" -f migrations/phase10_invite_clicks.sql
psql "$DB_URL" -f migrations/phase11_scorecard_insight.sql
```

The others (groups, question_bank, scorecard_emailed, invite_reminders,
student_invites) are already applied — their endpoints have been live
for a while.

### 1.2 Environment variables

Add to `.env` on the droplet:

```
GROQ_API_KEY=gsk_...     # for AI question generation + auto-tag
```

Optional overrides (leave unset to use defaults):
```
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_TIMEOUT=30
```

If `GROQ_API_KEY` is missing, AI features return a clean 503 — they don't
break anything else. Safe to deploy without setting it first.

### 1.3 Third-party config (Resend dashboard)

For `mail.procta.net`:
- **Track clicks** — confirm ON (it's usually on by default; verify in
  Resend → Domains → mail.procta.net → settings)
- **Track opens** — optional. If you turn it on, expect noisy data: Outlook
  blocks pixels, Apple Mail pre-fetches them. The Clicked column on the
  invites dashboard is a more reliable signal regardless.

### 1.4 Container restart

```bash
docker compose build api && docker compose up -d api
```

### 1.5 Smoke tests after deploy

| Feature | Test |
|---|---|
| Click tracking | Send invite → click link → verify Clicked column populates within seconds |
| Started column | Click invite → log into exam → verify Started column populates |
| AI question generation | Question Bank → ✨ Generate → topic "photosynthesis" → 5 questions → confirm preview |
| AI auto-tag | Save to Bank on any question → verify suggested tags appear pre-filled |
| Scorecard insights | Generate a scorecard PDF — should include a "Personalised Note" section with 2-4 sentences. Second download for the same session should be instant (cached). |
| Excel injection guard | Add a student named `=cmd\|'/c calc'!A1`, export Excel, open — should show literal string with leading apostrophe |

---

## 2. Deferred hardening items

Surfaced in the audit, deliberately not fixed in the hardening pass. Each is
real but the cost/benefit didn't justify shipping in a fast sprint. Listed in
priority order.

### 2.1 Scorecard claim race on hard-kill — MED

**Where:** `app/main.py` in `email_all_scorecards` (~line 3920).

**Problem:** The bulk endpoint claims `scorecard_emailed_at` *before* sending.
If the worker is SIGKILL'd between claim and send, the row stays claimed
forever — student never gets their PDF, and re-runs skip them as
"already_sent". Graceful failures (PDF build error, send error) DO roll back
correctly; only worker-kill mid-send is the hole.

**Fix:** Add a `scorecard_claim_at timestamptz` column. Use it as the racey
claim sentinel. Stamp the real `scorecard_emailed_at` only after send
success. Add a 5-min TTL recovery clause to the claim query so stuck claims
get retried automatically.

**Effort:** ~30 lines + 1 migration. Skipped because it requires a schema
change and worker-kill mid-send is genuinely rare in normal operation.

### 2.2 ChatHub per-tenant socket cap + idle eviction — MED

**Where:** `app/main.py` `ChatHub` class (~line 4083).

**Problem:** `teacher_conns[tid]` and `student_meta` are unbounded sets.
Pruning happens only on send failure — an idle leaked socket (student
closed laptop lid, OS hasn't torn the TCP) accumulates indefinitely.

**Fix:** 
- Add `MAX_TEACHER_SOCKETS_PER_TENANT = 50` constant; reject new connections
  past the cap with a 1008 close.
- Add a heartbeat ping every 30s; close connections that don't respond
  within 60s.
- TTL-evict `student_meta` entries older than 4h.

**Effort:** ~80 lines. Skipped because it needs careful testing under load
and current usage is well under any realistic cap.

### 2.3 ~~Resend transport retry with backoff~~ — DONE

Shipped: 3 attempts with exponential backoff (0.5 s, 1.0 s) on transport
errors, 429, and 5xx. 4xx fails fast.

### 2.4 ~~localStorage debounce in renderer~~ — DONE

Shipped: 400 ms debounce on `_persistAnswers`, with `_persistAnswersNow`
synchronous flush wired into `doBulkSave`, `beforeunload`, and `pagehide`
so we never lose the last keystroke.

### 2.5 sessionId orphan recovery in renderer — LOW/MED

**Where:** `renderer/index.html:678` (sessionId generation).

**Problem:** `sessionId = roll + Date.now()` — if Electron crashes mid-exam
and the student re-launches, the new sessionId doesn't match the old
localStorage key, so `_mergeLocalAnswers` returns nothing. Their offline
answers are silently lost.

**Fix:** On exam start, scan `localStorage` for any `answers_<roll>_*` keys,
take the most-recent timestamp, merge in. Add a cleanup step that removes
keys older than 7 days to bound storage growth.

**Effort:** ~25 lines, no schema. Skipped because it's a rare crash-resume
edge case.

### 2.6 Naive `datetime` in `proctor.py` — LOW

**Where:** `app/proctor.py:189, 728`.

**Problem:** Uses `datetime.now()` (naive, local tz) and `datetime.utcnow()`
(naive UTC). If ever compared against a tz-aware datetime, raises
`TypeError`.

**Fix:** Replace with `datetime.now(timezone.utc)` everywhere.

**Effort:** 2-line change. Skipped because the code paths don't currently
compare to aware datetimes — it's pre-emptive only.

### 2.7 Streaming Excel/PDF for huge exports — LOW

**Where:** `app/main.py` `/api/export-excel` and `scorecard-zip`.

**Problem:** Both build the full output in `BytesIO` before streaming.
A 1000-session export can hold ~500 MB temporarily.

**Fix:** Use openpyxl `write_only=True` workbook + chunked iter. For ZIP,
write each PDF directly to the response stream rather than buffering.

**Effort:** ~40 lines per format. Skipped because the bulk caps (1000
sessions, 500 questions) keep memory usage well under the worker's 1.2 GB
limit at current scale.

### 2.8 Cross-tenant roll_number collision — LOW

**Where:** `app/main.py:1497` `validate_student`.

**Problem:** `students` is queried by `roll_number` alone. If two teachers
both have a student with roll "STU-001", the first row wins. The
access_code path mitigates it (different teachers' codes won't match), but
the underlying lookup is global.

**Fix:** Make roll_number teacher-scoped. Either (a) require an additional
disambiguator on the login form, or (b) chain validation through the
invite token. Option (b) aligns with where the product is going.

**Effort:** Bigger lift — schema + login UX changes. Skipped because no
real customer has hit this collision yet.

---

## 3. Feature backlog

Things we discussed but haven't built. Ranked by value-per-effort.

### 3.1 Short-answer question type with AI grading

**What:** New `question_type='short_answer'`. Teacher writes a reference
answer + grading rubric. On submit, Groq compares student response to
rubric and assigns score (0/partial/full). Teacher sees AI-suggested grade
and can confirm or override.

**Why now:** Unlocks essay-style exams. Currently the platform only
supports MCQ — a major limitation for any humanities or coding teacher.

**Why not yet:** The trust gradient. Teachers need to *see* AI grades
being right before they'll let them auto-finalize. Ship with a
"AI-suggested, teacher-confirmed" UI first; auto-finalize later.

**Effort:** ~250 LOC: question type in schema + editor UI, grading
endpoint reusing `app/llm.py`, scorecard rendering, teacher review
workflow.

### 3.2 Live risk triage on dashboard

**What:** For each live session, a one-line LLM-generated TL;DR:
"Looked away 14 times in 5 min, all during Q3-Q5". Translates raw
violations into something a teacher can scan during a live exam.

**Effort:** ~50 LOC. Cache 60s per session.

### 3.3 Question quality lint (pre-publish)

**What:** Before "Publish exam", run each question through Groq:
"Is this ambiguous? Are options balanced? Is the correct answer
actually correct?" Returns warnings, not errors.

**Effort:** ~80 LOC.

### 3.4 Mobile app

**Why:** Schools increasingly want students taking exams from phones
(BYOD). Electron doesn't help here.

**Effort:** Substantial. React Native or Flutter rewrite of the
renderer. Camera + face detection are the main complexity.

### 3.5 Billing / subscription tier

**Why:** Cannot sell to schools without it.

**Effort:** Stripe integration + usage tracking + plan gates on the
dashboard.

---

## 4. Notes / parking lot

- **Outlook + Apple Mail tracking suppression:** Recipients on these
  clients won't reliably register opens. The Clicked + Started columns
  on the invites table are the canonical engagement signals — opens
  remain on the dashboard for completeness but should be treated as
  noisy.
- **Safari custom-scheme alert:** "Open in Procta app" still shows
  "Safari can't open the page" if Procta isn't installed. There's no
  JS API to detect protocol registration on Safari. Current solution
  is UX softening (muted button + explicit warning text). Don't try
  to fix the underlying alert again — the platform doesn't allow it.
