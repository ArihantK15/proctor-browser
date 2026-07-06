(function(){
  'use strict';
  var $ = function(id){ return document.getElementById(id); };
  var csrfToken = '';

  function escapeHtml(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function dlIcon(){
    return '<svg class="icon" viewBox="0 0 24 24" style="width:16px;height:16px">'
      + '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>';
  }

  // ── Auth ────────────────────────────────────────────────────────
  function checkAuthAndLoad(){
    // cache: 'no-store' — without it, a browser that cached the 401 from
    // this exact URL (e.g. an earlier unauthenticated visit that bounced
    // to /login) can replay that stale 401 right after a real login
    // succeeds, since standard HTTP caching doesn't vary on the Cookie
    // header. Bug: looked like login "succeeded" then instantly bounced
    // back to /login.
    fetch('/api/v1/student/auth/me', { credentials: 'include', cache: 'no-store' })
      .then(function(r){
        if (r.ok) return r.json().then(showDashboard);
        goToLogin();
      })
      .catch(goToLogin);
  }

  // No login form on this page — send unauthenticated visitors to the
  // unified /login page (role=student) and bring them straight back here
  // once signed in, rather than duplicating a login form.
  function goToLogin(){
    location.replace('/login?role=student&next=' + encodeURIComponent('/student-dashboard'));
  }

  function showDashboard(me){
    $('sd-login').classList.add('hidden');
    $('sd-app').classList.remove('hidden');
    var name = me.full_name || me.email || 'there';
    var firstName = name.split(' ')[0];
    $('sd-greeting').textContent = 'Good ' + timeOfDayGreeting() + ', ' + firstName;
    $('sd-subtext').textContent = me.email || '';
    $('sd-profile-name').textContent = name;
    $('sd-profile-org').textContent = me.email || '';
    var initials = name.split(' ').filter(Boolean).slice(0,2).map(function(p){ return p[0].toUpperCase(); }).join('');
    $('sd-avatar-initials').textContent = initials || '?';
    loadExams();
    loadHistory();
  }

  function timeOfDayGreeting(){
    var h = new Date().getHours();
    if (h < 12) return 'morning';
    if (h < 18) return 'afternoon';
    return 'evening';
  }

  function ensureCsrf(){
    if (csrfToken) return Promise.resolve(csrfToken);
    return fetch('/api/v1/auth/csrf', { credentials: 'include' })
      .then(function(r){ return r.ok ? r.json() : {}; })
      .then(function(d){ csrfToken = d.csrf_token || ''; return csrfToken; })
      .catch(function(){ return ''; });
  }

  function signOut(){
    ensureCsrf().then(function(csrf){
      return fetch('/api/v1/student/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      });
    }).catch(function(){}).finally(function(){
      csrfToken = '';
      goToLogin();
    });
  }
  $('sd-signout-desktop').addEventListener('click', signOut);
  $('sd-signout-mobile').addEventListener('click', signOut);

  // ── Upcoming & Active exams ──────────────────────────────────────
  var countdownTarget = null;
  var countdownTimer = null;

  function fmtWindow(startsAt, endsAt){
    if (!startsAt) return '';
    var s = new Date(startsAt);
    var opts = { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' };
    var out = s.toLocaleString(undefined, opts);
    if (endsAt) {
      var e = new Date(endsAt);
      out += ' – ' + e.toLocaleString(undefined, { hour:'2-digit', minute:'2-digit' });
    }
    return out;
  }

  function loadExams(){
    fetch('/api/student/exams', { credentials: 'include' })
      .then(function(r){ return r.ok ? r.json() : { exams: [] }; })
      .then(function(d){ renderExams(d.exams || []); })
      .catch(function(){ renderExams([]); });
  }

  function renderExams(exams){
    var active = exams.filter(function(x){
      return ['open','in_progress','upcoming'].indexOf(x.status) !== -1;
    });
    var list = $('sd-exams-list');
    var empty = $('sd-exams-empty');
    list.innerHTML = '';
    if (!active.length) {
      empty.classList.remove('hidden');
    } else {
      empty.classList.add('hidden');
      active.forEach(function(ex){
        var isLive = ex.status === 'open' || ex.status === 'in_progress';
        var badge = isLive
          ? '<span class="sd-badge live"><span class="dot"></span>Live Now</span>'
          : '<span class="sd-badge scheduled">Scheduled</span>';
        var card = document.createElement('div');
        card.className = 'sd-exam-card' + (isLive ? ' live' : '');
        card.innerHTML =
          '<div class="sd-exam-top">'
          + '<div><h3 class="sd-exam-title">' + escapeHtml(ex.exam_title) + '</h3>'
          + '<p class="sd-exam-sub">' + escapeHtml(ex.teacher_name || '') + '</p></div>'
          + badge
          + '</div>'
          + (ex.starts_at ? '<span class="sd-exam-meta"><svg class="icon" viewBox="0 0 24 24" style="width:14px;height:14px"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>' + escapeHtml(fmtWindow(ex.starts_at, ex.ends_at)) + '</span>' : '')
          + '<div class="sd-exam-foot">'
          + '<span class="sd-exam-note">' + (isLive
              ? '<svg class="icon" viewBox="0 0 24 24" style="width:14px;height:14px"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></svg>Opens in the Procta desktop app'
              : (ex.access_code_required ? 'Access code required' : 'No access code required'))
          + '</span>'
          + (isLive ? '<button class="sd-join-btn" disabled title="Open the Procta desktop app to join"><svg class="icon" viewBox="0 0 24 24" style="width:14px;height:14px;stroke:#fff"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>Join Exam</button>' : '')
          + '</div>';
        list.appendChild(card);
      });
    }

    // "Next Exam In" countdown — earliest upcoming/open exam's starts_at.
    var upcoming = active.filter(function(x){ return x.starts_at; })
      .sort(function(a,b){ return new Date(a.starts_at) - new Date(b.starts_at); });
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    if (upcoming.length) {
      countdownTarget = new Date(upcoming[0].starts_at);
      tickCountdown();
      countdownTimer = setInterval(tickCountdown, 1000);
    } else {
      $('sd-stat-countdown').textContent = '—';
    }
  }

  function tickCountdown(){
    var diff = countdownTarget - new Date();
    var el = $('sd-stat-countdown');
    if (diff <= 0) { el.textContent = 'Now'; return; }
    var h = Math.floor(diff / 3600000);
    var m = Math.floor((diff % 3600000) / 60000);
    var s = Math.floor((diff % 60000) / 1000);
    var pad = function(n){ return String(n).padStart(2,'0'); };
    el.textContent = pad(h) + ':' + pad(m) + ':' + pad(s);
  }

  // ── Past Results ──────────────────────────────────────────────
  function loadHistory(){
    fetch('/api/student/history', { credentials: 'include' })
      .then(function(r){ return r.ok ? r.json() : { history: [] }; })
      .then(function(d){ renderHistory(d.history || []); })
      .catch(function(){ renderHistory([]); });
  }

  function renderHistory(history){
    var tbody = $('sd-results-tbody');
    var cards = $('sd-results-cards');
    var table = $('sd-results-table');
    var empty = $('sd-results-empty');
    tbody.innerHTML = ''; cards.innerHTML = '';

    $('sd-stat-taken').textContent = String(history.length);
    if (history.length) {
      var avg = history.reduce(function(sum, h){ return sum + (Number(h.percentage) || 0); }, 0) / history.length;
      $('sd-stat-avg').textContent = Math.round(avg) + '%';
    } else {
      $('sd-stat-avg').textContent = '—';
    }

    if (!history.length) {
      table.classList.remove('has-rows');
      cards.classList.remove('has-rows');
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');
    table.classList.add('has-rows');
    cards.classList.add('has-rows');

    history.forEach(function(h){
      var pct = Number(h.percentage) || 0;
      var passed = pct >= (Number(h.pass_mark) || 40);
      var statusHtml = passed ? '<span class="sd-pass">Pass</span>' : '<span class="sd-fail">Fail</span>';
      var dlUrl = '/api/v1/student/scorecard/' + encodeURIComponent(h.session_key);

      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td style="font-weight:600;color:var(--text-high)">' + escapeHtml(h.exam_title) + '</td>'
        + '<td style="font-family:\'IBM Plex Mono\',monospace">' + escapeHtml(h.submitted_at || '') + '</td>'
        + '<td><span class="sd-score">' + escapeHtml(h.score) + '/' + escapeHtml(h.total) + '</span>'
        + '<span class="sd-score-pct">' + Math.round(pct) + '%</span></td>'
        + '<td>' + statusHtml + '</td>'
        + '<td style="text-align:right"><a class="sd-dl-btn" href="' + dlUrl + '" aria-label="Download report" download>' + dlIcon() + '</a></td>';
      tbody.appendChild(tr);

      var card = document.createElement('div');
      card.className = 'sd-result-card';
      card.innerHTML =
        '<div><h3 class="sd-exam-title" style="font-size:14px">' + escapeHtml(h.exam_title) + '</h3>'
        + '<div class="sd-result-meta"><span>' + escapeHtml(h.submitted_at || '') + '</span>' + statusHtml + '</div></div>'
        + '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">'
        + '<span class="sd-score" style="font-size:13px">' + escapeHtml(h.score) + '/' + escapeHtml(h.total) + '</span>'
        + '<a class="sd-dl-btn" href="' + dlUrl + '" aria-label="Download report" download>' + dlIcon() + '</a>'
        + '</div>';
      cards.appendChild(card);
    });
  }

  // ── View switching (Home / Results) ───────────────────────────────
  // Home and Results are separate views the user switches between via the
  // sidebar/bottom-nav, not one page stacked top-to-bottom — previously the
  // "Results" link was just an anchor jump down the same page, which read
  // as a layout bug (Home appeared to trail off into an unrelated table).
  function initViewSwitching(){
    var navLinks = document.querySelectorAll('.sd-nav a[data-view], .sd-bottomnav a[data-view]');
    var views = document.querySelectorAll('.sd-view');

    function showView(name){
      views.forEach(function(v){ v.classList.toggle('active', v.dataset.view === name); });
      navLinks.forEach(function(a){ a.classList.toggle('active', a.dataset.view === name); });
    }

    navLinks.forEach(function(a){
      a.addEventListener('click', function(e){
        e.preventDefault();
        var view = a.getAttribute('data-view');
        showView(view);
        history.replaceState(null, '', '#' + view);
      });
    });

    var initial = (location.hash || '#home').slice(1);
    if (initial !== 'home' && initial !== 'results') initial = 'home';
    showView(initial);
  }

  initViewSwitching();
  checkAuthAndLoad();
})();
