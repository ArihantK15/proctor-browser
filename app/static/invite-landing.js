// Invite landing page client script. Served as a static file at
// /static/invite-landing.js so the page's CSP (script-src 'self')
// allows it — the previous inline <script> block in
// app/services/invite_landing.py was being blocked silently and
// the "Download (detecting OS…)" button never resolved to a real
// platform-specific URL.
//
// Three responsibilities:
//   1. OS detection → swap the primary download button's href + label
//   2. Copy-to-clipboard helper for the roll-number / access-code chips
//   3. procta:// deeplink launch when "Open in Procta app" is clicked

(function () {
  var ua = (navigator.userAgent || '').toLowerCase();
  var btn = document.getElementById('primary-dl');
  if (!btn) return;
  if (ua.indexOf('mac') !== -1) {
    btn.href = '/download/mac';
    btn.textContent = 'Download for macOS';
  } else if (ua.indexOf('win') !== -1) {
    btn.href = '/download/win';
    btn.textContent = 'Download for Windows';
  } else {
    btn.textContent = 'Download installer';
  }
})();

// The button calls copyVal(this) from its onclick handler, so the
// function must live in the global scope.
window.copyVal = function (btn) {
  var v = btn.getAttribute('data-val');
  if (!v) return;
  navigator.clipboard.writeText(v).then(function () {
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('ok');
    setTimeout(function () {
      btn.textContent = orig;
      btn.classList.remove('ok');
    }, 1500);
  });
};

// Triggers the procta:// deeplink via an invisible iframe so the OS
// hands off to the installed Electron app. Falling-back gracefully
// when the scheme isn't registered: the iframe just no-ops.
window.openInApp = function (e) {
  var btn = document.getElementById('open-in-app');
  var token = btn ? btn.getAttribute('data-token') : '';
  if (!token) return;
  window.addEventListener('blur', function () {}, { once: true });
  document.addEventListener('visibilitychange', function () {}, { once: true });
  var url = 'procta://invite/' + encodeURIComponent(token);
  try {
    var f = document.createElement('iframe');
    f.style.display = 'none';
    f.src = url;
    document.body.appendChild(f);
    setTimeout(function () {
      try { f.remove(); } catch (_) {}
    }, 2000);
  } catch (_) {}
};
