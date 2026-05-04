"""
Browser-level regression tests for the Procta teacher dashboard.

These cover the two UI bugs that unit tests can't catch:

  Bug 2: Refreshing the dashboard wiped the selected exam, emptying Live
         Sessions until the teacher re-logged. Fix: persist currentExamId
         in localStorage so it survives reload.

  Bug 3: The Tools tab showed a stale "registered students" count because
         the loaders only ran inside refreshAll() (login-time only), never
         on subsequent tab switches. Fix: wire switchTab('tools') to call
         loadRegisteredCount/loadAccessCode/loadSchedule/loadShuffleConfig.

Run against any environment by setting three env vars:
    PROCTA_URL       — dashboard base URL, e.g. https://app.procta.net
    PROCTA_EMAIL     — teacher login
    PROCTA_PASSWORD  — teacher password
The teacher must already have at least 2 exams configured. Use
`tests/browser/seed_demo_data.py` to create them in a staging env.

Headless by default. Pass --headed to see the browser.

    cd tests/browser && pip install -r requirements.txt
    playwright install chromium
    PROCTA_URL=... PROCTA_EMAIL=... PROCTA_PASSWORD=... \\
        pytest test_dashboard_state.py -v
"""
import os
import pytest
from playwright.sync_api import Page, expect


PROCTA_URL = os.environ.get("PROCTA_URL", "").rstrip("/")
PROCTA_EMAIL = os.environ.get("PROCTA_EMAIL", "")
PROCTA_PASSWORD = os.environ.get("PROCTA_PASSWORD", "")


pytestmark = pytest.mark.skipif(
    not (PROCTA_URL and PROCTA_EMAIL and PROCTA_PASSWORD),
    reason="set PROCTA_URL, PROCTA_EMAIL, PROCTA_PASSWORD to run browser tests",
)


def _login(page: Page) -> None:
    """Authenticate the teacher. Leaves the page on the default tab."""
    page.goto(f"{PROCTA_URL}/dashboard")
    # If bfcache restored a valid token the overlay is already hidden;
    # only log in when the login form is visible.
    overlay = page.locator("#auth-overlay")
    if overlay.is_visible():
        page.locator("#login-email").fill(PROCTA_EMAIL)
        page.locator("#login-pwd").fill(PROCTA_PASSWORD)
        page.locator("#login-btn").click()
        expect(overlay).to_be_hidden(timeout=10_000)
    # Wait for the exam picker to populate — it's async after login.
    expect(page.locator("#exam-select option")).not_to_have_count(0, timeout=10_000)


def _exam_option_values(page: Page) -> list[str]:
    return page.locator("#exam-select").evaluate(
        "el => Array.from(el.options).map(o => o.value).filter(Boolean)"
    )


# ─── Bug 2: currentExamId persists across refresh ─────────────────────

class TestExamSelectionPersistence:

    def test_refresh_keeps_selected_exam(self, page: Page):
        """Steps that used to wipe Live Sessions:
            1. Log in (exam_id_A auto-selected by loadExams)
            2. Switch selector to exam_id_B  → onExamSwitch runs
            3. Full page reload
        Expected after fix: selector still shows exam_id_B and Live
        Sessions SSE reconnects for exam B — without re-login."""
        _login(page)
        options = _exam_option_values(page)
        if len(options) < 2:
            pytest.skip("teacher needs at least 2 exams — run seed_demo_data.py")

        target_exam = options[1]
        page.locator("#exam-select").select_option(target_exam)
        # Let onExamSwitch's localStorage write complete.
        page.wait_for_function(
            "id => localStorage.getItem('procta_current_exam') === id",
            arg=target_exam, timeout=3_000,
        )

        page.reload()

        # Auth overlay must NOT reappear — the bug was that live session
        # data only came back after re-login.
        expect(page.locator("#auth-overlay")).to_be_hidden(timeout=10_000)

        # After reload the picker and JS state should both be on target_exam.
        expect(page.locator("#exam-select")).to_have_value(target_exam, timeout=10_000)
        current = page.evaluate("() => window.currentExamId")
        assert current == target_exam, (
            f"currentExamId wiped by reload: got {current!r}, expected {target_exam!r}. "
            "The refresh-wipes-live-sessions bug has regressed."
        )

    def test_logout_clears_persisted_exam(self, page: Page):
        """Logout must drop procta_current_exam; otherwise a second teacher
        on the same browser would inherit the first teacher's exam id."""
        _login(page)
        options = _exam_option_values(page)
        if len(options) < 1:
            pytest.skip("teacher has no exams configured")

        page.locator("#exam-select").select_option(options[0])
        page.wait_for_function(
            "() => localStorage.getItem('procta_current_exam')", timeout=3_000,
        )

        page.evaluate("() => doLogout && doLogout()")
        # doLogout() reloads the page; wait for the login form to return.
        expect(page.locator("#auth-overlay")).to_be_visible(timeout=10_000)
        stored = page.evaluate("() => localStorage.getItem('procta_current_exam')")
        assert not stored, f"stale exam id left in localStorage after logout: {stored!r}"


# ─── Bug 3: Tools tab refreshes on every switch ───────────────────────

class TestToolsTabRefresh:

    def test_switching_to_tools_calls_registered_count(self, page: Page):
        """Regression: opening the Tools tab must hit GET /registered-count
        (and the other tools endpoints) every time, not just at login.

        Strategy: intercept the network call. Before the fix this request
        would only fire during refreshAll() at login; clicking the Tools
        tab afterward produced no request, so the count stayed stale."""
        _login(page)

        # Start on a non-tools tab so the test exercises the click path.
        page.locator('.tab[data-tab="results"]').click()

        requests_seen: list[str] = []
        page.on("request", lambda req: requests_seen.append(req.url)
                if "/api/admin/registered-count" in req.url else None)

        page.locator('.tab[data-tab="tools"]').click()
        # Give the async fetch a moment.
        page.wait_for_function(
            """() => {
                const el = document.getElementById('tools-registered');
                return el && el.textContent && el.textContent !== '--';
            }""",
            timeout=10_000,
        )

        assert any("/api/admin/registered-count" in u for u in requests_seen), (
            "Tools tab switch did not trigger loadRegisteredCount — the "
            "stale-count bug has regressed. switchTab('tools') branch missing?"
        )

    def test_switching_away_and_back_refetches(self, page: Page):
        """Sharper version: switching away then back must re-fetch, so a
        teacher who registers a new student in another tab and returns
        sees the updated count."""
        _login(page)

        page.locator('.tab[data-tab="tools"]').click()
        expect(page.locator("#tools-registered")).not_to_have_text("--", timeout=10_000)

        count = {"n": 0}
        def _on_req(req):
            if "/api/admin/registered-count" in req.url:
                count["n"] += 1
        page.on("request", _on_req)

        page.locator('.tab[data-tab="results"]').click()
        page.locator('.tab[data-tab="tools"]').click()

        # Wait up to 5s for the second fetch.
        page.wait_for_function(
            "expected => window.__countedRequests === undefined || true",
            arg=1, timeout=1_000,
        )
        # Poll manually since the counter lives in Python.
        import time
        for _ in range(50):
            if count["n"] >= 1:
                break
            time.sleep(0.1)

        assert count["n"] >= 1, (
            "second Tools-tab visit did not re-fetch registered-count — "
            "loadRegisteredCount is only being called once"
        )
