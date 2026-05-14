# Production Review Fixes — To-Do

Priority order: **P0** (blocks deploy) → **P1** (breaks features) → **P2** (risk/best practice)

---

## P0 — Blocks Deploy (fix immediately)

### C1 — `exam_idawait` undefined variable
**File:** `app/routers/question_bank.py:653`
**Fix:** `exam_idawait` → `exam_id`
```python
.eq("teacher_id", tid).eq("exam_id", exam_id).execute()
```

---

## P1 — Breaks Features / High Risk

### H1 — N+1 per-answer DB updates in grade-suggest loop
**File:** `app/routers/grading.py:218-224`
**Fix:** Batch all 50 answer updates into a single upsert instead of individual calls. Collect all updates into a list, call `_atable("answers").upsert(rows).execute()` once.

### H2 — Race condition in WS cleanup
**File:** `app/routers/sse.py:278-280`
**Fix:** Use `.get(sid, [])` instead of `_ws_clients[sid]`:
```python
for c in list(_ws_clients.get(sid, [])):
```
And iterate over a copy of the list to avoid modification-during-iteration.

### H5 — Local `_assert_session_owned` duplicate
**File:** `app/routers/question_bank.py:477-480`
**Fix:** Remove the local definition, import from `repositories.sessions` instead:
```python
from ..repositories.sessions import assert_session_owned as _assert_session_owned
```

### M2 — XSS in privacy.html
**File:** `app/static/privacy.html:150`
**Fix:** Escape `e.message` before innerHTML assignment:
```javascript
status.innerHTML = '<span class="err">Error: ' + _escHtml(e.message) + '</span>';
```

---

## P2 — Best Practice / Hardening

### H3 — Dead code removal
**File:** `app/routers/sse.py:54-56`
**Fix:** Remove the entire `_connect_tokens_lock_sync` function and its stub docstring.

### H4 — Unused imports
**File:** `app/main.py:5,200`
**Fix:** Remove `import json` (line 5) and `from starlette.datastructures import UploadFile` (line 200).

### M1 — `select("*")` in hot paths
**Files:** `app/auth/admin_auth.py:36,58,122,140`, `app/routers/exam.py:132,141,195,211,377,388,1061`, `app/repositories/sessions.py:24,27,90,127`
**Fix:** Replace with explicit column lists for auth lookups (e.g. `select("id,org_id,email")` for teacher queries). Start with the auth hot path (4 sites) which runs on every API request.

### M3 — Silent content-range parse failure
**File:** `app/database.py:201-203`
**Fix:** Log a warning when content-range header parsing fails:
```python
except (ValueError, IndexError) as e:
    _log.warning("Failed to parse content-range header '%s': %s", resp.headers.get("content-range"), e)
```

### M4 — Silent room_cam_status failure on WS disconnect
**File:** `app/routers/sse.py:434-439`
**Fix:** Log the failure instead of silent `pass`:
```python
except Exception as e:
    logger.warning("[room_cam] failed to mark session %s offline: %s", session_id, e)
```

### M5 — Pervasive `except Exception: pass` in chat.py
**File:** `app/services/chat.py` (8 locations)
**Fix:** Replace all silent `pass` with `logger.debug()` at minimum:
```python
except Exception as e:
    logger.debug("[chat] ws error: %s", e)
```

### L5 — `dir()` hack in admin_status.py
**File:** `app/routers/admin_status.py:265`
**Fix:** Move `_REQ_TS` definition before the function, or use module-level dict:
```python
uptime_sec = round(time.time() - _REQ_TS, 1)
```
(With `_REQ_TS` defined at module level before the handler.)

### L2/L3 — Hardcoded production fallbacks
**Files:** `app/constants.py:43-47`, `renderer/index.html:668`
**Fix:** Document as known developer convenience (won't fix — prod fallback is intentional for dev ease).

---

## Verification
After all fixes:
```bash
python3 -m pytest tests/ -x -q
npm audit
npm run build --prefix app/dashboard-ui
```

---

## Effort Estimate

| Priority | Items | Time |
|----------|-------|------|
| P0 | 1 (C1) | 1 min |
| P1 | 4 (H1, H2, H5, M2) | ~2 hr |
| P2 | 6 (H3, H4, M1-M5) | ~2 hr |
| **Total** | **11** | **~4 hr** |
