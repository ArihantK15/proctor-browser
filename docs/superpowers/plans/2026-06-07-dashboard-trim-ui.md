# Dashboard Trim (UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **BUILD ONLY — never commit/stage/push. The user commits.**

**Goal:** Make the legacy dashboard render the right experience per account type — a solo teacher sees zero org/admin chrome, an institute admin sees the org roll-up, and founder-internal superadmin tooling never ships into a customer's DOM.

**Architecture:** Add an `is_solo` signal computed server-side (solo = non-superadmin caller whose org has ≤1 member, or who has no org at all) and surface it on the two teacher payloads the dashboard already consumes (`/api/v1/auth/me` and the login response). The client's existing `applyOrgRole()` gate honors `is_solo` by forcing the teacher view regardless of stored `org_role`. Separately, hard-gate the three superadmin tabs (`all-orgs`, `issues`, `debug`) by physically removing them from the DOM for any non-superadmin and refusing hash/keyboard routing into hidden tabs — reversible (no superadmin code deleted), matching the spec's "hard gate now, separate build later" decision.

**Tech Stack:** FastAPI (asyncpg-backed Postgres, accessed via `app.database.async_table`), pytest + pytest-asyncio (STRICT mode — every async test needs `@pytest.mark.asyncio`), vanilla ES (no JS unit-test harness for `dashboard-app.js`; client tasks verify via `npm run lint:py`-style checks, eslint, and explicit DOM reasoning).

**Spec:** `docs/superpowers/specs/2026-06-07-dashboard-trim-tenancy-design.md` (sections B + C). Section B item 3 — the admin teacher-picker — is **already implemented** (`.teacher-filter` selects, `loadOrgMembers()`, `applyTeacherFilter()` in `dashboard-app.js`) and the exam-edit affordances are already teacher-only (`data-roles="teacher"` on Questions/Chat/Tools tabs), so neither is re-built here.

---

## File Structure

**Server**
- `app/auth/scope.py` — **Modify.** Add a pure `compute_is_solo(org_role, org_id, member_count)` helper and an async `org_is_solo(teacher)` wrapper that counts org members. Co-located with the existing org-scope spine; already imports `from ..database import async_table as _atable`.
- `app/routers/auth.py` — **Modify.** Surface `is_solo` in the `/api/v1/auth/me` payload (`:889`) and the login response teacher dict (`:868`).
- `tests/test_is_solo.py` — **Create.** Unit tests for `compute_is_solo` (pure) and `org_is_solo` (async, monkeypatched `_atable`).

**Legacy dashboard (client)**
- `app/static/dashboard-app.js` — **Modify.** `applyOrgRole()` (`:1110`) honors `is_solo`; `_onAuthDone()` (`:1155`) / `_onAuthed()` (`:218`) capture `is_solo`; `_tabButtonForName()` (`:1030`) refuses hidden tabs; `applyOrgRole()` removes superadmin-only tabs/panels from the DOM for non-superadmins.

No HTML edits are required: the superadmin tabs (`dashboard.html:351-353`) and panels (`#panel-all-orgs`, `#panel-debug`, issues panel) already carry `data-roles="superadmin"` + `style="display:none"`; the client task removes them from the DOM at runtime for non-superadmins, which is reversible and keeps superadmin behavior intact.

---

## Task 1: `is_solo` computation helpers (server)

**Files:**
- Modify: `app/auth/scope.py`
- Test: `tests/test_is_solo.py` (create)

The rule: **superadmin is never solo** (they have cross-org tooling and usually no `org_id`); a caller with **no `org_id`** is solo (a lone teacher account); otherwise solo iff the org has **≤1 member**.

- [ ] **Step 1: Write the failing test for the pure helper**

Create `tests/test_is_solo.py`:

```python
"""Unit tests for the is_solo signal (two-mode dashboard gate).

Solo = a non-superadmin caller who is effectively alone: no org, or an
org with a single member. Drives whether the legacy dashboard shows any
org/admin chrome at all (spec section B, two-mode UI).
"""
from __future__ import annotations

import pytest

import app.auth.scope as scope_mod


# ── pure helper: compute_is_solo(org_role, org_id, member_count) ──

def test_superadmin_is_never_solo():
    # member_count irrelevant; superadmin keeps full chrome
    assert scope_mod.compute_is_solo("superadmin", None, 1) is False
    assert scope_mod.compute_is_solo("superadmin", "orgA", 5) is False


def test_no_org_is_solo():
    assert scope_mod.compute_is_solo("teacher", None, 0) is True
    assert scope_mod.compute_is_solo("admin", None, 0) is True


def test_single_member_org_is_solo():
    assert scope_mod.compute_is_solo("admin", "orgA", 1) is True
    assert scope_mod.compute_is_solo("teacher", "orgA", 1) is True


def test_multi_member_org_is_not_solo():
    assert scope_mod.compute_is_solo("admin", "orgA", 2) is False
    assert scope_mod.compute_is_solo("teacher", "orgA", 3) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: FAIL with `AttributeError: module 'app.auth.scope' has no attribute 'compute_is_solo'`

- [ ] **Step 3: Implement the pure helper in `app/auth/scope.py`**

Add near the top of the module (after the imports, before `resolve_scope`):

```python
def compute_is_solo(org_role: str | None, org_id, member_count: int) -> bool:
    """Two-mode dashboard signal.

    Solo = a non-superadmin caller who is effectively alone, so the legacy
    dashboard should show a pure-teacher view with zero org/admin chrome:
      • superadmin → never solo (cross-org tooling; usually no org_id);
      • no org_id  → solo (a lone teacher account);
      • org with ≤1 member → solo;
      • otherwise → not solo (institute account).
    """
    if org_role == "superadmin":
        return False
    if not org_id:
        return True
    return member_count <= 1
```

- [ ] **Step 4: Run the pure-helper tests to verify they pass**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing test for the async wrapper**

Append to `tests/test_is_solo.py`:

```python
# ── async wrapper: org_is_solo(teacher) — counts org members ──

class _CountChain:
    """Stub _atable() chain that returns a fixed member list for
    .select(...).eq('org_id', X).execute()."""
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    async def execute(self):
        return type("R", (), {"data": self._rows})()


@pytest.mark.asyncio
async def test_org_is_solo_superadmin_short_circuits(monkeypatch):
    # superadmin must NOT hit the DB and must be non-solo
    def _boom(_table):
        raise AssertionError("org_is_solo must not query for superadmin")
    monkeypatch.setattr(scope_mod, "_atable", _boom)
    teacher = {"id": "x", "org_id": "orgA", "org_role": "superadmin"}
    assert await scope_mod.org_is_solo(teacher) is False


@pytest.mark.asyncio
async def test_org_is_solo_no_org_short_circuits(monkeypatch):
    def _boom(_table):
        raise AssertionError("org_is_solo must not query when org_id is empty")
    monkeypatch.setattr(scope_mod, "_atable", _boom)
    teacher = {"id": "t1", "org_id": None, "org_role": "teacher"}
    assert await scope_mod.org_is_solo(teacher) is True


@pytest.mark.asyncio
async def test_org_is_solo_counts_members(monkeypatch):
    # 1 member → solo
    monkeypatch.setattr(scope_mod, "_atable", lambda t: _CountChain([{"id": "a1"}]))
    teacher = {"id": "a1", "org_id": "orgA", "org_role": "admin"}
    assert await scope_mod.org_is_solo(teacher) is True
    # 2 members → not solo
    monkeypatch.setattr(scope_mod, "_atable",
                        lambda t: _CountChain([{"id": "a1"}, {"id": "a2"}]))
    assert await scope_mod.org_is_solo(teacher) is False
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: FAIL with `AttributeError: module 'app.auth.scope' has no attribute 'org_is_solo'`

- [ ] **Step 7: Implement the async wrapper in `app/auth/scope.py`**

Add directly below `compute_is_solo`:

```python
async def org_is_solo(teacher: dict) -> bool:
    """Resolve is_solo for a teacher dict (as returned by require_admin).

    Short-circuits for superadmin and org-less accounts so we only hit the
    DB for the genuine institute-vs-solo distinction. Counts org members
    by selecting their ids — the org member set is tiny (seat-limited), so
    a count-by-fetch is cheap and avoids a separate COUNT round-trip.
    """
    org_role = teacher.get("org_role", "teacher")
    org_id = teacher.get("org_id")
    if org_role == "superadmin" or not org_id:
        return compute_is_solo(org_role, org_id, 0)
    rows = (await _atable("teachers").select("id")
            .eq("org_id", str(org_id)).execute()).data or []
    return compute_is_solo(org_role, org_id, len(rows))
```

- [ ] **Step 8: Run all Task 1 tests to verify they pass**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: PASS (7 passed)

- [ ] **Step 9: Self-review (HARD RULE — do not commit)**

Re-read `app/auth/scope.py` changes. Confirm: `_atable` is the module-level import already used by `scope_to_teacher_ids` (monkeypatch target stays `app.auth.scope._atable`); superadmin/no-org paths never query; the count query matches the `list_members` shape (`select("id").eq("org_id", str(org_id))`). State findings. **STOP — do not commit.**

---

## Task 2: Surface `is_solo` on the teacher payloads (server)

**Files:**
- Modify: `app/routers/auth.py:868` (login response), `app/routers/auth.py:889` (`/api/v1/auth/me`)
- Test: `tests/test_is_solo.py` (append)

Both payloads build the same teacher dict shape the dashboard consumes; both must carry `is_solo` so the gate works on first login *and* on every refresh.

- [ ] **Step 1: Write the failing endpoint test**

Append to `tests/test_is_solo.py`:

```python
# ── /api/v1/auth/me surfaces is_solo ──

@pytest.mark.asyncio
async def test_me_payload_includes_is_solo(monkeypatch):
    import app.routers.auth as auth_mod

    async def _fake_require_admin(_request):
        return {"id": "a1", "email": "a@x.com", "full_name": "A",
                "org_id": "orgA", "org_role": "admin",
                "email_verified_at": None}

    async def _fake_org_is_solo(_teacher):
        return False

    monkeypatch.setattr(auth_mod, "require_admin", _fake_require_admin)
    monkeypatch.setattr(auth_mod, "org_is_solo", _fake_org_is_solo)

    payload = await auth_mod.teacher_me.__wrapped__(_request=object())  # type: ignore[attr-defined]
    assert payload["is_solo"] is False
    assert payload["org_role"] == "admin"
```

> Note: `teacher_me` is wrapped by `@limiter.limit`. If `.__wrapped__` is unavailable, call through TestClient instead — see Step 1b fallback. Verify which works before writing the implementation.

- [ ] **Step 1b: If `.__wrapped__` is not present, use the TestClient form instead**

Replace the test body with the project's established endpoint-test pattern (see `tests/test_room_cam.py`): patch `app.auth.admin_auth._get_teacher_by_id` to return the teacher, patch `app.routers.auth.org_is_solo` to an `AsyncMock(return_value=False)`, then `client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {make_admin_token(...)}"})` and assert `resp.json()["is_solo"] is False`. Use the same `client` fixture and `make_admin_token` helper that `tests/test_room_cam.py` imports.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: FAIL — `KeyError: 'is_solo'` (the payload has no such key yet)

- [ ] **Step 3: Import the helper and add `is_solo` to `/api/v1/auth/me`**

In `app/routers/auth.py`, add the import near the other auth imports (top of file, alongside the existing `require_admin` import):

```python
from ..auth.scope import org_is_solo
```

Then modify `teacher_me` (`:889`):

```python
    teacher = await require_admin(request)
    return {
        "id": teacher["id"],
        "email": teacher["email"],
        "full_name": teacher["full_name"],
        "org_id": teacher.get("org_id"),
        "org_role": teacher.get("org_role", "teacher"),
        "is_solo": await org_is_solo(teacher),
        "email_verified_at": teacher.get("email_verified_at"),
    }
```

- [ ] **Step 4: Add `is_solo` to the login response teacher dict (`:868`)**

In the same file, modify the login JSONResponse teacher dict:

```python
        "teacher": {
            "id": teacher["id"],
            "email": teacher["email"],
            "full_name": teacher["full_name"],
            "org_id": teacher.get("org_id"),
            "org_role": teacher.get("org_role", "teacher"),
            "is_solo": await org_is_solo(teacher),
            "email_verified_at": teacher.get("email_verified_at"),
        },
```

Confirm the enclosing login handler is `async def` (it is — it `await`s `_issue_and_persist_refresh_token` just above) so the `await` is legal.

- [ ] **Step 5: Run the endpoint test to verify it passes**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/test_is_solo.py -q`
Expected: PASS (8 passed)

- [ ] **Step 6: Run the auth regression suite**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/ -q -k "auth or login or me"`
Expected: PASS (no regressions)

- [ ] **Step 7: Self-review (HARD RULE — do not commit)**

Re-read the two payload edits and the import. Confirm: import path is `from ..auth.scope import org_is_solo`; both edits await `org_is_solo(teacher)`; the login handler is async; key name is exactly `is_solo` in both places (the client reads `teacher.is_solo`). State findings. **STOP — do not commit.**

---

## Task 3: `applyOrgRole()` honors `is_solo` (client)

**Files:**
- Modify: `app/static/dashboard-app.js:218` (`_onAuthed`), `:1104` (state), `:1110` (`applyOrgRole`), `:1155` (`_onAuthDone`)

No JS unit-test harness exists for `dashboard-app.js`; verification is `eslint` + explicit DOM reasoning. Do NOT fabricate a test framework.

- [ ] **Step 1: Add an `is_solo` state variable**

In `app/static/dashboard-app.js`, just below `let currentOrgRole = 'teacher';` (`:1104`), add:

```javascript
let currentIsSolo = false;  // solo account → force pure-teacher view (spec §B)
```

- [ ] **Step 2: Capture `is_solo` from the profile in `_onAuthed`**

In `_onAuthed(teacher)` (`:218`), immediately after `currentTeacherProfile = teacher || null;` add:

```javascript
  currentIsSolo = !!(teacher && teacher.is_solo);
```

- [ ] **Step 3: Make `applyOrgRole` resolve the effective role through `is_solo`**

Change the head of `applyOrgRole(org_role)` (`:1110-1111`) from:

```javascript
function applyOrgRole(org_role){
  currentOrgRole = org_role || 'teacher';
```

to:

```javascript
function applyOrgRole(org_role){
  const requested = org_role || 'teacher';
  // Solo accounts (no org, or an org of one) are pure teachers regardless
  // of any stored admin role — a 30-student solo buyer must never see an
  // "admin" concept (spec §B, two-mode UI). Superadmin is exempt: it is
  // never solo (see compute_is_solo) so this branch can't strip it.
  currentOrgRole = (currentIsSolo && requested !== 'superadmin') ? 'teacher' : requested;
```

The rest of `applyOrgRole` is unchanged: `[data-roles]` gating, the role badge, and default-tab routing all now key off the forced `currentOrgRole`, so a solo admin gets zero org chrome and the teacher default tab.

- [ ] **Step 4: Ensure `_onAuthDone` runs after `currentIsSolo` is set**

Confirm `_onAuthDone()` (`:1155`) is called from `_onAuthed` *after* Step 2's assignment (it already is — `_onAuthed` sets the profile then calls `_onAuthDone()` at `:222`). `_onAuthDone` reads `org_role` from the JWT/profile and calls `applyOrgRole(role)`, which now consults `currentIsSolo`. No further change needed. If `org_role` is falsy for a solo teacher, also force the gate by changing `_onAuthDone` (`:1157-1158`):

```javascript
  const role = (payload && payload.org_role) || (currentTeacherProfile && currentTeacherProfile.org_role);
  applyOrgRole(role || 'teacher');
```

(So `applyOrgRole` always runs and the `data-roles` gate is always applied, even when the JWT lacks `org_role`.)

- [ ] **Step 5: Lint the changed file**

Run: `cd /Users/arihantkaul/proctored-browser && npx eslint app/static/dashboard-app.js`
Expected: no new errors attributable to these edits. (If the file is not in the eslint config's globs, run `node --check app/static/dashboard-app.js` instead to confirm no syntax error.)

- [ ] **Step 6: DOM reasoning verification (state in the response)**

Trace and state: for `is_solo=true, org_role='admin'` → `currentOrgRole` becomes `'teacher'` → every `[data-roles="admin superadmin"]` tab/panel/filter-row gets `display:none` → role badge shows "Teacher" → default tab is `live`. For `is_solo=false, org_role='admin'` → unchanged admin behavior. For `org_role='superadmin'` → never forced (exempt), full tooling. Confirm no other reader of `currentOrgRole` assumes it equals the raw stored role.

- [ ] **Step 7: Self-review (HARD RULE — do not commit)**

Re-read all four edits. Confirm: `currentIsSolo` declared once; superadmin exemption present; `_onAuthed` sets `currentIsSolo` before `_onAuthDone()`; no syntax error. State findings. **STOP — do not commit.**

---

## Task 4: Hard-gate superadmin tooling out of the customer DOM (client)

**Files:**
- Modify: `app/static/dashboard-app.js:1030` (`_tabButtonForName`), `:1110` (`applyOrgRole` — add DOM strip)

Goal (spec §C, "hard gate now"): the three founder-internal tabs (`all-orgs`, `issues`, `debug`) and their panels must not exist in a non-superadmin's DOM, and must be unreachable via hash/keyboard routing. Superadmin behavior is untouched (reversible — no superadmin code deleted).

- [ ] **Step 1: Refuse routing into hidden/absent tabs in `_tabButtonForName`**

Change `_tabButtonForName(tab)` (`:1030-1033`) from:

```javascript
function _tabButtonForName(tab){
  if(!/^[a-z0-9-]+$/i.test(tab || '')) return null;
  return document.querySelector('.tab[data-tab="' + tab + '"]');
}
```

to:

```javascript
function _tabButtonForName(tab){
  if(!/^[a-z0-9-]+$/i.test(tab || '')) return null;
  const btn = document.querySelector('.tab[data-tab="' + tab + '"]');
  // Never route (via #tab- hash or keyboard) into a tab the current role
  // can't see. Closes the hash-deeplink path to superadmin panels for a
  // non-superadmin (e.g. #tab-all-orgs typed into the URL).
  if(!btn || btn.style.display === 'none') return null;
  return btn;
}
```

- [ ] **Step 2: Strip superadmin-only tabs + panels from the DOM for non-superadmins**

In `applyOrgRole`, immediately after the `[data-roles]` gating loop (just after the `forEach` block ending at `:1119`), add:

```javascript
  // Hard-gate founder-internal tooling: for any non-superadmin, remove the
  // superadmin-only tabs AND their panels from the DOM entirely (not just
  // display:none) so they cannot be hash-routed to and don't sit in a
  // paying teacher's page. Superadmin keeps everything. Reversible: this
  // deletes nothing from source; a superadmin session still renders them.
  if(currentOrgRole !== 'superadmin'){
    ['all-orgs', 'issues', 'debug'].forEach(name => {
      const tabBtn = document.querySelector('.tab[data-tab="' + name + '"]');
      if(tabBtn) tabBtn.remove();
      const panel = document.getElementById('panel-' + name);
      if(panel) panel.remove();
    });
  }
```

- [ ] **Step 3: Lint / syntax-check the changed file**

Run: `cd /Users/arihantkaul/proctored-browser && node --check app/static/dashboard-app.js`
Expected: no syntax error. Also run `npx eslint app/static/dashboard-app.js` if it is in the eslint globs.

- [ ] **Step 4: DOM reasoning verification (state in the response)**

Trace and state: a teacher/admin session → the three tab buttons and `#panel-all-orgs` / `#panel-issues` / `#panel-debug` (confirmed ids in `dashboard.html:1859/1883/1981`) are removed from the DOM; `_tabButtonForName('all-orgs')` returns `null` so `#tab-all-orgs` in the URL is a no-op; the existing `loadIssues()` / `loadAllOrgs()` early-return guards (`if(currentOrgRole !== 'superadmin') return;`) remain as defense-in-depth. A superadmin session → nothing removed, full tooling.

- [ ] **Step 5: Self-review (HARD RULE — do not commit)**

Re-read both edits. Confirm: `_tabButtonForName` still returns `null` for the invalid-name and missing-button cases; the strip block only runs for non-superadmin and only `.remove()`s the three named nodes; superadmin path is untouched; the panel ids used in `.remove()` match the actual DOM ids in `dashboard.html`. State findings. **STOP — do not commit.**

---

## Task 5: Full-suite regression + lint sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python test suite**

Run: `cd /Users/arihantkaul/proctored-browser && python3 -m pytest tests/ -q`
Expected: all pass (the prior baseline was 792 passed, 33 skipped; this plan adds 8 tests → expect ~800 passed, 33 skipped). Zero failures.

- [ ] **Step 2: Syntax/lint sweep on the touched JS**

Run: `cd /Users/arihantkaul/proctored-browser && node --check app/static/dashboard-app.js`
Expected: clean (no syntax error).

- [ ] **Step 3: Confirm `is_solo` key is present and consistent end-to-end**

Run: `cd /Users/arihantkaul/proctored-browser && grep -n "is_solo" app/routers/auth.py app/auth/scope.py app/static/dashboard-app.js`
Expected: `is_solo` appears in both server payloads, both scope helpers, and the client (`currentIsSolo` / `teacher.is_solo`). No stray/misspelled variants.

- [ ] **Step 4: Final self-review across all changed files (HARD RULE — do not commit)**

Re-read every changed file (`app/auth/scope.py`, `app/routers/auth.py`, `app/static/dashboard-app.js`, `tests/test_is_solo.py`). Audit for syntax/runtime/config/cross-ref/auth/failure issues. State findings in the response. **STOP — the user commits, not the agent.**

---

## Verification & safety net (maps to spec §"Verification & safety net")

- **Solo-mode UI (spec V3):** Task 3 forces the teacher view for a single-member org → zero org/admin chrome. Verified by DOM reasoning + the server `is_solo` unit tests proving a 1-member org returns `is_solo=true`.
- **No superadmin tabs in the teacher bundle (spec V4):** Task 4 removes the three tabs/panels from a non-superadmin DOM and blocks hash routing into them.
- **Admin roll-up correctness (spec V2):** unchanged — the teacher-picker (`.teacher-filter` + `loadOrgMembers` + `applyTeacherFilter`) and the org-widened read paths already shipped in the server tenancy plan (`2026-06-07-make-tenancy-real-server.md`); this plan does not touch them.
- **Tenant isolation (spec V1):** unchanged — guarded by `tests/test_tenant_isolation.py` from the server plan.
- **Self-Review Before Commit (spec V5):** every task ends with a self-review step and an explicit "do not commit" stop.

## Out of scope (this plan)
- Moving superadmin tooling to a **separate internal build/route** (`/internal`). Deferred per the spec's "hard gate now, separate build later" — Task 4 is the hard gate; the separate build is a later effort.
- Deleting superadmin handlers (`loadAllOrgs`, `loadIssues`, debug/flags) from `dashboard-app.js`. Kept (superadmin still uses them); only removed from the non-superadmin DOM at runtime.
- The React dashboard (`app/dashboard-ui/`) — untouched, not the direction.
- Splitting the 7,721-line `dashboard-app.js` into modules — separate refactor.
- The deferred **admin-screenshot-roll-up** gap carried over from the server plan's follow-ups — separate effort.
