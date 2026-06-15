// Org-invite acceptance page logic. External (not inline) because the app's CSP
// is `script-src 'self'` with no unsafe-inline/nonce — an inline <script> here is
// blocked in production, which left the "Accept & Join" form non-functional. The
// invite token is passed via the form's data-token attribute (set server-side).
// Local copy — this standalone page does not load _safe.js. Keep in sync
// with _safe.js::_detailText. Renders FastAPI 422 arrays (which would
// otherwise show "[object Object]") as readable text.
function _detailText(d, fallback) {
  var det = d && d.detail;
  if (typeof det === 'string' && det) return det;
  if (Array.isArray(det)) {
    var msgs = det.map(function (x) { return (x && x.msg) ? x.msg : ''; }).filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  if (det && typeof det === 'object' && typeof det.msg === 'string' && det.msg) return det.msg;
  if (d && typeof d.message === 'string' && d.message) return d.message;
  return fallback;
}
(function () {
  var form = document.getElementById('acceptForm');
  if (!form) return;
  var errEl = document.getElementById('error');
  var token = form.dataset.token || '';

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    errEl.style.display = 'none';
    var full_name = document.getElementById('full_name').value.trim();
    var password = document.getElementById('password').value;
    if (!full_name) {
      errEl.textContent = 'Name is required';
      errEl.style.display = 'block';
      return;
    }
    if (password.length < 10) {
      errEl.textContent = 'Password must be at least 10 characters';
      errEl.style.display = 'block';
      return;
    }
    try {
      var r = await fetch('/api/v1/auth/accept-org-invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token, full_name: full_name, password: password })
      });
      if (!r.ok) {
        var d = {};
        try { d = await r.json(); } catch (_) {}
        errEl.textContent = _detailText(d, 'Failed to accept invite');
        errEl.style.display = 'block';
        return;
      }
      var ok = {};
      try { ok = await r.json(); } catch (_) {}
      errEl.style.display = 'block';
      errEl.style.background = '#ecfdf5';
      errEl.style.color = '#065f46';
      errEl.textContent = ok.message || 'Invitation accepted. Check your email to verify before signing in.';
    } catch (ex) {
      errEl.textContent = 'Network error';
      errEl.style.display = 'block';
    }
  });
})();
