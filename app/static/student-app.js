// ─── API base resolution ─────────────────────────────────────
// When this page is loaded via file:// from inside the Electron lobby,
// relative `/api/...` URLs would resolve to `file:///api/...` and fail.
// The lobby preload injects window.procta_native.serverUrl; we prepend
// it to every API call in that case. When served normally by FastAPI
// (HTTPS), the base is empty and same-origin relative URLs work fine.
const API_BASE = (window.procta_native && window.procta_native.serverUrl)
  ? window.procta_native.serverUrl.replace(/\/+$/, '')
  : '';
function apiUrl(p) { return API_BASE + p; }

function fetchWithTimeout(url, opts={}, timeoutMs=30000) {
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
  return fetch(url, {...opts, signal: opts.signal || ctrl.signal}).finally(()=>clearTimeout(timer));
}

// OAuth (Google + Microsoft) sign-in was removed 2026-05-23 — see
// HANDOFF.md. Email + password is the only sign-in path now.

// ─── state ────────────────────────────────────────────────────
let authMode = 'login';
let authToken   = '';
let refreshTok  = '';
let studentAuthed = false;
let refreshInFlight = null;
let _turnstileToken = null;
let _turnstileSiteKey = '';
let _turnstileWidgetId = null;
let _studentCsrfMemory = '';

async function _loadPublicConfig() {
  try {
    const r = await fetchWithTimeout(apiUrl('/api/v1/public-config'));
    if (r.ok) {
      const cfg = await r.json();
      _turnstileSiteKey = cfg.turnstile_site_key || '';
    }
  } catch(e) {}
}

// Track every widget we render so reset() hits all of them — the
// login + signup + reset forms each have their own widget slot.
const _turnstileWidgetIds = [];

function _renderTurnstileSlot(elementId) {
  if (!_turnstileSiteKey || !window.turnstile) return;
  const el = document.getElementById(elementId);
  if (!el || el.dataset.rendered) return;
  el.dataset.rendered = '1';
  const id = window.turnstile.render(el, {
    sitekey: _turnstileSiteKey,
    theme: 'dark',
    callback: (token) => { _turnstileToken = token; },
    'expired-callback': () => { _turnstileToken = null; },
    'error-callback': () => { _turnstileToken = null; },
  });
  _turnstileWidgetIds.push(id);
  // Keep the existing single-id var for back-compat with anything
  // still reading it directly.
  _turnstileWidgetId = id;
}

function _initTurnstile() {
  // P1.2: login form (#cf-turnstile-login) needs a widget too. The
  // reset slot was already wired; without the login slot we never
  // collected a captcha_token to send on /student/auth/login.
  _renderTurnstileSlot('cf-turnstile-login');
  _renderTurnstileSlot('cf-turnstile-reset');
}

function _resetTurnstile() {
  _turnstileToken = null;
  if (!_turnstileSiteKey || !window.turnstile) return;
  for (const id of _turnstileWidgetIds) {
    try { window.turnstile.reset(id); } catch(e) {}
  }
}

// ─── auth UI ──────────────────────────────────────────────────
function setAuthMode(mode) {
  authMode = mode;
  const isSignup = mode === 'signup';
  document.getElementById('tab-login').classList.toggle('active', !isSignup);
  document.getElementById('tab-signup').classList.toggle('active', isSignup);
  document.getElementById('fg-name').style.display = isSignup ? 'block' : 'none';
  document.getElementById('auth-heading').textContent = isSignup ? 'Create your account' : 'Welcome back';
  document.getElementById('auth-subheading').textContent = isSignup
    ? 'One login for every exam you take on Procta'
    : 'Sign in to see your upcoming exams';
  document.getElementById('auth-btn').textContent = isSignup ? 'Sign up' : 'Log in';
  document.getElementById('inp-password').autocomplete = isSignup ? 'new-password' : 'current-password';
  document.getElementById('auth-err').textContent = '';
}

function showReset() {
  document.getElementById('fg-name').style.display = 'none';
  // Hide email + password fields (by parent .fg)
  document.getElementById('inp-email').closest('.fg').style.display = 'none';
  document.getElementById('inp-password').closest('.fg').style.display = 'none';
  document.getElementById('auth-btn').style.display = 'none';
  document.getElementById('auth-err').textContent = '';
  document.querySelector('.auth-tabs').style.display = 'none';
  document.getElementById('forgot-link').style.display = 'none';
  document.getElementById('reset-view').style.display = 'block';
  document.getElementById('auth-heading').textContent = 'Reset password';
  document.getElementById('auth-subheading').textContent = 'We\'ll email you a link to reset it';
  _initTurnstile();
}

function cancelReset() {
  document.getElementById('reset-view').style.display = 'none';
  document.getElementById('reset-err').textContent = '';
  document.getElementById('reset-ok').style.display = 'none';
  document.getElementById('reset-email').disabled = false;
  document.getElementById('reset-btn').style.display = '';
  // Restore login form
  document.getElementById('inp-email').closest('.fg').style.display = '';
  document.getElementById('inp-password').closest('.fg').style.display = '';
  document.getElementById('auth-btn').style.display = '';
  document.querySelector('.auth-tabs').style.display = '';
  document.getElementById('forgot-link').style.display = '';
  setAuthMode('login');
}

async function doReset() {
  const email = document.getElementById('reset-email').value.trim().toLowerCase();
  const errEl = document.getElementById('reset-err');
  errEl.textContent = '';
  if (!email || !email.includes('@')) { errEl.textContent = 'Enter a valid email'; return; }
  const btn = document.getElementById('reset-btn');
  btn.disabled = true; btn.textContent = 'Sending...';
  try {
    const body = {email};
    if (_turnstileToken) body.captcha_token = _turnstileToken;
    const r = await fetchWithTimeout(apiUrl('/api/v1/student-auth/password-reset'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail || 'Failed to send reset link');
    }
    document.getElementById('reset-ok').style.display = 'block';
    document.getElementById('reset-btn').style.display = 'none';
    document.getElementById('reset-email').disabled = true;
  } catch(e) {
    errEl.textContent = e.message || 'Something went wrong, try again.';
    _resetTurnstile();
  } finally {
    btn.disabled = false; btn.textContent = 'Send Reset Link';
  }
}

_loadPublicConfig().then(_initTurnstile);

async function doAuth() {
  const errEl = document.getElementById('auth-err');
  errEl.textContent = '';
  const email = document.getElementById('inp-email').value.trim().toLowerCase();
  const password = document.getElementById('inp-password').value;
  const name = document.getElementById('inp-name').value.trim();

  if (!email || !email.includes('@')) { errEl.textContent = 'A valid email is required'; return; }
  if (!password) { errEl.textContent = 'Password is required'; return; }
  if (authMode === 'signup' && !isStrongPassword(password)) {
    errEl.textContent = 'Password must be at least 10 characters and include uppercase, lowercase, a number, and a symbol';
    return;
  }
  if (authMode === 'signup' && !name) { errEl.textContent = 'Full name is required'; return; }

  const btn = document.getElementById('auth-btn');
  btn.disabled = true;
  btn.textContent = authMode === 'signup' ? 'Creating account…' : 'Signing in…';

  try {
    if (authMode === 'signup') {
      // P2.8: send captcha_token on signup too once backend gates
      // it. Backend currently accepts the field whether or not
      // verify_or_403 runs (no-op when sandbox/disabled), so this
      // is forward-compatible without waiting for the backend gate.
      const signupBody = {email, password, full_name: name};
      if (_turnstileToken) signupBody.captcha_token = _turnstileToken;
      const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/signup'), {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(signupBody),
      });
      if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Signup failed');
      // fall through to login
    }

    // P1.2: backend's /student/auth/login calls verify_or_403 when
    // TURNSTILE_SECRET_KEY is set. Pass _turnstileToken so production
    // logins succeed; null in dev falls through the sandbox path.
    const loginBody = {email, password};
    if (_turnstileToken) loginBody.captcha_token = _turnstileToken;
    const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/login'), {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(loginBody),
    });
    if (!r.ok) {
      _resetTurnstile && _resetTurnstile();
      throw new Error((await r.json().catch(()=>({}))).detail || 'Login failed');
    }
    const d = await r.json();
    authToken  = d.access_token || '';
    refreshTok = d.refresh_token || '';
    studentAuthed = true;
    await ensureStudentCsrf(true);
    await showDashboard(d.account);
    // If this sign-in was triggered by a procta://invite/<token>
    // deep-link, link the account to the invite row now that we have
    // a live JWT. Acceptance is idempotent on the server, so it's
    // safe to also call this if the student was already signed in.
    if (_pendingInvite) await _acceptPendingInvite();
  } catch (e) {
    errEl.textContent = e.message || 'Something went wrong';
  } finally {
    btn.disabled = false;
    btn.textContent = authMode === 'signup' ? 'Sign up' : 'Log in';
  }
}

async function doLogout() {
  try {
    const headers = {};
    if (authToken) headers.Authorization = 'Bearer ' + authToken;
    const csrf = await ensureStudentCsrf();
    if (csrf) headers['X-CSRF-Token'] = csrf;
    await fetchWithTimeout(apiUrl('/api/v1/student/auth/logout'), {
      method: 'POST',
      credentials: 'include',
      headers,
    });
  } catch(e) {}
  _studentCsrfMemory = '';
  authToken = '';
  refreshTok = '';
  studentAuthed = false;
  location.reload();
}

function isStrongPassword(password) {
  return !!password
    && password.length >= 10
    && /[a-z]/.test(password)
    && /[A-Z]/.test(password)
    && /\d/.test(password)
    && /[^A-Za-z0-9]/.test(password);
}

// ─── fetch wrapper with single-flight refresh ─────────────────
function getStudentCsrf() {
  return _studentCsrfMemory || '';
}

async function ensureStudentCsrf(force = false) {
  const existing = getStudentCsrf();
  if (existing && !force) return existing;
  const headers = {};
  if (authToken) headers.Authorization = 'Bearer ' + authToken;
  const r = await fetchWithTimeout(apiUrl('/api/v1/auth/csrf'), {
    credentials: 'include',
    headers,
  });
  if (!r.ok) return '';
  const d = await r.json().catch(()=>({}));
  const csrf = d.csrf_token || '';
  if (csrf) _studentCsrfMemory = csrf;
  return csrf;
}

async function authed(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  opts.credentials = opts.credentials || 'include';
  opts.headers = Object.assign({}, opts.headers);
  if (authToken) opts.headers.Authorization = 'Bearer ' + authToken;
  if (!['GET','HEAD','OPTIONS'].includes(method)) {
    const csrf = await ensureStudentCsrf();
    if (csrf) opts.headers['X-CSRF-Token'] = csrf;
  }
  const url = apiUrl(path);
  let r = await fetchWithTimeout(url, opts);
  if (r.status === 401) {
    const ok = await tryRefresh();
    if (ok) {
      if (authToken) opts.headers.Authorization = 'Bearer ' + authToken;
      else delete opts.headers.Authorization;
      if (!['GET','HEAD','OPTIONS'].includes(method)) {
        const csrf = await ensureStudentCsrf();
        if (csrf) opts.headers['X-CSRF-Token'] = csrf;
      }
      r = await fetchWithTimeout(url, opts);
    }
  }
  return r;
}

async function tryRefresh() {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/refresh'), {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(refreshTok ? {refresh_token: refreshTok} : {}),
      });
      if (!r.ok) return false;
      const d = await r.json();
      authToken = d.access_token || '';
      refreshTok = d.refresh_token || '';
      studentAuthed = true;
      await ensureStudentCsrf(true);
      return true;
    } catch { return false; }
    finally { refreshInFlight = null; }
  })();
  return refreshInFlight;
}

// ─── dashboard ────────────────────────────────────────────────
async function showDashboard(account) {
  studentAuthed = true;
  document.getElementById('auth-view').style.display = 'none';
  document.getElementById('dashboard').style.display = 'block';
  document.getElementById('me-name').textContent = account.full_name || account.email;
  await Promise.all([loadExams(), loadHistory(), loadAppeals()]);
}

function fmtWhen(iso) {
  if (!iso) return '—';
  // Use the student's browser timezone + locale so non-India students
  // see local times (audit M3 — was hardcoded en-IN + Asia/Kolkata).
  // Falls back to IST if Intl is unavailable for any reason.
  try {
    const tz = (()=>{ try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; } catch(_){ return 'Asia/Kolkata'; } })();
    return new Date(iso).toLocaleString(navigator.language || 'en-IN', {
      timeZone: tz,
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true,
      timeZoneName: 'short',
    });
  } catch { return iso; }
}

function statusBadge(status) {
  const map = {
    in_progress: ['badge-amber',   'In progress'],
    open:        ['badge-emerald', 'Open now'],
    upcoming:    ['badge-accent',  'Upcoming'],
    closed:      ['badge-red',     'Closed'],
    completed:       ['badge-muted', 'Submitted'],
    force_submitted: ['badge-muted', 'Force Submitted'],
  };
  const [cls, label] = map[status] || ['badge-muted', _escHtml(status)];
  return `<span class="badge ${cls}">${label}</span>`;
}

let _lastExamsFetch = 0;
let _examsInflight = false;
async function loadExams(opts) {
  const silent = opts && opts.silent;
  if (_examsInflight) return;
  _examsInflight = true;
  const container = document.getElementById('exams-container');
  // Only show the "Loading…" placeholder on the first fetch — silent
  // refreshes (focus/visibility) keep the existing list visible so the
  // dashboard doesn't flicker every time the user alt-tabs.
  if (!silent) {
    container.innerHTML = '<div class="exams-empty">Loading…</div>';
  }
  try {
    const r = await authed('/api/student/exams');
    if (r.status === 401) { doLogout(); return; }
    if (!r.ok) throw new Error('Failed to load exams');
    const d = await r.json();
    renderExams(d.exams || []);
    _lastExamsFetch = Date.now();
  } catch (e) {
    if (!silent) {
      container.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load exams</strong>' + _escHtml(e.message||'') + '</div>';
    }
  } finally {
    _examsInflight = false;
  }
}

// Phase 2: if the dashboard is loaded inside the Procta Electron lobby,
// window.procta_native is injected by lobby_preload.js. Otherwise the
// student is in a regular browser and we show a "Launch in the Procta
// app" nudge instead of the Start button.
const INSIDE_PROCTA_APP = !!(window.procta_native && window.procta_native.isLobby);

// Stash the exams list so we can resolve a click back to its record
// when the access-code modal fires.
let _examsCache = [];
let _pendingExam = null;
let _pendingAccessCode = '';

function renderExams(exams) {
  _examsCache = exams;
  const container = document.getElementById('exams-container');
  if (!exams.length) {
    container.innerHTML = `
      <div class="exams-empty">
        <strong>No exams yet</strong>
        Once your teacher registers you for an exam with this email, it'll show up here.
        <div style="margin-top:14px">
          <a class="btn btn-secondary btn-sm" href="/register">Register for an exam</a>
        </div>
      </div>`;
    return;
  }
  container.innerHTML = exams.map((e, idx) => {
    const roll = e.roll_number || '—';
    const dur = e.duration_minutes ? (e.duration_minutes + ' min') : '—';
    const accessNote = e.access_code_required
      ? '<span class="badge badge-muted">Access code required</span>'
      : '';

    // Launchable states: either inside the active window or open now.
    // `in_progress` is also launchable because the renderer supports
    // resume (check-session endpoint).
    const launchable = e.status === 'open' || e.status === 'in_progress';
    // Countdown shown for in-progress exams that have an ends_at timestamp
    const countdownHtml = (e.status === 'in_progress' && e.ends_at)
      ? `<div style="font-size:13px;color:var(--amber);margin-top:4px">⏱ Time remaining: <strong><span class="exam-countdown" data-ends="${_escHtml(e.ends_at)}">—</span></strong></div>`
      : '';
    let actionHtml = '';
    if (launchable && INSIDE_PROCTA_APP) {
      actionHtml = `
        <div class="exam-actions">
          ${countdownHtml}
          <div class="exam-hint">Make sure your face is visible to the camera.</div>
          <button class="btn btn-primary btn-sm" data-action="startExamFromCard" data-args='[${idx}]'>Start exam</button>
        </div>`;
    } else if (launchable) {
      actionHtml = `
        <div class="exam-actions">
          ${countdownHtml}
          <div class="exam-hint">
            Open this in the Procta app to start.
            <a href="/download">Download the app</a>
          </div>
        </div>`;
    } else if (e.status === 'upcoming') {
      actionHtml = `
        <div class="exam-actions">
          <div class="exam-hint">Starts ${fmtWhen(e.starts_at)}</div>
        </div>`;
    } else if (e.status === 'closed') {
      actionHtml = `
        <div class="exam-actions">
          <div class="exam-hint">The exam window has closed.</div>
        </div>`;
    } else if (e.status === 'completed') {
      actionHtml = `
        <div class="exam-actions">
          <div class="exam-hint">Submitted ${fmtWhen(e.submitted_at)}</div>
        </div>`;
    } else if (e.status === 'force_submitted') {
      actionHtml = `
        <div class="exam-actions">
          <div class="exam-hint">Force-submitted ${fmtWhen(e.submitted_at)}</div>
        </div>`;
    }

    return `
      <div class="exam-card">
        <div class="exam-card-head">
          <div>
            <div class="exam-title">${_escHtml(e.exam_title || 'Exam')}</div>
            <div class="exam-teacher">${_escHtml(e.teacher_name || 'Teacher')}</div>
          </div>
          ${statusBadge(e.status)}
        </div>
        <div class="exam-meta">
          <div>Roll number<strong>${_escHtml(roll)}</strong></div>
          <div>Starts<strong>${fmtWhen(e.starts_at)}</strong></div>
          <div>Ends<strong>${fmtWhen(e.ends_at)}</strong></div>
          <div>Duration<strong>${dur}</strong></div>
        </div>
        <div class="placeholder-row">${accessNote}</div>
        ${actionHtml}
      </div>`;
  }).join('');
}

async function loadHistory(){
  const container = document.getElementById('history-container');
  if(!container) return;
  container.innerHTML = '<div class="exams-empty">Loading…</div>';
  try{
    const r = await authed('/api/student/history');
    if(r.status===401){ doLogout(); return; }
    if(!r.ok) throw new Error('Failed to load history');
    const d = await r.json();
    renderHistory(d.history||[]);
  }catch(e){
    container.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load history</strong></div>';
  }
}

function renderHistory(items){
  const container = document.getElementById('history-container');
  if(!items.length){
    container.innerHTML = '<div class="exams-empty">No completed exams yet</div>';
    return;
  }
  container.innerHTML = items.map(h=>{
    const pctColor = h.percentage>=40?'var(--emerald)':'var(--red)';
    const riskStr = h.risk_score!=null?_riskLabel(h.risk_score):'';
    return `
      <div class="exam-card">
        <div class="exam-card-head">
          <div>
            <div class="exam-title">${_escHtml(h.exam_title||'Exam')}</div>
            <div class="exam-teacher">${_escHtml(h.teacher_name||'')}</div>
          </div>
          <span class="badge" style="color:${pctColor};border-color:${pctColor}">${h.percentage}%</span>
        </div>
        <div class="exam-meta">
          <div>Score<strong>${h.score} / ${h.total}</strong></div>
          <div>Time<strong>${_fmtDuration(h.time_taken_secs)}</strong></div>
          <div>Violations<strong>${h.violation_count}</strong></div>
          ${riskStr?'<div>Risk<strong style="color:'+_riskColor(h.risk_score)+'">'+_escHtml(riskStr)+'</strong></div>':''}
          <div>Submitted<strong>${_escHtml(h.submitted_at)}</strong></div>
        </div>
        <div style="margin-top:8px">
          <button class="btn btn-sm btn-secondary" data-action="openAppeal" data-args='["${_escHtml(h.session_key)}"]'>Appeal</button>
        </div>
      </div>`;
  }).join('');
}

// ─── Appeal status read-back ──────────────────────────────────────
async function loadAppeals() {
  const container = document.getElementById('appeals-container');
  if (!container) return;
  container.innerHTML = '<div class="exams-empty">Loading…</div>';
  try {
    const r = await authed('/api/v1/student/appeals');
    if (r.status === 401) { doLogout(); return; }
    if (!r.ok) throw new Error('Failed to load appeals');
    const d = await r.json();
    renderAppeals(d.appeals || []);
  } catch (e) {
    container.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load appeals</strong></div>';
  }
}

function renderAppeals(items) {
  const container = document.getElementById('appeals-container');
  if (!items.length) {
    container.innerHTML = '<div class="exams-empty">No appeals submitted yet.</div>';
    return;
  }
  const statusColor = { pending: 'var(--amber)', accepted: 'var(--emerald)', rejected: 'var(--red)' };
  container.innerHTML = items.map(a => {
    const col = statusColor[a.status] || 'var(--muted)';
    const resolvedRow = a.resolved_at
      ? `<div>Resolved<strong>${_escHtml(a.resolved_at.slice(0, 10))}</strong></div>` : '';
    const noteRow = a.teacher_note
      ? `<div style="margin-top:8px;font-size:13px;color:var(--muted)">Teacher note: <em>${_escHtml(a.teacher_note)}</em></div>` : '';
    return `
      <div class="exam-card">
        <div class="exam-card-head">
          <div>
            <div class="exam-title">${_escHtml(a.appeal_type || 'Appeal')}</div>
            <div class="exam-teacher" style="font-size:12px;color:var(--muted)">${_escHtml(a.description ? a.description.slice(0, 80) + (a.description.length > 80 ? '…' : '') : '')}</div>
          </div>
          <span class="badge" style="color:${col};border-color:${col};text-transform:capitalize">${_escHtml(a.status)}</span>
        </div>
        <div class="exam-meta">
          <div>Type<strong>${_escHtml(a.appeal_type)}</strong></div>
          <div>Submitted<strong>${_escHtml((a.created_at || '').slice(0, 10))}</strong></div>
          ${resolvedRow}
        </div>
        ${noteRow}
      </div>`;
  }).join('');
}

// ─── Appeal ──────────────────────────────────────────────────────
let _appealSessionKey = '';

function openAppeal(sessionKey) {
  _appealSessionKey = sessionKey;
  document.getElementById('appeal-type').value = 'violation';
  document.getElementById('appeal-desc').value = '';
  document.getElementById('appeal-err').textContent = '';
  document.getElementById('appeal-modal').classList.add('active');
}

function closeAppeal() {
  document.getElementById('appeal-modal').classList.remove('active');
  _appealSessionKey = '';
}

async function submitAppeal() {
  const btn = document.getElementById('appeal-submit-btn');
  const err = document.getElementById('appeal-err');
  const type = document.getElementById('appeal-type').value;
  const desc = document.getElementById('appeal-desc').value.trim();
  if (!desc) { err.textContent = 'Describe your concern.'; return; }
  btn.disabled = true;
  err.textContent = '';
  try {
    const r = await authed('/api/v1/student/appeal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_key: _appealSessionKey, appeal_type: type, description: desc }),
    });
    const d = await r.json();
    if (r.ok) {
      err.style.color = 'var(--emerald)';
      err.textContent = d.message || 'Appeal submitted.';
      setTimeout(closeAppeal, 2000);
    } else {
      err.textContent = d.detail || 'Failed to submit.';
    }
  } catch (e) {
    err.textContent = 'Network error.';
  }
  btn.disabled = false;
}

function _riskLabel(score){
  if(score==null) return '';
  if(score<=15) return 'Low';
  if(score<=40) return 'Moderate';
  if(score<=70) return 'High';
  return 'Critical';
}
function _riskColor(score){
  if(score==null) return 'var(--muted)';
  if(score<=15) return 'var(--emerald)';
  if(score<=40) return 'var(--amber)';
  return 'var(--red)';
}
function _fmtDuration(secs){
  if(!secs && secs!==0) return '—';
  const m=Math.floor(secs/60), s=secs%60;
  return m>0?`${m}m ${s}s`:`${s}s`;
}

// ─── Preflight device check ─────────────────────────────────────
let _preflightResults = {};

function showPreflight() {
  document.getElementById('preflight-modal').classList.add('active');
  document.getElementById('preflight-start-btn').disabled = true;
  document.getElementById('preflight-err').textContent = '';
  _preflightResults = {};
  // Reset all rows
  document.querySelectorAll('.pf-row').forEach(r => {
    r.querySelector('.pf-icon').textContent = '\u231B';
    r.querySelector('.pf-status').textContent = 'Checking...';
    r.querySelector('.pf-status').style.color = '';
  });
  // Run all checks in parallel
  Promise.all([
    _pfCheckCamera(),
    _pfCheckBrowser(),
    _pfCheckBandwidth(),
  ]).then(() => {
    const ok = Object.values(_preflightResults).every(v => v === 'ok');
    document.getElementById('preflight-start-btn').disabled = !ok;
    if (!ok) {
      document.getElementById('preflight-err').textContent =
        'Some checks failed. You can fix and recheck, or try starting anyway.';
    }
  });
}

function closePreflight() {
  document.getElementById('preflight-modal').classList.remove('active');
  _pendingExam = null;
  _pendingAccessCode = '';
}

function launchAfterPreflight() {
  closePreflight();
  launchExam(_pendingExam, _pendingAccessCode);
}

function _setPfStatus(check, icon, status, color) {
  const row = document.querySelector(`[data-check="${check}"]`);
  if (!row) return;
  row.querySelector('.pf-icon').textContent = icon;
  row.querySelector('.pf-status').textContent = status;
  row.querySelector('.pf-status').style.color = color || '';
}

async function _pfCheckCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true });
    stream.getTracks().forEach(t => t.stop());
    _setPfStatus('camera', '\u2705', 'Working', 'var(--emerald)');
    _preflightResults.camera = 'ok';
  } catch (e) {
    _setPfStatus('camera', '\u274C', 'Not accessible', 'var(--red)');
    _preflightResults.camera = 'fail';
  }
}

function _pfCheckBrowser() {
  const issues = [];
  if (typeof WebSocket === 'undefined') issues.push('No WebSocket');
  if (!navigator.onLine) issues.push('Offline');
  const ua = navigator.userAgent || '';
  if (!ua) issues.push('Unknown browser');
  if (issues.length) {
    _setPfStatus('browser', '\u26A0\uFE0F', issues.join(', '), 'var(--amber)');
    _preflightResults.browser = 'warn';
  } else {
    _setPfStatus('browser', '\u2705', 'Compatible', 'var(--emerald)');
    _preflightResults.browser = 'ok';
  }
}

async function _pfCheckBandwidth() {
  const start = performance.now();
  try {
    const r = await fetchWithTimeout(apiUrl('/health?t=' + Date.now()), { cache: 'no-store' });
    const ms = Math.round(performance.now() - start);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (ms < 300) {
      _setPfStatus('bandwidth', '\u2705', ms + 'ms', 'var(--emerald)');
      _preflightResults.bandwidth = 'ok';
    } else if (ms < 1000) {
      _setPfStatus('bandwidth', '\u26A0\uFE0F', ms + 'ms (slow)', 'var(--amber)');
      _preflightResults.bandwidth = 'warn';
    } else {
      _setPfStatus('bandwidth', '\u274C', ms + 'ms (too slow)', 'var(--red)');
      _preflightResults.bandwidth = 'fail';
    }
  } catch (e) {
    _setPfStatus('bandwidth', '\u274C', 'Unreachable', 'var(--red)');
    _preflightResults.bandwidth = 'fail';
  }
}

// ─── Practice mode launch ─────────────────────────────────────
// Generates a one-shot PRACTICE_<8 hex> roll number and routes
// through the existing launchExam IPC. The server detects the
// PRACTICE_ prefix on every endpoint that touches the renderer
// and returns mock data / no-op responses, so there's nothing to
// rebuild on the Electron main side — the existing IPC contract
// is reused as-is.
async function startPracticeExam() {
  if (!INSIDE_PROCTA_APP) {
    showModal('Open this page inside the Procta app to start a practice run.');
    return;
  }
  // Crypto-random ID so two students starting practice on the same
  // box don't collide in the (admittedly unlikely) case where the
  // backend ever caches per-session-id state.
  const rand = Array.from(crypto.getRandomValues(new Uint8Array(4)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  const practiceRoll = 'PRACTICE_' + rand.toUpperCase();
  try {
    await window.procta_native.launchExam({
      rollNumber: practiceRoll,
      accessCode: '',
      examTitle:  'Practice — Setup Test',
      teacherId:  null,
      examId:     null,
    });
  } catch (e) {
    showModal((e && e.message) || 'Failed to start practice run.');
  }
}

// ─── Start-exam flow ──────────────────────────────────────────
function startExamFromCard(idx) {
  const exam = _examsCache[idx];
  if (!exam) return;
  _pendingExam = exam;
  if (exam.access_code_required) {
    openCodeModal();
  } else {
    showPreflight();
  }
}

function openCodeModal() {
  document.getElementById('modal-code').value = '';
  document.getElementById('modal-err').textContent = '';
  document.getElementById('code-modal').classList.add('active');
  setTimeout(() => document.getElementById('modal-code').focus(), 50);
}

function closeCodeModal() {
  document.getElementById('code-modal').classList.remove('active');
  _pendingExam = null;
}

async function confirmStartExam() {
  if (!_pendingExam) return;
  const code = document.getElementById('modal-code').value.trim().toUpperCase();
  if (!code) {
    document.getElementById('modal-err').textContent = 'Enter the access code from your teacher.';
    return;
  }
  document.getElementById('modal-err').textContent = '';
  _pendingAccessCode = code;
  closeCodeModal();
  showPreflight();
}

async function launchExam(exam, accessCode) {
  if (!INSIDE_PROCTA_APP) {
    showModal('Open this page inside the Procta app to start your exam.');
    return;
  }
  const btn = document.getElementById('modal-start-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  try {
    await window.procta_native.launchExam({
      rollNumber: exam.roll_number,
      accessCode: accessCode || '',
      examTitle:  exam.exam_title || 'Exam',
      teacherId:  exam.teacher_id,
      examId:     exam.exam_id || null,
    });
    // Main process hides the lobby on success; this JS will stop running.
  } catch (e) {
    const msg = (e && e.message) || 'Failed to start exam';
    document.getElementById('modal-err').textContent = msg;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Start exam'; }
  }
}

// Modal dismiss: Escape key + backdrop click.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('code-modal').classList.contains('active')) {
    closeCodeModal();
  }
});
document.getElementById('code-modal').addEventListener('click', (e) => {
  if (e.target.id === 'code-modal') closeCodeModal();
});
document.getElementById('modal-code').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') confirmStartExam();
});

// ─── invite deep-link (procta://invite/<token>) ──────────────
// When the student clicks "Open in Procta app" on their email landing
// page, the OS launches (or focuses) this app with the invite token.
// main.js parks it until the lobby DOM is ready, then exposes it via
// the preload. We resolve it to {email, full_name, exam_title} and
// pre-fill the auth form. After a successful login/signup the bound
// accept call links the student account to the invite row.
let _pendingInvite = null; // {token, email, full_name, exam_title, exam_id}
async function _handleInviteToken(token){
  if (!token || _pendingInvite) return;
  try {
    const r = await fetchWithTimeout(apiUrl('/api/v1/invite/' + encodeURIComponent(token) + '/resolve'));
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      console.warn('[invite] resolve failed:', r.status, d.detail || '');
      return; // silent — fall back to the normal login form
    }
    const inv = await r.json();
    _pendingInvite = {
      token,
      email:       (inv.email || '').trim().toLowerCase(),
      full_name:   inv.full_name || '',
      exam_title:  inv.exam_title || '',
      exam_id:     inv.exam_id || '',
    };
    _applyInvitePrefill();
  } catch(e) {
    console.error('[invite] resolve error:', e);
  }
}
function _applyInvitePrefill(){
  if (!_pendingInvite) return;
  // If we already have a live session, don't override — just try
  // acceptance immediately.
  if (studentAuthed) { _acceptPendingInvite(); return; }
  // Pre-fill the email and lock it so the student can't accidentally
  // type a different address (which would fail server-side anyway).
  const em = document.getElementById('inp-email');
  if (em) { em.value = _pendingInvite.email; em.readOnly = true; }
  // Show a small banner above the auth card.
  let banner = document.getElementById('invite-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'invite-banner';
    banner.style.cssText = 'background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.35);'
      + 'border-radius:10px;padding:12px 14px;margin:0 0 14px 0;color:#6ee7b7;font-size:13px;line-height:1.5';
    const card = document.querySelector('.auth-card');
    if (card) card.insertBefore(banner, card.firstChild.nextSibling);
  }
  banner.innerHTML = 'You\'re accepting an invite for <b>'
    + _escHtml(_pendingInvite.exam_title || 'your exam') + '</b>.'
    + ' Sign in or sign up with <b>' + _escHtml(_pendingInvite.email) + '</b> to continue.';
  // If the email doesn't exist yet on Procta, signup makes more sense
  // than login. Default to signup and pre-fill the name.
  setAuthMode('signup');
  const nm = document.getElementById('inp-name');
  if (nm && _pendingInvite.full_name) nm.value = _pendingInvite.full_name;
}
async function _acceptPendingInvite(){
  if (!_pendingInvite || !studentAuthed) return;
  const token = _pendingInvite.token;
  _pendingInvite = null; // one-shot
  try {
    const r = await authed('/api/invite/' + encodeURIComponent(token) + '/accept', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      console.warn('[invite] accept failed:', r.status, d.detail || '');
      return;
    }
    // The invite is now linked to this student_account. Upcoming
    // exam list refresh will pick up the exam assignment.
    console.log('[invite] accepted, refreshing exam list');
    try { await loadExams({ silent: false }); } catch(e){}
  } catch(e) {
    console.error('[invite] accept error:', e);
  }
}

// ─── boot: route between web landing vs Electron dashboard ──
(async function init(){
  if (!INSIDE_PROCTA_APP) {
    // On the web: show the simple landing page, hide everything else
    document.getElementById('web-landing').style.display = 'flex';
    document.getElementById('auth-view').style.display = 'none';
    return;
  }
  // Inside Electron: show the full auth/dashboard flow
  document.getElementById('web-landing').style.display = 'none';
  document.getElementById('auth-view').style.display = '';

  // Attach invite-token listeners BEFORE the auto-login check so that
  // cold-launch via procta:// never misses the token. The preload
  // exposes two paths — consumeInviteToken() for tokens that arrived
  // before we registered, and onInviteToken() for ones that arrive
  // after the lobby is already up (second click while running).
  try {
    if (window.procta_native && window.procta_native.onInviteToken) {
      window.procta_native.onInviteToken((tok) => { _handleInviteToken(tok); });
    }
    if (window.procta_native && window.procta_native.consumeInviteToken) {
      const tok = await window.procta_native.consumeInviteToken();
      if (tok) await _handleInviteToken(tok);
    }
  } catch(e) { console.error('[invite] wire-up failed', e); }

  try {
    const r = await authed('/api/v1/student/auth/me');
    if (r.ok) {
      const me = await r.json();
      await ensureStudentCsrf(true);
      await showDashboard(me);
      // If a token resolved BEFORE we knew we were already signed in,
      // accept it now against the live session.
      if (_pendingInvite) await _acceptPendingInvite();
    } else if (r.status === 401) {
      // stored tokens are both expired
      doLogout();
    }
  } catch {}
})();

// Re-check auth when page is restored from bfcache (back/forward navigation)
window.addEventListener('pageshow', (e) => {
  if (e.persisted) {
    if (!document.getElementById('web-landing')?.style.display?.includes('flex')) {
      authed('/api/v1/student/auth/me').then(async (r) => {
        if (r.ok) await showDashboard(await r.json());
        else document.getElementById('auth-view').style.display = '';
      }).catch(() => { document.getElementById('auth-view').style.display = ''; });
    }
  }
});

// ─── refetch exams when the lobby regains focus ───────────────
// Phase 2: the main process hides (not destroys) the lobby window when
// an exam launches and re-shows it when the kiosk releases. Without
// this, the cached exam list still shows "Start exam" on the just-
// finished exam because nothing told the dashboard to refresh. Listen
// for window focus and visibilitychange and re-fetch /api/student/exams
// whenever we come back into view, but only if the user is logged in
// and the dashboard view is currently visible (not the auth view).
// Only refetch if (a) logged in, (b) on dashboard view, and (c) the cached
// list is at least STALE_MS old. Without the stale check the dashboard
// reloaded on every alt-tab which made it feel like a permanent loading
// spinner during testing.
const EXAMS_STALE_MS = 60_000;
function _shouldRefetchExams() {
  if (!studentAuthed) return false;
  if (document.getElementById('dashboard').style.display !== 'block') return false;
  return (Date.now() - _lastExamsFetch) > EXAMS_STALE_MS;
}
window.addEventListener('focus', () => {
  if (_shouldRefetchExams()) loadExams({ silent: true });
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && _shouldRefetchExams()) loadExams({ silent: true });
});

// ─── Countdown ticker for in-progress exams ──────────────────────
// Ticks every second and updates all .exam-countdown[data-ends] spans.
function _fmtCountdown(ms) {
  if (ms <= 0) return 'Time up';
  const totalSecs = Math.floor(ms / 1000);
  const h = Math.floor(totalSecs / 3600);
  const m = Math.floor((totalSecs % 3600) / 60);
  const s = totalSecs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
setInterval(function _tickCountdowns() {
  const spans = document.querySelectorAll('.exam-countdown[data-ends]');
  const now = Date.now();
  spans.forEach(span => {
    const ends = new Date(span.dataset.ends).getTime();
    span.textContent = _fmtCountdown(ends - now);
    const remaining = ends - now;
    // Turn red when under 5 minutes
    span.style.color = remaining < 5 * 60 * 1000 ? 'var(--red)' : 'inherit';
  });
}, 1000);
function showModal(title, message){
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').textContent = message;
  document.getElementById('modal-overlay').style.display = 'flex';
}
function closeModal(){
  document.getElementById('modal-overlay').style.display = 'none';
}

function _parseDataArgs(raw) {
  try { return JSON.parse(raw || '[]'); } catch (err) { console.warn('[delegated] invalid data-args', err); return []; }
}

const _BLOCKED_DELEGATED_ACTIONS = new Set(['close', 'open', 'name', 'blur', 'focus', 'status', 'print', 'alert', 'confirm', 'prompt', 'eval', 'Function', 'fetch']);
function _resolveDelegatedAction(name) {
  if (!/^[A-Za-z_$][\w$]*$/.test(name || '') || _BLOCKED_DELEGATED_ACTIONS.has(name)) return null;
  const fn = window[name];
  return typeof fn === 'function' ? fn : null;
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || !el.dataset.action) return;
  if (el.dataset.guardSelf !== undefined && e.target !== el) return;
  if (e.target.closest('a') === el) e.preventDefault();
  const fn = _resolveDelegatedAction(el.dataset.action);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.args));
});

document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-change-action]');
  if (!el || !el.dataset.changeAction) return;
  const fn = _resolveDelegatedAction(el.dataset.changeAction);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.changeArgs));
});

document.addEventListener('input', (e) => {
  const el = e.target.closest('[data-input-action]');
  if (!el || !el.dataset.inputAction) return;
  const fn = _resolveDelegatedAction(el.dataset.inputAction);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.inputArgs));
});

document.addEventListener('keydown', (e) => {
  const el = e.target.closest('[data-keydown-action]');
  if (!el || !el.dataset.keydownAction) return;
  const wantKey = el.dataset.keydownKey || '';
  if (wantKey && e.key !== wantKey) return;
  const fn = _resolveDelegatedAction(el.dataset.keydownAction);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.keydownArgs));
});
