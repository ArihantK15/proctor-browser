# Pre-deploy critical fixes

## A1 — Appeals broken
**File**: `app/routers/appeals.py` lines 40-65

`verify_student_auth_token()` returns the DB `student_accounts` row (has `id`, `email`), NOT the JWT payload (has `sid`, `roll`).
- Line 41: `claims.get("sid", "")` → `account.get("id", "")` (change variable name to `account`)
- Line 42: `claims.get("roll", "")` → `account.get("email", "")` (roll not in student_accounts; use email for ownership check)
- Line 49-50: Session ownership check uses `roll_number` + `email` instead of `student_id` + `roll_number`
- Line 57-59: Insert uses `email` instead of `roll_number`

## A2 — Privacy CSRF 
**(a)** `app/auth/tokens.py` line 128-135: Add `"csrf": _gen_csrf()` to `issue_student_auth_token` payload.
**(b)** `app/static/privacy.html` lines 97-103: In `authFetch`, decode JWT payload (base64), extract `csrf` claim, send as `X-CSRF-Token` header:
```javascript
function _getCsrf() {
  const tok = TOKEN;
  if (!tok) return '';
  try {
    const payload = JSON.parse(atob(tok.split('.')[1]));
    return payload.csrf || '';
  } catch(e) { return ''; }
}
const headers = {'Authorization':'Bearer '+TOKEN, 'Content-Type':'application/json', 'X-CSRF-Token': _getCsrf()};
```

## A3 — Website postcss XSS
```bash
cd website && npm audit fix
```

## A4 — `student_account_id` → `account_id`
**File**: `app/routers/privacy.py`
- Line 122: `.eq("student_account_id", user_id)` → `.eq("account_id", user_id)`
- Line 203: `.eq("student_account_id", user_id)` → `.eq("account_id", user_id)`

## B2 — select(*) refactor (hot paths)
- `app/routers/api.py:62`: `select("*")` → `select("exam_id,exam_title,starts_at,ends_at,duration_minutes,access_code,phone_camera_enabled,shuffle_questions,shuffle_options,created_at")`
- `app/routers/privacy.py:88`: `select("*")` on `teachers` → explicit columns
- `app/routers/privacy.py:99`: `select("*")` on `organizations` → explicit columns
- `app/routers/privacy.py:105`: `select("*")` on `exam_config` → explicit columns

## B3 — Lint scripts
`package.json`: Add `"lint": "eslint main.js preload.js lobby_preload.js setup-preload.js lib/ renderer/"` to scripts

## B4 — Python version file
Create `.python-version` with `3.12`
