// System-status page logic. External (not inline) because the app's CSP is
// `script-src 'self'` with no unsafe-inline/nonce — an inline <script> here is
// blocked in production, leaving /status-page permanently on "Loading...".
(function () {
  var STATUS_URL = '/api/v1/admin/status';
  var CHECK_ORDER = [
    ['supabase', 'Supabase Database'],
    ['redis', 'Redis Cache'],
    ['email', 'Email Service'],
    ['worker', 'Background Worker'],
    ['disk', 'Disk Space'],
    ['storage_write', 'Storage Write'],
    ['memory_pct', 'Memory'],
  ];

  function esc(s) { return (s == null ? '' : String(s)).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

  function cls(s) { return (s || '').toLowerCase(); }

  function cardHtml(key, label, value, dotClass) {
    return '<div class="card"><h3>' + esc(label) + '</h3><div><span class="status-dot ' + esc(dotClass) + '"></span><span class="value">' + esc(value) + '</span></div></div>';
  }

  async function load() {
    var ts = document.getElementById('ts');
    var grid = document.getElementById('grid');
    try {
      var r = await fetch(STATUS_URL, { credentials: 'include' });
      if (!r.ok) {
        grid.textContent = 'Failed to load status (HTTP ' + r.status + '). Check your admin session.';
        grid.style.color = 'red';
        return;
      }
      var d = await r.json();
      ts.textContent = 'Last updated: ' + new Date().toLocaleString() + '  \u2022  Uptime: ' + d.uptime_sec + 's  \u2022  ' + d.health_checks + ' checks';
      var html = '';
      for (var i = 0; i < CHECK_ORDER.length; i++) {
        var key = CHECK_ORDER[i][0], label = CHECK_ORDER[i][1];
        var v = d.checks[key];
        if (v === undefined) continue;
        var s = cls(v);
        var dot = s == 'ok' ? 'ok' : s == 'warning' ? 'warning' : 'critical';
        html += cardHtml(key, label, v, dot);
      }
      for (var k in d.checks) {
        if (!Object.prototype.hasOwnProperty.call(d.checks, k)) continue;
        if (CHECK_ORDER.findIndex(function (o) { return o[0] === k; }) !== -1) continue;
        html += cardHtml(k, k.replace(/_/g, ' '), JSON.stringify(d.checks[k]), 'ok');
      }
      grid.innerHTML = html;
    } catch (e) {
      grid.textContent = 'Error: ' + esc(e.message);
      grid.style.color = 'red';
    }
  }
  load();
})();
