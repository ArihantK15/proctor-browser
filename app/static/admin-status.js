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

  function cls(s) { return (s || '').toLowerCase(); }

  async function load() {
    var ts = document.getElementById('ts');
    var grid = document.getElementById('grid');
    try {
      var r = await fetch(STATUS_URL, { credentials: 'include' });
      if (!r.ok) {
        grid.innerHTML = '<p style="color:red">Failed to load status (HTTP ' + r.status + '). Check your admin session.</p>';
        return;
      }
      var d = await r.json();
      ts.textContent = 'Last updated: ' + new Date().toLocaleString() + '  •  Uptime: ' + d.uptime_sec + 's  •  ' + d.health_checks + ' checks';
      var html = '';
      for (var i = 0; i < CHECK_ORDER.length; i++) {
        var key = CHECK_ORDER[i][0], label = CHECK_ORDER[i][1];
        var v = d.checks[key];
        if (v === undefined) continue;
        var s = cls(v);
        var dot = s == 'ok' ? 'ok' : s == 'warning' ? 'warning' : 'critical';
        html += '<div class="card"><h3>' + label + '</h3><div><span class="status-dot ' + dot + '"></span><span class="value">' + v + '</span></div></div>';
      }
      for (var k in d.checks) {
        if (!Object.prototype.hasOwnProperty.call(d.checks, k)) continue;
        if (CHECK_ORDER.findIndex(function (o) { return o[0] === k; }) !== -1) continue;
        html += '<div class="card"><h3>' + k.replace(/_/g, ' ') + '</h3><div><span class="value">' + JSON.stringify(d.checks[k]) + '</span></div></div>';
      }
      grid.innerHTML = html;
    } catch (e) {
      grid.innerHTML = '<p style="color:red">Error: ' + e.message + '</p>';
    }
  }
  load();
})();
