/* Shared safe-string helpers.
 * Loaded as <script src="/static/_safe.js"> on served HTML pages.
 * Avoids duplicating the same 5-line functions across files.
 */

/* ── Theme bootstrap (must run BEFORE first paint to avoid a flash) ──
 * tokens.css ships three themes via [data-theme] on <html>:
 *   dark (default), dark-oled (true black), light.
 * This script is loaded synchronously in <head> (CSP is script-src
 * 'self', so an inline <script> would be blocked — an external sync
 * script is the flash-free, CSP-safe way). It reads the saved choice
 * from localStorage('procta_theme') and stamps data-theme so the very
 * first paint already uses it. Pages with no theme switch just inherit
 * the fallback theme harmlessly. The live switch (setTheme in
 * dashboard-app.js/student-app.js) writes the same key.
 *
 * Fallback (no saved choice yet) is 'dark' UNLESS a page opted into a
 * different default by setting window.__PROCTA_DEFAULT_THEME__ via a
 * small external script loaded before this one (see
 * _theme-default-light.js — used by student.html so the Electron
 * login/dashboard defaults to light without changing the teacher web
 * dashboard's dark default, which shares this same file). */
(function () {
  var fallback = (window.__PROCTA_DEFAULT_THEME__ === 'light') ? 'light' : 'dark';
  var resolved = fallback;
  try {
    var t = localStorage.getItem('procta_theme');
    var ok = { 'dark': 1, 'dark-oled': 1, 'light': 1 };
    resolved = ok[t] ? t : fallback;
  } catch (_) { /* resolved stays fallback */ }
  document.documentElement.setAttribute('data-theme', resolved);
  // Electron only (window.procta_native is undefined in a plain browser —
  // the teacher web dashboard/login pages never see this call). Tells
  // Electron to follow OUR resolved theme for prefers-color-scheme instead
  // of nativeTheme's OS-following 'system' default, which was fighting our
  // own light/dark switcher on a Mac/Windows machine in OS dark mode — see
  // the set-native-theme-source handler in main.js for the full story.
  // As early as possible (before first paint) to minimize any mismatch
  // window; setTheme() re-calls this on every live toggle.
  try {
    if (window.procta_native && typeof window.procta_native.setNativeThemeSource === 'function') {
      window.procta_native.setNativeThemeSource(resolved === 'light' ? 'light' : 'dark');
    }
  } catch (_) { /* best-effort */ }
})();

function _escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/`/g, '&#96;').replace(/\//g, '&#47;');
}
function _escGrp(s) {
  return String(s || '')
    .replace(/\\/g, '\\\\').replace(/'/g, "\\'")
    .replace(/`/g, '\\`').replace(/\r/g, '\\r').replace(/\n/g, '\\n')
    .replace(/"/g, '&quot;');
}
function esc(s) { return _escHtml(s); }
function escJs(s) { return _escGrp(s); }
function chatEscape(s) { return _escHtml(s); }
function chatJsEscape(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/* Extract a human-readable message from a parsed JSON error body. FastAPI
 * returns `detail` as a STRING (HTTPException) OR an ARRAY of {loc,msg,type}
 * objects (422 validation) — rendering the latter directly yields the infamous
 * "[object Object]". Always returns a string. Pass the parsed body + a
 * fallback: _detailText(await r.json(), 'Save failed'). */
function _detailText(d, fallback) {
  var det = d && d.detail;
  if (typeof det === 'string' && det) return det;
  if (Array.isArray(det)) {
    var msgs = det.map(function (x) { return (x && x.msg) ? x.msg : ''; })
                  .filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  if (det && typeof det === 'object' && typeof det.msg === 'string' && det.msg) return det.msg;
  if (d && typeof d.message === 'string' && d.message) return d.message;
  return fallback;
}
