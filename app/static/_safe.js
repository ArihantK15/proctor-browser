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
 * the saved/dark theme harmlessly. The live switch (setTheme in
 * dashboard-app.js) writes the same key. */
(function () {
  try {
    var t = localStorage.getItem('procta_theme');
    var ok = { 'dark': 1, 'dark-oled': 1, 'light': 1 };
    document.documentElement.setAttribute('data-theme', ok[t] ? t : 'dark');
  } catch (_) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
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
