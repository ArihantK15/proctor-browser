/* Opt a specific page into a light default theme instead of _safe.js's
 * global 'dark' fallback, without touching every other page that shares
 * _safe.js (e.g. the teacher web dashboard, which stays dark-default).
 * Must be loaded BEFORE _safe.js's <script> tag. External file (not an
 * inline <script>) because CSP here is script-src 'self', no unsafe-inline.
 */
window.__PROCTA_DEFAULT_THEME__ = 'light';
/* Also opt into _safe.js's theme-color enforcement (inline !important
 * background/color reassertion — see its comment for why). Scoped here,
 * not applied blanket in _safe.js, because dashboard.html shares _safe.js
 * and has its own gradient body background (dashboard.css) that a flat
 * --bg-base override would incorrectly flatten. */
window.__PROCTA_ENFORCE_THEME_COLORS__ = true;
/* ALWAYS boot in light — not just as a fallback when nothing is saved —
 * see _safe.js's comment on window.__PROCTA_FORCE_BOOT_THEME__ for why:
 * booting the login screen from a saved 'dark' preference and then
 * logging in (an in-page transition, no reload) reliably produced a
 * broken mixed light/dark render even with the reactive color-enforcement
 * fix in place. The live theme switch still works normally once the
 * dashboard is showing. */
window.__PROCTA_FORCE_BOOT_THEME__ = 'light';
