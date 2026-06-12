/* Cookie notice — essential-cookies disclosure banner.
 *
 * Shows a dismissible bottom banner on login/dashboard/student pages
 * until the user clicks "Got it" (persisted in localStorage). No
 * accept/reject — all cookies are essential.
 *
 * CSP constraint: this file is loaded via <script defer>. No inline
 * scripts or event handlers anywhere — all DOM work is addEventListener.
 */
(function(){
  var DISMISSED_KEY = 'procta_cookie_notice_dismissed';

  /* Return true if the user has already dismissed the notice. */
  function _isDismissed() {
    try { return !!localStorage.getItem(DISMISSED_KEY); } catch(e) { return false; }
  }

  /* Mark as dismissed in localStorage and hide the banner. */
  function _dismiss() {
    try { localStorage.setItem(DISMISSED_KEY, '1'); } catch(e) {}
    var el = document.getElementById('cookie-notice');
    if (el) el.style.display = 'none';
  }

  /* Show the banner (hidden by default, revealed here if not dismissed). */
  function _showIfNeeded() {
    if (_isDismissed()) return;
    var el = document.getElementById('cookie-notice');
    if (el) el.style.display = '';
  }

  /* Wire up the dismiss button — no onclick, CSP-safe. */
  function _wireDismiss() {
    var btn = document.getElementById('cookie-notice-dismiss');
    if (btn) btn.addEventListener('click', _dismiss);
  }

  /* Run on DOMContentLoaded so the HTML is parsed. With <script defer>
   * this fires before paint, avoiding a flash of the banner. */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){
      _wireDismiss();
      _showIfNeeded();
    });
  } else {
    _wireDismiss();
    _showIfNeeded();
  }
})();
