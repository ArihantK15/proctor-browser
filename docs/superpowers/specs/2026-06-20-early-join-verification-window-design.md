# Early-Join Pre-Exam Verification Window — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorm), pre-implementation

## Goal

For a scheduled exam, let students enter early to complete ID + phone-camera
verification, then wait in the lobby with a live countdown until the scheduled
start, at which point they click **Begin**. Produces a real exam-hall flow:
arrive early → verify → wait → everyone begins around the same moment.

## Approved decisions

1. **Start trigger:** manual **Begin** that *unlocks* at `starts_at` (countdown until then). No forced auto-redirect (auto-start is a possible future toggle).
2. **Timer:** per-student duration starts when the student clicks Begin (matches the existing engine). `ends_at` still caps the hard close.
3. **Early-join lead time:** per-exam setting `early_join_minutes`, default 15.
4. **Late join** (`now >= starts_at`): straight to verification → Begin, no wait. The window only gates *early* entry, never blocks a normal/late join.

## Data model

- New exam column: `early_join_minutes INT NOT NULL DEFAULT 15`.
  - Migration: next free `phaseNNN_early_join_window.sql` (EXCEPTION-handled DO block, per house style). Confirm the real next number at build (memory's "96" is stale; ls shows ≥phase99, likely ≥130).
  - Add to `admin_exams.py` config SELECT list + save/load (`starts_at`/`ends_at` already there).
  - Semantics: `0` (or no `starts_at`) ⇒ behaves exactly like today (no early window). Non-zero only matters when `starts_at` is set.

## Backend (`app/routers/exam.py`)

Split `_check_exam_time_window` into two single-purpose gates:

- `_check_lobby_window(config)` — called by `validate_student` (replaces the current call at exam.py:183):
  - 403 if `now < starts_at - early_join_minutes` → "The exam lobby opens at {lobby_open}. Please come back then."
  - 403 if `now > ends_at` → existing "window has closed" message.
  - Otherwise allow (this is the early window).
- `_check_exam_started(config)` — called by `GET /api/v1/questions` (insert right after `config = await _load_exam_config(...)`, ~exam.py:622):
  - 403 if `now < starts_at` → "The exam begins at {starts_at}. Please wait — you can finish verification meanwhile."
  - 403 if `now > ends_at` (defensive).
- No `starts_at` ⇒ both gates are no-ops (unchanged for unscheduled exams).

`_build_validate_response` (exam.py:565) gains: `starts_at`, `ends_at`,
`early_join_minutes`, and `server_now` (ISO UTC) so the client renders an
**authoritative** countdown off server time, not the local clock.

## Client (Electron lobby + student app)

- The validate response now carries `starts_at` + `server_now`. After
  verification completes:
  - `now < starts_at`: show "Verified ✓ — exam begins in MM:SS", Begin disabled, live countdown (tick off `server_now` + elapsed since fetch, so a wrong local clock can't unlock early).
  - at `starts_at`: enable Begin / "You may begin".
  - `now >= starts_at` (late): Begin enabled immediately, no countdown.
- Join screen: if a student opens before the lobby window, the validate 403
  surfaces "lobby opens at {lobby_open}".
- Exact placement: the lobby window (`lobby_preload.js` / lobby HTML) already
  sits between join and the kiosk exam window — the countdown lives there.
  The Begin action gates `launchExam`.

## Dashboard (`dashboard-app.js` schedule UI)

- In the schedule editor (`loadSchedule`/`saveSchedule`), add an input:
  "Allow students to join ___ minutes early (for verification)", default 15,
  saved alongside `starts_at`/`ends_at`. Only meaningful when a start time is set.

## Testing

- **Backend unit (pytest)** — the high-value, docker-free core:
  - lobby gate: blocks before `starts-early`; allows in `[starts-early, starts)`; allows at/after starts; `ends_at` still closes.
  - questions gate: 403 before `starts_at`; allows at/after.
  - no `starts_at` ⇒ both no-ops.
  - `early_join_minutes=0` ⇒ lobby gate == start gate (today's behavior).
  - validate response carries the new fields.
- **Live (if budget allows):** docker-compose up, schedule an exam ~3 min out
  with early_join=10, confirm validate succeeds early, questions 403s until
  start, countdown renders.

## Out of scope

- Auto-start at scheduled time (future toggle).
- Common-clock-for-all timer (rejected: more work, penalizes late verifiers).

## Build order

1. Migration + `admin_exams` config plumbing.
2. Backend gate split + validate-response fields + pytest.
3. Dashboard schedule setting.
4. Electron lobby countdown + Begin gating.
5. Live docker smoke (optional).
