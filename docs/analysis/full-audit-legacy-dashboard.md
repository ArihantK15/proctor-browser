# Full Code Audit — Proctored Browser Dashboard

---

## DASHBOARD-APP.JS (10,293 lines)

### A. SECURITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 1 | 10100-10101 | **CRITICAL** | `onclick` attribute in template literal: `onclick="restoreQVersion(...)"` builds an inline event handler inside a template string injected via `innerHTML`. This **violates CSP** `script-src 'self'` — inline event handlers are not allowed under non-hash/nonce CSP. Replacing all legacy onclick handlers with `data-action` delegation was the explicit goal of the file, yet this one slipped through. The restore button should use `data-action="restoreQVersion"` with `data-args`. |
| 2 | 7075 | **CRITICAL** | `dot.onclick = (ev)=>{...}` — programmatic onclick assignment inside dynamically created DOM (scrubber dots). Same CSP violation. Must use `addEventListener` or `data-action` delegation. |
| 3 | 10059-10060 | **MEDIUM** | `_jsonArgsForAttr` returns `JSON.stringify(Array.from(arguments)).replace(/'/g,'&#x27;')`. Replacing `'` with `&#x27;` in a **JSON string** (which already uses double quotes) is unnecessary for JSON safety but the output is embedded in HTML attribute values. If a value inside the JSON contains `"`, the attribute could be broken. Should use `escAttr(JSON.stringify(...))` instead. |
| 4 | 10138 | **MEDIUM** | `_BLOCKED_DELEGATED_ACTIONS` blocks `eval`, `Function`, `fetch` but does **not** block `setTimeout` (string form), `setInterval`, `XMLHttpRequest`, `document.write`, `importScripts`, or constructor-based RCE. If an attacker can control `data-action` on an element, they have multiple RCE bypasses. The regex guard on line 10140 (`/^[A-Za-z_$][\w$]*$/`) is tighter, so this is defense-in-depth. But the blocklist is misleading. |
| 5 | 10086-10122 | **MEDIUM** | `showQHistory` uses `innerHTML` for the version history rows (line 10114). The `restoreBtn` template includes `v.version_number` unescaped on line 10101 and the `restoreQVersion` function is called with `escAttr(qid)` for the qid but `v.version_number` is interpolated raw. Version numbers are integers so not exploitable today, but the pattern is fragile. |
| 6 | 9103-9118 | **MEDIUM** | `_parseBulkRows` does naive comma splitting: `line.split(',').map(s=>s.trim())`. This does **not** handle quoted CSV fields (e.g., a name containing "Doe, John" would be split into two columns). Valid CSV requires proper parsing. |
| 7 | 7355-7359 | **LOW** | `_isSafeSid` regex `/^[a-zA-Z0-9_-]{1,64}$/` is reasonable but the check on line 7357 blocks only `__proto__`, `constructor`, `prototype`. Prototype-pollution vectors via `toString`, `valueOf`, `__defineGetter__` are not blocked. However, line 7364 uses `hasOwnProperty.call` which is the correct defense. |
| 8 | 835-862 | **LOW** | `authFetch` spreads headers with `{...hdr(), ...(opts.headers||{})}`. If `opts.headers` has a maliciously crafted `__proto__` key, it could pollute the headers object. In practice, `opts.headers` is always a literal object in this codebase. |
| 9 | 1-12 | **LOW** | `window.__proctaFragmentToken` (line 7) briefly stores the OAuth access token on the window object. It's immediately consumed by `authToken` on line 14 and the fragment is scrubbed. But if any third-party script or extension runs between lines 7 and 14 (other IIFEs, browser extensions), the token is exposed on `window`. |
| 10 | 302, 317 | **LOW** | SSE connect token is passed in URL query string: `${BASE}/api/v1/sse/sessions?token=${encodeURIComponent(connect_token)}`. The token appears in server access logs, browser history, and the Referer header. |
| 11 | 7298 | **LOW** | WebSocket auth: `new WebSocket(chatWsUrl(), [authToken])` passes the JWT as a WebSocket subprotocol. Custom subprotocols are visible in the initial HTTP upgrade request, logged by proxies and load balancers. |
| 12 | 788-801 | **LOW** | `_ensureCsrfToken` has no single-flight guard. If `refreshAll()` fires 10 parallel requests before the CSRF fetch completes, each one calls `_ensureCsrfToken()` independently, resulting in 10 CSRF token fetches. Contrast with `_refreshTokens` (line 808-814) which has the single-flight pattern. |

### B. CORRECTNESS

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 13 | 10216 | **HIGH** | `parseFloat(this.value)||1` — when `this.value` is `"0"`, `parseFloat("0")` returns `0` and `0 || 1` evaluates to `1`. A max score of 0 becomes 1 silently. |
| 14 | 532-533 | **HIGH** | `confirmDeleteExam` compares `(typed || '').trim() !== (name || '').trim()`. The `name` comes from `ex.exam_title` which may contain leading/trailing whitespace naturally (user-entered). If the exam title has trailing whitespace, the teacher can **never** type it correctly to confirm deletion, blocking a legitimate action. |
| 15 | 5008 | **HIGH** | `localInputToUtc`: `if (!y || !mo || !d || Number.isNaN(h) || Number.isNaN(mi)) return null;` — `!y` is `true` for year `0`, `!mo` is `true` for month `0`. Month `0` is invalid, but year `0` is a valid ISO year (1 BC). Not a real-world concern but a latent correctness issue. |
| 16 | 5042 | **MEDIUM** | `saveSchedule` validates `new Date(starts) >= new Date(ends)` but both values are from `datetime-local` inputs, which are already validated by the browser. However, `new Date(invalidString)` produces `Invalid Date`, and comparisons with `Invalid Date` are always `false`, so the validation **passes** silently, sending garbage to the server. |
| 17 | 6287, 6292 | **MEDIUM** | `_openAppDialog` on line 6292: `if(_appDialogResolve) _appDialogResolve(mode === 'confirm' ? false : null)` — if a modal is already open and a second one is triggered, the first is silently resolved with a falsy value. The caller thinks the user cancelled. For example, if `appConfirm` fires during a `showModal`, the showModal is silently dismissed as "cancelled". |
| 18 | 2219, 2240 | **MEDIUM** | Blob URL leak: `_pollRoomCamFrame` sets `img.src = URL.createObjectURL(blob)` on line 2219 without revoking the previous blob URL. On `closeRoomCamView` (line 2239), the current blob URL is revoked, but if the timer fires between `_roomCamOpened = false` and the cleanup, a blob URL is leaked. |
| 19 | 6297 | **MEDIUM** | `_openAppDialog` line 6297: `msg.textContent = body || ''` — the body text is set via `textContent`, which is XSS-safe. However, `showModal` calls this with a message string that may contain `\n` characters. `textContent` does NOT render newlines as `<br>` — they display as literal spaces in HTML. The dialog body collapses all newlines into a single line. |
| 20 | 6295-6309 | **MEDIUM** | `_openAppDialog` appends the prompt input (line 6309) after setting `textContent`. But if the `body` contains HTML-ish text (e.g., from a server error), it displays literally including angle brackets, which looks ugly but is safe. |
| 21 | 7355, 7364 | **MEDIUM** | `chatEnsureSession` uses `Object.prototype.hasOwnProperty.call(chatSessions, sid)` (line 7364) which is correct. But on lines 7393 and 7398, `chatSessions[chatActiveSid]` is accessed without the hasOwnProperty guard. If `chatActiveSid` is `"toString"`, it returns the function, and `chatSessions[sid]` on line 7395 (`sess.messages = ...`) would throw because `sess` is the `toString` function. |
| 22 | 788-801 | **MEDIUM** | `_ensureCsrfToken` race: if two parallel calls both find `_csrfTokenMemory` empty, both fire `fetch`. Both responses arrive. The second overwrites the first's token. Both tokens are valid (fetched from the server), but it's wasteful and the second `_csrfTokenMemory` assignment (line 800) races. |
| 23 | 3181-3196 | **MEDIUM** | `_syncIdReviewModalAfterRefresh` line 3184: `let at = (prevVid != null) ? _idReviewQueue.findIndex(...) : -1;` uses `!= null` which catches both `null` and `undefined`. But `prevVid` could be `0` (a valid violation ID), and `0 != null` is `true`, so it searches for index `0` correctly. This is actually correct but worth noting the `!= null` pattern. |
| 24 | 495, 500 | **LOW** | `createExam` line 495: `const title = document.getElementById('new-exam-title').value.trim()` — if the element doesn't exist, this throws `Cannot read properties of null`. The element is in the static HTML, so this is theoretical. |
| 25 | 500, 2162 | **LOW** | `parseInt` usage: `parseInt(document.getElementById('new-exam-duration').value, 10) || 60` — if the duration field is empty, `parseInt('', 10)` is `NaN`, and `NaN || 60` is `60`. But if the user types `0`, `parseInt('0', 10)` is `0` and `0 || 60` is `60`. Zero-minute exams become 60. |
| 26 | 1049-1062 | **LOW** | `_onboardMaybeShow` polls every 500ms for 30 seconds checking if `auth.classList.contains('hidden')`. If auth never completes (network down, server error), the interval runs 60 times and then stops. No error is logged. |
| 27 | 4214-4216 | **LOW** | `loadAppeals` has a cascading fallback pattern for `i.exam_id`: `i.exam_id || (data && data.exam_id) || ''`. The `data` variable refers to the original API response, but `i` iterates over `(data.appeals || [])`. The closure captures `data` correctly, but this indirect dependency is fragile. |
| 28 | 855-858 | **LOW** | `authFetch` retry path (after 401 refresh) copies `opts.headers` into a new object via spread on line 853 but then conditionally deletes `Authorization` on line 854 if `!authToken`. However, the original `opts` object's `headers` may have been mutated earlier. This is an idempotent operation so it's safe, but the pattern is confusing. |
| 29 | 737, 740 | **LOW** | In `refreshAll`, lines 737-740 are on a failed catch inside `refreshIdReviews`. The `refreshAll` function swallows individual failures to prevent one broken tab from blocking others. This is intentional but means errors in `refreshLive()` or other tabs are silently suppressed. |

### C. PERFORMANCE

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 30 | 6011-6141 | **HIGH** | `renderQEditor` rebuilds the ENTIRE question editor DOM from an HTML string on every keystroke (via `setQOption`, `setQQuestion`, `setQRange`). For an exam with 100 questions, each keystroke serializes 100 questions to HTML, parses it, and replaces the entire DOM. The comment on line 10217-10220 acknowledges this but only mitigates one specific range-input case. |
| 31 | 6145-6153 | **MEDIUM** | After rendering `qData`, iterates ALL questions to load images: `qData.forEach((q,i)=>{ if(!q.image_url) return; qLoadImgSrc(q.image_url)... })`. This fires one `authFetch` per question with an image, all in parallel with no concurrency limit. With 50 questions with images, 50 simultaneous fetch requests. |
| 32 | 10153-10155 | **MEDIUM** | On every delegated click: `JSON.parse(el.dataset.args || '[]')`. Even when `data-args` is empty or absent, this parses `"[]"` as JSON. The `|| '[]'` means the default is always parsed as JSON. Could be optimized: skip parse when `el.dataset.args` is undefined. |
| 33 | 7135-7142 | **MEDIUM** | Timeline lazy-load fires all `authFetch` calls simultaneously: `el.querySelectorAll('.tl-thumb[data-src]').forEach(img=>{ authFetch(img.dataset.src)... })`. No concurrency limit. A timeline with 100 screenshots fires 100 concurrent fetches. |
| 34 | 2296-2318 | **MEDIUM** | `openSecondaryCamGrid` iterates `pending` sessions **sequentially with `await`** in a `for...of` loop: each room-cam/start call awaits before the next one begins. If there are 10 pending sessions, this takes 10× the API round-trip time. Should use `Promise.all`. |
| 35 | 2918-2935 | **MEDIUM** | `renderResults` filters + sorts a copy of `resultsData` on every invocation. For 5000+ results with active filters, this runs through the entire array each time. No memoization or pagination. |
| 36 | 2920-2921 | **LOW** | Inefficient multiple `includes`: `[r.roll_number, r.full_name, r.email, r.session_id].some(v => String(v || '').toLowerCase().includes(q))` — creates four String wrappers per row. For 1000 rows, creates 4000 temporary String objects. |
| 37 | 3939-3941 | **LOW** | `_rememberAlertKey` iterates the entire `_recentAlertKeys` Map on every insert to evict old entries: `for(const [k, ts] of _recentAlertKeys){ ... }`. Map is bounded by `_ALERT_DEDUPE_MS` eviction; at 50 entries this is negligible. |
| 38 | 2238-2239, 2317 | **LOW** | `closeRoomCamView` calls `URL.revokeObjectURL` synchronously, but `_pollRoomCamFrame` may fire one more time before the interval is cleared. The revoke + a final frame poll could result in a revoked blob URL still being referenced. |
| 39 | 6310 | **LOW** | `setTimeout(()=>input.focus(), 0)` inside `_openAppDialog` — zero-timeout is a microtask deferral. This works but creates an observable flicker: the dialog renders without focus, then focus jumps to the input on the next frame. |

### D. ACCESSIBILITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 40 | 3015-3019 | **HIGH** | ID review cards have `role="button"` and `tabindex="0"` but only handle click events via `data-action`. There is **no Enter/Space key handler**. Keyboard-only users can focus the card but cannot activate it. |
| 41 | 3092-3093 | **HIGH** | `openIdReviewModal` sets `role="dialog"` and `aria-modal="true"` but does **not** set `aria-labelledby` or `aria-label` on the dialog. Screen readers don't announce the dialog's purpose. |
| 42 | 3089-3101 | **HIGH** | Focus is **not** moved into the newly created modal. After `_renderIdReviewModal()`, focus remains on the triggering element. Line 3099 adds a keydown listener but the modal can be dismissed via keyboard before focus enters it. |
| 43 | 3169-3174 | **HIGH** | `_closeIdReviewModal` removes the modal from the DOM but does **not** return focus to the element that triggered the modal (the `.id-review-card`). Screen reader users are stranded with no announced context. |
| 44 | 6287 | **HIGH** | `_openAppDialog` sets `els.overlay.style.display = 'flex'` on line 6315 but does **not** move focus into the dialog. The `setTimeout(()=>input.focus(), 0)` on line 6310 only applies to prompt mode. For alert and confirm modes, focus stays on the trigger element outside the dialog. |
| 45 | 6320 | **HIGH** | `_resolveAppDialog` hides the overlay (line 6321) but does **not** return focus to the trigger element that opened the dialog. |
| 46 | 2554-2558 | **MEDIUM** | `_calBadge` returns a `<span>` with `title` attribute for explanation but the badge text itself ("UNCALIBRATED" or "LOW CONFIDENCE" etc.) is color-coded with no equivalent text alternative. Color alone conveys meaning (red = bad, green = good). |
| 47 | 2148-2165 | **MEDIUM** | `refreshPendingGradeBadge` updates `chip.textContent` with a number. Screen readers announce the number but there's no `aria-label` like "3 pending grades" or an `aria-live` region on the parent. |
| 48 | 3012 | **MEDIUM** | ID review thumbnails: `<img src="..." alt="" ...>` — empty `alt` means decorative, but the image is the student's identity photo, which is content-bearing. Should have `alt="Student ID photo"` or similar. |
| 49 | 2643-2655 | **MEDIUM** | Live sessions table uses `<td>${_escHtml(state)}</td>` for session state. The state is rendered as text but is color-coded via CSS classes (`sev-high`, `sev-medium`). No `aria-label` on cells to convey severity. |
| 50 | 7067-7077 | **MEDIUM** | Scrubber dots (`tl-dot`) are `<div>` elements positioned absolutely on the scrubber track. They have a `title` attribute but no `role`, `tabindex`, or keyboard handler. Mouse-only interaction. |
| 51 | 3206-3208 | **MEDIUM** | `decideIdReview` creates an `<img>` for OCR/ID comparison with `alt=""` — decorative alt on a content-bearing image. |
| 52 | 7108 | **MEDIUM** | Timeline thumbnail `<img>` elements have `alt` set via `escAttr(e.type+suffix)` — the result is something like `"Tab switch — primary camera"` which is descriptive but starts with a machine event name rather than identifying what the image shows. Better: `"Proctoring screenshot: Tab switch"`. |
| 53 | 1260 | **LOW** | Role-gated tabs use `el.style.display = ''` / `'none'`. Removing `display: none` makes elements visible but they remain in DOM order, which may differ from visual order. Screen reader linearization order may not match visual tab order. |

### E. COMPATIBILITY / EDGE CASES

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 54 | 302-392 | **MEDIUM** | SSE reconnect: if `_connectSSE()` is called while `_sseFallbackTimer` is active and `_sseSource` is null, lines 384-390 queue another reconnect setTimeout. If the server is persistently down, the retry-ReadyState-close-onerror cycle creates a stack of `setTimeout` and `setInterval` calls. Each retry branch has `_sseFallbackTimer = setInterval(...)` — and if `_sseSource.close()` is called while already closed, it throws (caught). |
| 55 | 9311 | **MEDIUM** | `_copyInviteLink` calls `navigator.clipboard.writeText(url)` without checking for `navigator.clipboard` existence. In non-HTTPS contexts or older browsers, `navigator.clipboard` is undefined, and this throws. The `.catch(()=>{})` silently swallows the error, but the user gets no feedback that the copy failed. |
| 56 | 7291, 7298 | **MEDIUM** | `chatWsUrl` constructs the WebSocket URL by parsing `BASE` with a regex. If `BASE` contains user-controlled characters (e.g., `location.origin` on a page with a crafted URL), the protocol detection could be wrong. For `file://` protocol, `location.protocol` is `file:`, and `location.protocol==='https:'` is false, so the proto becomes `ws:` — but `file://` cannot make WebSocket connections. |
| 57 | 7285-7286 | **LOW** | `chatBeep = new Audio(CHAT_BEEP_DATA)` and `chatBeep.volume = 0.5`. In browsers with autoplay policies (Chrome, Safari), `Audio` elements cannot start playback without a user gesture. The chat beep may never play on the first message. |
| 58 | 1007-1008 | **LOW** | `onboardNext` can be called after the last step. If `_onboardIdx >= _ONBOARD_STEPS.length - 1`, `onboardComplete()` is called. But if `_ONBOARD_STEPS` is empty (length 0), `_onboardIdx < -1` is false, `_onboardIdx++` makes it `0`, and `_onboardRender()` tries to render an out-of-bounds step. Steps are hardcoded, so this is theoretical. |
| 59 | 1500-1520 | **LOW** | `renderOrgOverview` builds `orgTeacherOptions` from API response. If `rows` is empty, the teacher filter dropdown only has "All teachers". The CSV/template import sections that appear after the filter may overlap or be empty. |
| 60 | 2285 | **LOW** | `_scTile` uses `(window.CSS && CSS.escape) ? CSS.escape(sid) : sid` as a CSS selector. `CSS.escape` is available in modern browsers (Chrome 46+, Firefox 31+, Safari 10+). For very old browsers without it, session IDs containing special CSS characters (`.`, `:`, `#`) would create invalid selectors. |
| 61 | 1074, 1095 | **LOW** | `setTheme` and `showToolsSection` validate their input against a fixed array: `_THEMES.indexOf(name) === -1` — if the stored localStorage value is corrupted (e.g., `"[object Object]"` from a serialization bug), both silently fall back to defaults. This is fine but worth noting. |
| 62 | 3228 | **LOW** | `decideIdReview` gets `confirmMsg` and `okText` from a lookup on the `decision` parameter. If `decision` is an unexpected value, `confirmMsg` is `undefined` and `confirmMsg.includes(...)` throws. Guarded by the shared `decideIdReview` call sites, but `data-args` bypasses this. |
| 63 | 5055-5058 | **LOW** | `saveSchedule` on success sets `st.textContent = 'Schedule saved!'` and then `setTimeout(()=>loadSchedule(), 1000)`. If `loadSchedule` fails, the success message remains visible for 1 second, then potentially replaced by an error. The user sees "Schedule saved!" disappear, replaced by error state. |

### F. MAINTAINABILITY / CODE QUALITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 64 | 1-10293 | **HIGH** | Single 10,293-line file with ~250+ global functions. No module boundaries, no imports, no namespacing. Any function can call any other function. Refactoring is high-risk. |
| 65 | 1-12 vs rest | **MEDIUM** | The IIFE on lines 1-12 uses `var` (necessary for the pattern). Lines 1072, 1093 use `var`. Lines 1116, 1128-1132 use `var`. The rest uses `let` and `const`. Inconsistent declaration style. |
| 66 | 1339-1343 | **MEDIUM** | `_origSaveTokens` pattern: `_saveTokens` is reassigned after being declared with `let`. The original is saved, then overridden. The new function calls `_origSaveTokens(access, refresh)` and then `_onAuthDone()`. This works but is fragile — if another patch also wraps `_saveTokens`, the order breaks. |
| 67 | 264-272 | **LOW** | `_sseExamId`, `_sseFallbackTimer`, `_liveRefreshTimer` are declared as `let` at global scope but only used inside `_connectSSE` and `_debouncedLiveRefresh`. Should be local to a closure or module. |
| 68 | Various | **LOW** | Magic numbers throughout: `400` (debounce), `5000` (poll interval), `30000` (reconnect delay), `60000` (analytics cache TTL), `1500` (room cam frame poll), `800` (onboard delay), `8000` (alert toast TTL), `240` (early join min limit), `1100` (pulse animation), `200` (note slice), `120` (details preview truncation). No named constants. |
| 69 | 5049 | **LOW** | `_ej = Math.max(0, Math.min(_ej, 240))` — the `240` limit on early join minutes is undocumented. Why 240 (4 hours)? Should be a named constant with a comment. |
| 70 | 3939-3941 | **LOW** | `for(const [k, ts] of _recentAlertKeys){ if(now - ts > _ALERT_DEDUPE_MS) _recentAlertKeys.delete(k); }` — iterating a Map while deleting from it is safe in JS (the spec guarantees it), but many developers don't know this and it looks like a bug. A comment would help. |
| 71 | 527-536 | **LOW** | `confirmDeleteExam` shows `ex.exam_title` in the confirm dialog, then in the prompt dialog. If the title contains characters that break the prompt layout (e.g., very long strings), the UX degrades. |
| 72 | 690-700 | **LOW** | `doLogout` uses a plain `fetch` (not `authFetch`) for logout to avoid the refresh loop. Line 695-696 grabs `_getCsrfToken()` and line 696 sets `X-CSRF-Token`. If the CSRF token has expired, the logout POST may fail with 403. Logout silently fails but the user is still shown as logged out client-side. |
| 73 | 764-782 | **LOW** | `hdr()` function constructs Authorization header from `authToken`. Returned object is spread into `opts.headers` in `authFetch`. If `authToken` changes between `opts.headers` construction and the actual fetch (unlikely since JS is single-threaded), stale token is used. |
| 74 | 700-701 | **LOW** | Map deletion inside iteration: `for(const [k, ts] of _recentAlertKeys)` on line 3939. Same pattern as line 3940 `for(const [key, ts] of _recentAlertKeys)`. This is correct but unusual. |

---

## DASHBOARD.HTML (~400 lines)

### A. SECURITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 75 | Static | **HIGH** | HTML has NO inline event handlers anywhere (onclick, onchange, etc.) — already migrated to `data-action` delegation. This is correct for CSP. No issues found. |

### B. CORRECTNESS

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 76 | Modal overlay | **LOW** | The `<div id="app-modal-overlay">` contains a single shared dialog structure used by `showModal`, `appConfirm`, `appPrompt`. Since all three share the same DOM, `aria-modal` and `role` cannot be specialized per-mode. The dialog always has the same ARIA role regardless of which mode is active. |

### C. ACCESSIBILITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 77 | n/a | **LOW** | The static HTML is a document shell with very little content (most DOM is generated by JS). Accessibility audit must focus on generated content. The auth login form has `<label>` elements correctly associated with `<input>` elements. |

### D. COMPATIBILITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 78 | n/a | **LOW** | HTML uses semantic elements: `.tab` with `role="tab"`, `role="tablist"`, etc. Good. No `<table>` for layout. |

---

## DASHBOARD.CSS (~380 lines)

### A. CORRECTNESS

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 79 | Various | **LOW** | CSS uses `var(--token)` custom properties extensively. If a token is missing (e.g., the theme CSS doesn't load), the fallback is `initial` for most properties, which may produce invisible (color: initial is black, not white) output. Some rules have CSS fallbacks (e.g., `background: var(--card,#161a22)`) but most don't. |

### B. PERFORMANCE

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 80 | Various | **LOW** | No excessive selector specificity issues. No `!important`. No `@import`. |

### C. COMPATIBILITY

| # | Line(s) | Severity | Issue |
|---|---------|----------|-------|
| 81 | `.ir-stage-img.zoomed` | **LOW** | Uses `position:fixed` for zoom overlay. On mobile viewports, `position:fixed` behavior varies (iOS Safari viewport changes). |

---

## SUMMARY: COMPLETE ISSUE INVENTORY

| Category | Count | Critical | High | Medium | Low |
|----------|-------|----------|------|--------|-----|
| Security | 12 | 2 | 0 | 6 | 4 |
| Correctness | 17 | 0 | 3 | 8 | 6 |
| Performance | 10 | 0 | 1 | 5 | 4 |
| Accessibility | 14 | 0 | 5 | 7 | 2 |
| Compatibility/Edge Cases | 10 | 0 | 0 | 3 | 7 |
| Maintainability | 11 | 0 | 1 | 2 | 8 |
| **Total** | **74** | **2** | **10** | **31** | **31** |

### Critical (must fix):
1. **Line 10100-10101**: `onclick` attribute in template literal — breaks CSP
2. **Line 7075**: `dot.onclick` assignment — breaks CSP

### High (should fix before next deploy):
3. **Line 10216**: `parseFloat(this.value)||1` treats 0 as 1
4. **Line 532-533**: exam title with trailing whitespace blocks deletion
5. **Line 6011-6141**: full Q editor DOM rebuild on every keystroke
6. **Lines 3015-3019**: ID review cards lack keyboard handler (Enter/Space)
7. **Lines 3092-3093**: modal dialog missing `aria-labelledby`
8. **Lines 3089-3101**: focus not moved into modal on open
9. **Lines 3169-3174**: focus not returned on modal close
10. **Line 6287**: `_openAppDialog` doesn't move focus into dialog
11. **Line 6320**: `_resolveAppDialog` doesn't return focus
12. **Line 7075** (also in Critical): `dot.onclick` CSP violation (listed in both Critical and High as a single item — it's one fix)

Note: items 6-11 are the same underlying accessibility pattern (focus management), but each is a distinct point where the pattern fails.
