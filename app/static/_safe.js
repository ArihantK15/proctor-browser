/* Shared safe-string helpers.
 * Loaded as <script src="/static/_safe.js"> on served HTML pages.
 * Avoids duplicating the same 5-line functions across files.
 */
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
