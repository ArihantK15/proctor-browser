// Guardian consent landing page behaviour.
// External file because the page is server-served and the CSP is
// script-src 'self' (no unsafe-inline) — an inline <script> is blocked.
(function () {
  function respond(action, token) {
    var status = document.getElementById('consent-status');
    if (status) status.textContent = 'Processing…';
    fetch('/api/v1/guardian/consent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, action: action })
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.ok) {
        if (status) status.textContent = (res.d && res.d.detail) || 'Something went wrong';
        return;
      }
      var card = document.getElementById('consent-card');
      if (!card) return;
      var name = (res.d && res.d.student_name) || '';
      card.textContent = '';
      var h = document.createElement('h1');
      h.textContent = action === 'grant' ? 'Consent recorded' : 'Consent denied';
      var p = document.createElement('p');
      // textContent (not innerHTML) — never interpolate the name as markup.
      p.textContent = action === 'grant'
        ? 'Your consent for ' + name + ' has been recorded.'
        : 'You have denied consent for ' + name + '.';
      card.appendChild(h);
      card.appendChild(p);
    }).catch(function () {
      if (status) status.textContent = 'Network error — please try again';
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-action]');
    if (!btn) return;
    var action = btn.getAttribute('data-action');
    if (action === '_guardianGrant') respond('grant', btn.dataset.token);
    else if (action === '_guardianDeny') respond('deny', btn.dataset.token);
  });
})();
