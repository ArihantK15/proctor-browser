// Auto-submits the LTI Deep Linking response form back to the LMS. External
// (not inline) because the app's CSP is `script-src 'self'` with no
// unsafe-inline/nonce — an inline <script> here is blocked in production.
// NOTE: the form POSTs to the external LMS deep_link_return_url, which the
// global `form-action 'self'` CSP directive also blocks — that directive must
// be relaxed for this route for the POST to actually reach the LMS.
(function () {
  var form = document.getElementById('dlform');
  if (form) form.submit();
})();
