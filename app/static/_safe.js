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
 * dashboard's dark default, which shares this same file).
 *
 * window.__PROCTA_FORCE_BOOT_THEME__ goes further: ALWAYS boot in that
 * theme, ignoring any saved localStorage preference entirely (not just
 * as a fallback). Pragmatic workaround for a real, reproduced-but-not-
 * fully-diagnosed bug: booting student.html with a saved 'dark'
 * preference (so the login screen itself renders dark, correctly) and
 * then logging in — an in-page transition to #dashboard, no reload —
 * reliably produced a broken mixed light/dark render, even with the
 * MutationObserver-based color-enforcement above active. Booting the
 * login screen in light every time sidesteps the trigger condition
 * entirely; the live theme switch on the dashboard itself still works
 * normally afterward and its choice still persists for that session. */
(function () {
  var fallback = (window.__PROCTA_DEFAULT_THEME__ === 'light') ? 'light' : 'dark';
  var resolved = fallback;
  if (window.__PROCTA_FORCE_BOOT_THEME__) {
    resolved = window.__PROCTA_FORCE_BOOT_THEME__;
  } else {
    try {
      var t = localStorage.getItem('procta_theme');
      var ok = { 'dark': 1, 'dark-oled': 1, 'light': 1 };
      resolved = ok[t] ? t : fallback;
    } catch (_) { /* resolved stays fallback */ }
  }
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

/* ── Theme-color enforcement (survives an unexplained dark-mode
 * override) ──────────────────────────────────────────────────────────
 * On a real Mac (confirmed via DevTools, not guessed), something inserts
 * an "injected stylesheet" — DevTools' label for a real <style> element
 * added at runtime, distinct from the greyed-out "user agent stylesheet"
 * entries for browser defaults — that forces html/body to hardcoded dark
 * colors with !important, AFTER our own CSS has already applied the
 * correct light-theme colors. Neither raising our own CSS's specificity
 * nor syncing nativeTheme.themeSource (still worth having; see above)
 * stopped it — the exact trigger was never conclusively identified.
 *
 * _enforceThemeColors() sidesteps the mystery entirely: it reads our
 * OWN resolved --bg-base/--text-high for the current theme and reapplies
 * them as INLINE style with !important on both <html> and <body>. Inline
 * !important is very high in the cascade, and — critically — this
 * function is called AGAIN after the page fully settles (window 'load'
 * + a couple of delayed re-checks), so even if the mystery injection
 * happens asynchronously after our initial paint, our reassertion runs
 * AFTER it and wins on "last applied, equal origin" grounds. Exported on
 * window so setTheme() (student-app.js) can also call it on every live
 * toggle.
 *
 * Gated on window.__PROCTA_ENFORCE_THEME_COLORS__ (set by
 * _theme-default-light.js, loaded only by student.html) — NOT applied
 * blanket here, because dashboard.html shares this file and has its own
 * gradient body background (dashboard.css) that flattening to a flat
 * --bg-base color would visibly break. */
if (window.__PROCTA_ENFORCE_THEME_COLORS__) {
  window._enforceThemeColors = function () {
    try {
      var root = document.documentElement;
      var bg = getComputedStyle(root).getPropertyValue('--bg-base').trim();
      var fg = getComputedStyle(root).getPropertyValue('--text-high').trim();
      if (bg) {
        root.style.setProperty('background-color', bg, 'important');
        if (document.body) document.body.style.setProperty('background-color', bg, 'important');
      }
      if (fg) {
        root.style.setProperty('color', fg, 'important');
        if (document.body) document.body.style.setProperty('color', fg, 'important');
      }
    } catch (_) { /* best-effort */ }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window._enforceThemeColors);
  } else {
    window._enforceThemeColors();
  }
  window.addEventListener('load', function () {
    window._enforceThemeColors();
    // A late/async injection (e.g. tied to a network response or a timer
    // inside whatever is doing this) could still land after 'load' fires.
    // Two more passes at increasing delays catch that without polling
    // forever — cheap, and harmless if nothing ever displaces our colors.
    setTimeout(window._enforceThemeColors, 500);
    setTimeout(window._enforceThemeColors, 2000);
  });
  // The fixed-delay passes above only cover the first few seconds after
  // page load — real-world report: logging in (an in-page transition, no
  // reload — auth-view hides, #dashboard shows) minutes later, WELL past
  // those delays, and the mystery override reappeared, meaning whatever
  // triggers it isn't strictly tied to initial page load; a big DOM
  // change like the dashboard becoming visible can retrigger it. Rather
  // than guess more magic delays, watch <head> for the actual moment a
  // new stylesheet/style element is inserted (that's what the injection
  // IS, per the real DevTools inspection this whole fix is based on) and
  // reassert immediately whenever one appears — this catches it whenever
  // it happens, not just near page load.
  try {
    new MutationObserver(function () {
      window._enforceThemeColors();
    }).observe(document.head, { childList: true, subtree: true });
  } catch (_) { /* best-effort */ }
}

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
