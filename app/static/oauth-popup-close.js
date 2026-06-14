// Closes the OAuth popup after a successful connect. External (not inline)
// because the app's CSP is `script-src 'self'` with no unsafe-inline/nonce —
// an inline <script> here is blocked in production. The window was opened via
// window.open() from the dashboard, so window.close() is permitted.
window.close();
