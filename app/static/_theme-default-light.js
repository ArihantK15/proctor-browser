/* Opt a specific page into a light default theme instead of _safe.js's
 * global 'dark' fallback, without touching every other page that shares
 * _safe.js (e.g. the teacher web dashboard, which stays dark-default).
 * Must be loaded BEFORE _safe.js's <script> tag. External file (not an
 * inline <script>) because CSP here is script-src 'self', no unsafe-inline.
 */
window.__PROCTA_DEFAULT_THEME__ = 'light';
