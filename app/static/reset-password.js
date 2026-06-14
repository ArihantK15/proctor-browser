// Password-reset page logic. External (not inline) because the app's CSP is
// `script-src 'self'` with no unsafe-inline/nonce — an inline <script> here is
// blocked in production, which left the reset form non-functional. The reset
// token is passed via the form's data-token attribute (set server-side).
(function () {
  var f = document.getElementById('f'),
      pw = document.getElementById('password'),
      btn = document.getElementById('btn'),
      err = document.getElementById('err'),
      ok = document.getElementById('ok'),
      loginBtn = document.getElementById('login-btn'),
      hint = document.getElementById('hint'),
      hintText = document.getElementById('hint-text'),
      toggle = document.getElementById('toggle');
  if (!f) return;
  var token = f.dataset.token || '';

  function validate() {
    var good = pw.value.length >= 10;
    btn.disabled = !good;
    hint.classList.toggle('good', good);
    hintText.textContent = good ? 'Looks good' : 'At least 10 characters';
  }
  pw.addEventListener('input', validate);

  toggle.addEventListener('click', function () {
    var s = pw.type === 'password';
    pw.type = s ? 'text' : 'password';
    toggle.textContent = s ? 'Hide' : 'Show';
    pw.focus();
  });

  f.addEventListener('submit', async function (e) {
    e.preventDefault();
    err.style.display = 'none';
    btn.disabled = true;
    var orig = btn.textContent;
    btn.textContent = 'Updating…';
    try {
      var r = await fetch('/api/v1/auth/password-reset/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token, password: pw.value })
      });
      var d = {};
      try { d = await r.json(); } catch (_) {}
      if (!r.ok) throw new Error(d.detail || 'Could not update password. Please try again.');
      f.style.display = 'none';
      ok.style.display = 'block';
      loginBtn.style.display = 'block';
    } catch (ex) {
      err.textContent = ex.message || 'Could not update password';
      err.style.display = 'block';
      btn.disabled = false;
      btn.textContent = orig;
    }
  });
})();
