# Procta Browser Regression Tests

End-to-end tests that exercise the teacher dashboard in a real Chromium
instance via Playwright. They catch UI bugs that unit tests can't —
specifically the two that shipped to prod in April 2026:

| File | Covers | Bug |
|---|---|---|
| `test_dashboard_state.py::TestExamSelectionPersistence` | exam selector survives page reload | Bug 2: refresh wiped live sessions |
| `test_dashboard_state.py::TestToolsTabRefresh` | Tools tab re-fetches counts on every open | Bug 3: stale registered-student count |

The API-level regression (Bug 1: pending-ID filter) lives one level up in
`tests/test_pending_verifications_filter.py` and runs with mocked
Supabase — no browser, no credentials, no network.

## Prerequisites

1. A running Procta instance (local docker-compose, staging droplet, etc.)
2. One teacher account with a known password
3. At least 2 exams under that teacher (use the seed script below)

## Setup

```bash
cd tests/browser
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Seed multi-exam test data

The browser tests skip themselves if the teacher has fewer than 2 exams,
because the bugs they cover are invisible in a single-exam setup. Run
this once per environment:

```bash
PROCTA_URL=https://staging.procta.net \
PROCTA_EMAIL=qa@procta.net \
PROCTA_PASSWORD='your-password' \
STUDENTS_PER_EXAM=5 \
    python seed_demo_data.py
```

The script is idempotent — reruns are safe, duplicates get a 409 which
is swallowed. It creates:

- 2 exams: "QA Exam A" (45 min), "QA Exam B" (60 min)
- 5 students per exam tag (`QAA001…QAA005`, `QAB001…QAB005`)

## Run the browser tests

```bash
PROCTA_URL=https://staging.procta.net \
PROCTA_EMAIL=qa@procta.net \
PROCTA_PASSWORD='your-password' \
    pytest test_dashboard_state.py -v

# watch the browser if you want to debug a failure
pytest test_dashboard_state.py -v --headed --slowmo 400
```

## Run the API regression (no browser needed)

```bash
cd ..                       # back up to tests/
pytest test_pending_verifications_filter.py -v
```

That suite uses the existing `conftest.py` mocks — no env vars, runs
in ~1s, safe to wire into CI on every push.

## Suggested CI wiring

1. **On every push / PR** — run API tests only. No credentials, no flake:
   ```yaml
   - run: pytest tests/ -v --ignore=tests/browser
   ```

2. **On merge to main** (or nightly) — run browser tests against staging:
   ```yaml
   - run: pip install -r tests/browser/requirements.txt
   - run: playwright install --with-deps chromium
   - run: pytest tests/browser -v
     env:
       PROCTA_URL:      ${{ secrets.STAGING_URL }}
       PROCTA_EMAIL:    ${{ secrets.QA_TEACHER_EMAIL }}
       PROCTA_PASSWORD: ${{ secrets.QA_TEACHER_PASSWORD }}
   ```

## What each test locks in

### `test_refresh_keeps_selected_exam`
Logs in → selects the second exam → full `page.reload()` →
asserts the auth overlay stays hidden, the `<select>` retains the value,
and `window.currentExamId` matches. Fails the moment anyone drops
`localStorage.setItem('procta_current_exam', …)` from `onExamSwitch`.

### `test_logout_clears_persisted_exam`
Mirror test — logout must remove the key. Prevents "user-B sees user-A's
exam id" on shared browsers.

### `test_switching_to_tools_calls_registered_count`
Starts on the Results tab, attaches a network listener, clicks Tools,
asserts `/api/admin/registered-count` fired. Fails if `switchTab('tools')`
ever loses its loader branch again.

### `test_switching_away_and_back_refetches`
Tools → Results → Tools. Second visit must re-fetch. Catches the subtler
"loads once but not on revisit" regression.
