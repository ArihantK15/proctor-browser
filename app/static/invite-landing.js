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

function copyVal(btn) {
  var v = btn.getAttribute('data-val');
  if (!v) return;
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    return;
  }
  navigator.clipboard.writeText(v).then(function () {
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('ok');
    setTimeout(function () {
      btn.textContent = orig;
      btn.classList.remove('ok');
    }, 1500);
  });
}

document.addEventListener('click', function (e) {
  var copyBtn = e.target && e.target.closest ? e.target.closest('.copy[data-val]') : null;
  if (copyBtn) {
    e.preventDefault();
    copyVal(copyBtn);
    return;
  }

  var launch = e.target && e.target.closest ? e.target.closest('#open-in-app') : null;
  if (!launch) return;
  openInApp(e);
});

// Triggers the procta:// deeplink through a direct user-click navigation.
// The old iframe launch path was unreliable in modern browsers and could
// surface "failed to launch external protocol" style errors.
function openInApp(e) {
  if (e && e.preventDefault) e.preventDefault();
  var btn = document.getElementById('open-in-app');
  var token = btn ? btn.getAttribute('data-token') : '';
  if (!token) return;
  var msg = document.getElementById('open-in-app-msg');
  var url = 'procta://invite/' + encodeURIComponent(token);
  if (msg) {
    msg.className = 'launch-msg';
    msg.textContent = 'Opening Procta... If nothing happens, install the app first and click again.';
  }
  try {
    window.location.href = url;
    setTimeout(function () {
      if (msg && !document.hidden) {
        msg.textContent = 'Still here? Install Procta with the download button below, then click Open in Procta app again.';
      }
    }, 1800);
  } catch (err) {
    if (msg) {
      msg.className = 'launch-msg err';
      msg.textContent = 'Could not open Procta from this browser. Install the app, then reopen this invite.';
    }
  }
}
