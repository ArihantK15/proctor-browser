(function(){
  // Consume LTI / OAuth access token from URL fragment (never sent to server)
  if (window.location.hash && window.location.hash.includes('access_token=')) {
    var params = new URLSearchParams(window.location.hash.substring(1));
    var tok = params.get('access_token');
    if (tok) {
      window.__proctaFragmentToken = tok;
      // Scrub the fragment so it doesn't leak into history or future navigations
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }
})();
const BASE = location.origin;
let authToken = window.__proctaFragmentToken || '';
let refreshToken = '';
let liveData = [], resultsData = [];
let liveSortKey = 'last_seen', liveSortAsc = false;
let resSortKey = 'submitted_at', resSortAsc = false;
let currentSessionId = null;
let autoRefreshTimer = null;
let currentTeacherId = null;
let currentTeacherFilter = '';
let orgTeacherOptions = [];
let issuesData = [];
let currentIssueId = null;
let issueReportContext = {session_id:'', exam_id:''};
let currentTeacherProfile = null;
let razorpayCheckoutPromise = null;
// Restore the previously-selected exam across page refreshes. Without
// this, F5 silently swaps you to examsList[0], and live sessions / results
// / schedule all blank out because they filter by exam_id.
let currentExamId = localStorage.getItem('procta_current_exam') || null;
let examsList = [];
let _refreshGen = 0; // incremented on exam switch to discard stale responses
let _liveViewSid = null;
let _liveViewLastFrameAt = 0;
let _liveViewFrameTimer = null;
let _liveViewKeepaliveTimer = null;
let _liveViewStaleTimer = null;
let _csrfTokenMemory = '';

// ── AUTH ─────────────────────────────────────────────────────────

// Turnstile state — loaded from /api/v1/public-config on init.
// Token is null when Turnstile is not configured (dev sandbox).
let _turnstileToken = null;
let _turnstileSiteKey = '';

async function _loadPublicConfig() {
  try {
    const r = await fetchWithTimeout(`${BASE}/api/v1/public-config`);
    if (r.ok) {
      const cfg = await r.json();
      _turnstileSiteKey = cfg.turnstile_site_key || '';
    }
  } catch(e) {}
}

async function fetchWithTimeout(url, opts={}, timeoutMs=30000){
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
  const merged = {...opts, signal: opts.signal || ctrl.signal};
  try{
    return await fetch(url, merged);
  }catch(e){
    if(e && e.name === 'AbortError') {
      const err = new Error('Request timed out. Please check your connection and try again.');
      err.code = 'REQUEST_TIMEOUT';
      err.url = String(url || '');
      throw err;
    }
    if(e && e.name === 'TypeError' && !e.code) {
      e.code = 'NETWORK_ERROR';
      e.url = String(url || '');
    }
    throw e;
  }finally{
    clearTimeout(timer);
  }
}

function _initTurnstile() {
  if (!_turnstileSiteKey) return;  // dev sandbox — skip
  const el = document.getElementById('cf-turnstile-login');
  if (!el || el.dataset.rendered) return;
  el.dataset.rendered = '1';
  if (window.turnstile) {
    window.turnstile.render(el, {
      sitekey: _turnstileSiteKey,
      theme: 'dark',
      callback: (token) => { _turnstileToken = token; },
      'expired-callback': () => { _turnstileToken = null; },
      'error-callback': () => { _turnstileToken = null; },
    });
  }
}

function _resetTurnstile() {
  _turnstileToken = null;
  if (_turnstileSiteKey && window.turnstile) {
    try { window.turnstile.reset(document.getElementById('cf-turnstile-login')); } catch(e) {}
  }
}

function toggleAuthForm(mode){
  document.getElementById('auth-login').style.display = mode==='login' ? '' : 'none';
  document.getElementById('auth-reset').style.display = mode==='reset' ? '' : 'none';
  document.getElementById('auth-err').textContent = '';
  document.getElementById('reset-err').textContent = '';
  document.getElementById('reset-success').style.display = 'none';
  // Render Turnstile widget when login form becomes visible
  if (mode === 'login') _initTurnstile();
}

function _saveTokens(access, refresh){
  authToken = access || '';
  refreshToken = refresh || '';
  if(!access){
    _csrfTokenMemory = '';
  }
}

async function doLogin(){
  const email = document.getElementById('login-email').value.trim();
  const pwd = document.getElementById('login-pwd').value;
  if(!email||!pwd){ document.getElementById('auth-err').textContent='Enter email and password'; return; }
  const btn = document.getElementById('login-btn');
  const otpRow = document.getElementById('login-2fa-row');
  const otpInput = document.getElementById('login-2fa-code');
  btn.disabled = true; btn.textContent = 'Signing in...';
  try{
    const body = {email, password: pwd};
    if (_turnstileToken) body.captcha_token = _turnstileToken;
    // If the 2FA row is visible, include whatever code the user typed
    // (otherwise the server will email a new code on this attempt).
    if (otpRow && otpRow.style.display !== 'none') {
      const code = (otpInput && otpInput.value || '').trim();
      if (!code) {
        document.getElementById('auth-err').textContent = 'Enter the 6-digit code we emailed you.';
        return;
      }
      body.email_otp_code = code;
    }
    const r = await fetchWithTimeout(`${BASE}/api/v1/auth/login`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'include',
      body:JSON.stringify(body)
    });
    const data = await r.json();
    if(!r.ok){
      _resetTurnstile();
      // Email-OTP 2FA — server emailed a code and is asking the user
      // to retry with it. Show the OTP input + a friendly status, but
      // don't treat this as a hard error (no red flash).
      if (data && data.error === 'EMAIL_2FA_REQUIRED') {
        if (otpRow) otpRow.style.display = '';
        if (otpInput) {
          otpInput.value = '';
          otpInput.focus();
        }
        document.getElementById('auth-err').textContent = data.message || 'We emailed you a 6-digit code.';
        return;
      }
      throw new Error(data.detail||'Login failed');
    }
    _saveTokens(data.access_token, data.refresh_token);
    await _ensureCsrfToken(true);
    _onAuthed(data.teacher);
  }catch(e){
    document.getElementById('auth-err').textContent = e.message;
  }finally{
    btn.disabled = false; btn.textContent = 'Log In';
  }
}

async function doPasswordReset(){
  const email = document.getElementById('reset-email').value.trim();
  if(!email||!email.includes('@')){ document.getElementById('reset-err').textContent='Enter a valid email'; return; }
  const btn = document.getElementById('reset-btn');
  btn.disabled = true; btn.textContent = 'Sending...';
  try{
    const body = {email};
    if (_turnstileToken) body.captcha_token = _turnstileToken;
    const r = await fetchWithTimeout(`${BASE}/api/v1/auth/password-reset`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({detail:'Failed to send reset link'}));
      throw new Error(d.detail||'Failed');
    }
    document.getElementById('reset-success').style.display = 'block';
    document.getElementById('reset-err').textContent = '';
  }catch(e){
    document.getElementById('reset-err').textContent = e.message;
  }finally{
    btn.disabled = false; btn.textContent = 'Send Reset Link';
  }
}

// Signup is handled at procta.net/signup — no inline form on the dashboard.

async function _onAuthed(teacher){
  document.getElementById('auth-overlay').classList.add('hidden');
  currentTeacherProfile = teacher || null;
  _onAuthDone();
  if(teacher && teacher.full_name){
    document.getElementById('teacher-name').textContent = teacher.full_name;
  }
  if(teacher && teacher.id){
    currentTeacherId = teacher.id;
    _populateShareLinks(teacher.id);
  }
  await loadExams();
  refreshAll();
  // Try SSE for real-time updates; fall back to polling if unavailable
  _connectSSE();
  chatConnect();
}

let _sseSource = null;
let _sseFallbackTimer = null;

async function _connectSSE(){
  // Clean up any existing connection
  if(_sseSource){ try{_sseSource.close();}catch(_){} _sseSource=null; }
  if(_sseFallbackTimer){ clearInterval(_sseFallbackTimer); _sseFallbackTimer=null; }

  try{
    // Fetch a short-lived connect token (avoids putting JWT in URL)
    const ctr = await authFetch(`${BASE}/api/v1/sse/connect-token`, {
      method: 'POST',
    });
    if(!ctr.ok) throw new Error('connect-token failed');
    const { connect_token } = await ctr.json();

    _sseSource = new EventSource(`${BASE}/api/v1/sse/sessions?token=${encodeURIComponent(connect_token)}`);

    _sseSource.addEventListener('init', (e)=>{
      try{
        const d=JSON.parse(e.data);
        liveData=d.all_sessions||[];
        renderLiveStats(d.sessions||[],liveData);
        renderLive();
      }catch(err){ console.error('[SSE] init parse error',err); }
    });

    _sseSource.addEventListener('update', (e)=>{
      // Incremental update — refresh live data from server
      refreshLive();
      refreshIdReviews();
    });

    _sseSource.addEventListener('alert', (e)=>{
      try{
        const a=JSON.parse(e.data);
        handleRealtimeAlert(a);
      }catch(err){ console.error('[SSE] alert parse error',err); }
    });

    _sseSource.addEventListener('refresh', (e)=>{
      // Full refresh (fallback mode when no Redis)
      try{
        const d=JSON.parse(e.data);
        liveData=d.all_sessions||[];
        renderLiveStats(d.sessions||[],liveData);
        renderLive();
      }catch(err){ console.error('[SSE] refresh parse error',err); }
    });

    _sseSource.onerror = ()=>{
      console.warn('[SSE] connection error — falling back to polling');
      try{_sseSource.close();}catch(_){}
      _sseSource=null;
      // Fall back to polling
      _sseFallbackTimer = setInterval(()=>{ refreshLive(); refreshIdReviews(); }, 5000);
      // Retry SSE after 30s
      setTimeout(()=>{
        if(_sseFallbackTimer){
          clearInterval(_sseFallbackTimer);
          _sseFallbackTimer=null;
          _connectSSE();
        }
      }, 30000);
    };
  }catch(e){
    console.warn('[SSE] not available, using polling');
    _sseFallbackTimer = setInterval(()=>{ refreshLive(); refreshIdReviews(); }, 5000);
  }
}

// ── EXAM SELECTOR ──────────────────────────────────────────────
async function loadExams(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams`);
    if(!r.ok){
      document.getElementById('exam-bar').style.display='flex';
      document.getElementById('exam-count').textContent='Failed to load exams';
      document.getElementById('exam-count').style.color='var(--red)';
      return;
    }
    document.getElementById('exam-count').style.color='';
    const d = await r.json();
    examsList = d.exams || [];
    const sel = document.getElementById('exam-select');
    sel.innerHTML = '';
    examsList.forEach(ex=>{
      const opt = document.createElement('option');
      opt.value = ex.exam_id;
      opt.textContent = `${ex.exam_title || 'Untitled'} (${ex.question_count}Q, ${ex.session_count} sessions)`;
      sel.appendChild(opt);
    });
    // Restore previous selection or default to first
    if(currentExamId && examsList.find(e=>e.exam_id===currentExamId)){
      sel.value = currentExamId;
    } else if(examsList.length){
      currentExamId = examsList[0].exam_id;
      try{ localStorage.setItem('procta_current_exam', currentExamId || ''); }catch(_){}
      sel.value = currentExamId;
    }
    document.getElementById('exam-bar').style.display = 'flex';
    document.getElementById('exam-count').textContent = `${examsList.length} exam${examsList.length!==1?'s':''}`;
    // Show delete button only if >1 exam
    document.getElementById('delete-exam-btn').style.display = examsList.length > 1 ? '' : 'none';
    // Duplicate is always available once an exam is selected
    document.getElementById('duplicate-exam-btn').style.display = currentExamId ? '' : 'none';
  }catch(e){ console.error('loadExams', e); }
}

function onExamSwitch(examId){
  currentExamId = examId;
  try{ localStorage.setItem('procta_current_exam', examId || ''); }catch(_){}
  document.getElementById('delete-exam-btn').style.display = examsList.length > 1 ? '' : 'none';
  document.getElementById('duplicate-exam-btn').style.display = currentExamId ? '' : 'none';
  // Reset data and reload everything for the new exam
  liveData = []; resultsData = []; qData = [];
  refreshAll();
}

function _examQuery(sep){
  const params = [];
  if(currentExamId) params.push(`exam_id=${encodeURIComponent(currentExamId)}`);
  if(currentTeacherFilter) params.push(`teacher_id=${encodeURIComponent(currentTeacherFilter)}`);
  return params.length ? `${sep}${params.join('&')}` : '';
}

function _teacherQuery(sep){
  return currentTeacherFilter ? `${sep}teacher_id=${encodeURIComponent(currentTeacherFilter)}` : '';
}

function showCreateExamModal(){
  document.getElementById('create-exam-modal').classList.remove('hidden');
  document.getElementById('new-exam-title').value = '';
  document.getElementById('new-exam-duration').value = '60';
  document.getElementById('create-exam-err').textContent = '';
  document.getElementById('new-exam-title').focus();
}

function hideCreateExamModal(){
  document.getElementById('create-exam-modal').classList.add('hidden');
}

async function createExam(){
  const title = document.getElementById('new-exam-title').value.trim();
  const dur = parseInt(document.getElementById('new-exam-duration').value) || 60;
  const phoneCam = document.getElementById('new-exam-phone-cam').checked;
  if(!title){ document.getElementById('create-exam-err').textContent='Title is required'; return; }
  const btn = document.getElementById('create-exam-btn');
  btn.disabled = true; btn.textContent = 'Creating...';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams`,{
      method:'POST', body:JSON.stringify({exam_title:title, duration_minutes:dur, phone_camera:phoneCam})
    });
    if(!r.ok){ const d=await r.json(); throw new Error(d.detail||'Failed'); }
    const d = await r.json();
    hideCreateExamModal();
    currentExamId = d.exam_id;
    await loadExams();
    liveData = []; resultsData = []; qData = [];
    refreshAll();
  }catch(e){
    document.getElementById('create-exam-err').textContent = e.message;
  }finally{
    btn.disabled = false; btn.textContent = 'Create';
  }
}

async function confirmDeleteExam(){
  if(examsList.length <= 1){ showModal('Cannot delete the only exam.'); return; }
  const ex = examsList.find(e=>e.exam_id===currentExamId);
  const name = ex ? ex.exam_title : 'this exam';
  // Two-step deletion check — a single click is too easy to muscle-memory-OK
  // for an action that wipes every question on the exam. Type-to-confirm
  // forces the teacher to read the exam name back.
  if(!(await appConfirm(`Delete "${name}"? This removes ALL its questions. Session history is preserved.`, 'Delete exam', {okText:'Delete'}))) return;
  const typed = await appPrompt(`To confirm, type the exam name exactly:\n\n${name}`, '', {title:'Type exam name', okText:'Delete'});
  if(typed === null) return; // cancelled
  if((typed || '').trim() !== (name || '').trim()){
    showModal('Names do not match — delete cancelled.');
    return;
  }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${currentExamId}`,{method:'DELETE'});
    if(!r.ok){ const d=await r.json(); throw new Error(d.detail||'Failed'); }
    currentExamId = null;
    await loadExams();
    liveData = []; resultsData = []; qData = [];
    refreshAll();
  }catch(e){ showModal('Delete failed: '+e.message); }
}

// Clone current exam's config + questions into a new exam. The copy
// clears the schedule + access code (so the old window can't run
// twice) and shows up as the active exam immediately after creation.
async function duplicateCurrentExam(){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  const ex = examsList.find(e => e.exam_id === currentExamId);
  const srcTitle = ex ? ex.exam_title : 'this exam';
  const defaultTitle = `${srcTitle} (copy)`;
  const newTitle = await appPrompt(
    `Duplicate "${srcTitle}"?\n\n` +
    `A new exam will be created with the same questions, duration, and\n` +
    `shuffle settings. The schedule and access code will be cleared so\n` +
    `you can set them fresh.\n\n` +
    `Title for the new exam:`,
    defaultTitle,
    {title:'Duplicate exam', okText:'Duplicate'}
  );
  if (newTitle === null) return; // cancelled
  try {
    const r = await authFetch(
      `${BASE}/api/v1/admin/exams/${encodeURIComponent(currentExamId)}/duplicate`,
      { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_title: (newTitle || '').trim() }) }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    // Switch to the new exam immediately so the teacher lands in
    // editing mode on their fresh copy.
    currentExamId = d.exam_id;
    try { localStorage.setItem('procta_current_exam', d.exam_id || ''); } catch(_) {}
    await loadExams();
    const sel = document.getElementById('exam-select');
    if (sel) sel.value = d.exam_id;
    liveData = []; resultsData = []; qData = [];
    refreshAll();
    showModal(`Created "${d.exam_title}" with ${d.questions_copied} question(s).`);
  } catch (e) {
    showModal('Duplicate failed: ' + e.message);
  }
}

async function _tryAutoLogin(){
  try{
    // Cookie auth is primary. Bearer is only kept for one-shot OAuth/LTI
    // fragments before the backend sets HttpOnly cookies.
    let r = await fetchWithTimeout(`${BASE}/api/v1/auth/me`, {
      credentials:'include',
      headers: authToken ? {'Authorization':'Bearer '+authToken} : {},
    });
    if(r.ok){ await _ensureCsrfToken(true); _onAuthed(await r.json()); return; }

    // Token/cookie expired — try refresh. Modern sessions refresh via
    // HttpOnly cookies; legacy refreshToken is accepted during migration.
    const rr = await fetchWithTimeout(`${BASE}/api/v1/auth/refresh`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'include',
      body:JSON.stringify(refreshToken ? {refresh_token:refreshToken} : {})
    });
    if(!rr.ok){ _saveTokens('',''); return; }
    const rd = await rr.json();
    _saveTokens(rd.access_token, rd.refresh_token);
    await _ensureCsrfToken(true);

    // Retry with fresh token
    r = await fetchWithTimeout(`${BASE}/api/v1/auth/me`, {
      credentials:'include',
      headers: rd.access_token ? {'Authorization':'Bearer '+rd.access_token} : {},
    });
    if(r.ok){ _onAuthed(await r.json()); return; }
    _saveTokens('','');
  }catch(e){
    _saveTokens('','');
  }
}

async function doLogout(){
  try{
    await authFetch(`${BASE}/api/v1/auth/logout`, {method:'POST'});
  }catch(_){}
  _saveTokens('','');
  if(autoRefreshTimer){ clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  if(_sseSource){ try{_sseSource.close();}catch(_){} _sseSource=null; }
  if(_sseFallbackTimer){ clearInterval(_sseFallbackTimer); _sseFallbackTimer=null; }
  // Wipe in-memory state so a second teacher logging in on the same
  // browser never sees the previous teacher's data even momentarily.
  try{
    if(typeof liveData    !== 'undefined') liveData    = [];
    if(typeof resultsData !== 'undefined') resultsData = [];
    if(typeof currentSessionId !== 'undefined') currentSessionId = null;
    currentExamId = null; examsList = [];
    try{ localStorage.removeItem('procta_current_exam'); }catch(_){}
    document.querySelectorAll('#live-body, #results-body').forEach(el=>el.innerHTML='');
  }catch(_){}
  chatDisconnect();
  document.getElementById('auth-overlay').classList.remove('hidden');
  document.getElementById('teacher-name').textContent = '';
  document.getElementById('exam-bar').style.display = 'none';
  toggleAuthForm('login');
}

function _populateShareLinks(teacherId){
  const base = location.origin;
  document.getElementById('share-register-link').value = `${base}/register?t=${teacherId}`;
  document.getElementById('share-download-link').value = `${base}/download`;
}

function copyLink(inputId){
  const inp = document.getElementById(inputId);
  navigator.clipboard.writeText(inp.value).then(()=>{
    const btn = inp.parentElement.querySelector('button');
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.style.background = 'var(--emerald)';
    setTimeout(()=>{ btn.textContent = orig; btn.style.background = ''; }, 1500);
  }).catch(()=>{
    inp.select();
    document.execCommand('copy');
    const btn = inp.parentElement.querySelector('button');
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.style.background = 'var(--emerald)';
    setTimeout(()=>{ btn.textContent = orig; btn.style.background = ''; }, 1500);
  });
}

function hdr(){
  const h = {'Content-Type':'application/json'};
  if(authToken) h.Authorization = 'Bearer '+authToken;
  return h;
}

function _getCsrfToken(){
  return _csrfTokenMemory || '';
}

async function _ensureCsrfToken(force=false){
  const existing = _getCsrfToken();
  if(existing && !force) return existing;
  const headers = {};
  if(authToken) headers.Authorization = 'Bearer '+authToken;
  const r = await fetchWithTimeout(`${BASE}/api/v1/auth/csrf`, {
    credentials:'include',
    headers,
  });
  if(!r.ok) return '';
  const d = await r.json().catch(()=>({}));
  const csrf = d.csrf_token || '';
  if(csrf) _csrfTokenMemory = csrf;
  return csrf;
}

// Single-flight guard so concurrent 401s only trigger one /refresh call —
// without this, parallel admin requests can each refresh independently and
// the slower responses overwrite the newer rotated refresh_token, causing
// sporadic logouts on the next call.
let _refreshInFlight = null;
async function _refreshTokens(){
  if(_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = (async ()=>{
    const rr = await fetchWithTimeout(`${BASE}/api/v1/auth/refresh`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'include',
      body:JSON.stringify(refreshToken ? {refresh_token:refreshToken} : {})
    });
    if(!rr.ok) throw new Error('refresh failed');
    const rd = await rr.json();
    _saveTokens(rd.access_token, rd.refresh_token);
    await _ensureCsrfToken(true);
    return rd;
  })();
  try { return await _refreshInFlight; }
  finally { _refreshInFlight = null; }
}

// Wrapper: auto-refresh token on 401 and retry once. Always merges
// the Authorization header into caller-supplied headers so POST calls
// that pass {'Content-Type':'application/json'} still get authenticated.
async function authFetch(url, opts={}){
  opts.headers = {...hdr(), ...(opts.headers||{})};
  opts.credentials = opts.credentials || 'include';
  if(!opts.method || opts.method==='POST' || opts.method==='PUT' || opts.method==='PATCH' || opts.method==='DELETE'){
    const csrf = await _ensureCsrfToken();
    if(csrf) opts.headers['X-CSRF-Token'] = csrf;
  }
  let r = await fetchWithTimeout(url, opts);
  if(r.status===401){
    try{
      await _refreshTokens();
    }catch(_){
      doLogout();
      return r;
    }
    opts.headers = {...(opts.headers||{}), ...hdr()};
    if(!authToken) delete opts.headers.Authorization;
    if(!opts.method || opts.method==='POST' || opts.method==='PUT' || opts.method==='PATCH' || opts.method==='DELETE'){
      const csrf = await _ensureCsrfToken();
      if(csrf) opts.headers['X-CSRF-Token'] = csrf;
    }
    r = await fetchWithTimeout(url, opts);
  }
  return r;
}

// Load public config (e.g. Turnstile site key) before showing auth.
// Fire-and-forget: if it fails the dashboard still works in sandbox mode.
_loadPublicConfig().then(() => _initTurnstile());

// Auto-login on page load
_tryAutoLogin();

// Re-check auth when page is restored from bfcache (back/forward navigation)
window.addEventListener('pageshow', (e) => {
  if (e.persisted) {
    // Page was restored from bfcache — re-validate the HttpOnly cookie.
    _tryAutoLogin();
  }
});

// ── Onboarding wizard ────────────────────────────────────────────
// Auto-shows on first dashboard load. Persists "done" in
// localStorage so a teacher only sees it once unless they manually
// re-trigger via the "?" button in the topbar (wired up at boot).
//
// Steps are an array of {title, body, cta_hint} so adding a new
// step is just an array entry — no per-step DOM template needed.
const _ONBOARD_STEPS = [
  {
    title: 'Welcome to Procta',
    body: `<p style="font-size:14px;color:var(--text-mid);line-height:1.6;margin-bottom:14px">
        You can run your first proctored exam in <strong style="color:var(--accent)">about 5 minutes</strong>. This 1-minute tour shows the four things you'll touch.
      </p>
      <div style="background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;padding:14px;font-size:13px;color:var(--text-muted);line-height:1.7">
        <strong style="color:var(--text-high);display:block;margin-bottom:6px">What Procta does</strong>
        AI watches every student's webcam + screen for cheating signals (gaze, multiple faces, tab switches). Auto-grades MCQs the moment they submit. Generates per-student PDF scorecards. Zero install on your servers — students download the desktop app, you watch from the browser.
      </div>`,
  },
  {
    title: 'Step 1 — Build your exam',
    body: `<p style="font-size:14px;color:var(--text-mid);line-height:1.6;margin-bottom:14px">
        Click the <strong style="color:var(--accent)">Questions</strong> tab. You have three ways to add questions:
      </p>
      <div style="display:flex;flex-direction:column;gap:10px">
        <div style="background:var(--surface-1);border:1px solid var(--accent-border);border-radius:8px;padding:12px 14px">
          <div style="font-size:13px;font-weight:600;color:var(--accent);margin-bottom:3px">✨ AI Generate (fastest)</div>
          <div style="font-size:12px;color:var(--text-muted)">Type a topic ("Photosynthesis"), pick how many, click Generate. 25 questions in 2 seconds. Review, edit, then save.</div>
        </div>
        <div style="background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;padding:12px 14px">
          <div style="font-size:13px;font-weight:600;color:var(--text-high);margin-bottom:3px">+ Add Question</div>
          <div style="font-size:12px;color:var(--text-muted)">Type your own. MCQ single, multi-select, or true/false.</div>
        </div>
        <div style="background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;padding:12px 14px">
          <div style="font-size:13px;font-weight:600;color:var(--text-high);margin-bottom:3px">Import CSV</div>
          <div style="font-size:12px;color:var(--text-muted)">Bulk import from a spreadsheet. Template available in the Bank panel.</div>
        </div>
      </div>`,
  },
  {
    title: 'Step 2 — Share with students',
    body: `<p style="font-size:14px;color:var(--text-mid);line-height:1.6;margin-bottom:14px">
        Click the <strong style="color:var(--accent)">Tools</strong> tab. Two ways to onboard students:
      </p>
      <div style="background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;padding:14px;font-size:13px;color:var(--text-mid);line-height:1.7;margin-bottom:10px">
        <strong style="color:var(--text-high);display:block;margin-bottom:4px">Send invite emails</strong>
        Paste a list of <code style="background:var(--surface-2);padding:1px 6px;border-radius:3px;font-size:11px">name, email, roll</code> rows; we send each student a unique link. They click → land on a download page → install the Procta app → join your exam. Recommended for batches > 20.
      </div>
      <div style="background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;padding:14px;font-size:13px;color:var(--text-mid);line-height:1.7">
        <strong style="color:var(--text-high);display:block;margin-bottom:4px">Share an access code</strong>
        Tools → Access Code → copy the 6-letter code. Students join by entering their roll number + this code. Simpler for small classes / drop-in exams.
      </div>`,
  },
  {
    title: 'Step 3 — Watch live',
    body: `<p style="font-size:14px;color:var(--text-mid);line-height:1.6;margin-bottom:14px">
        Once students start, the <strong style="color:var(--accent)">Live Sessions</strong> tab fills up in real time. Each row shows:
      </p>
      <ul style="font-size:13px;color:var(--text-mid);line-height:1.8;padding-left:20px;margin-bottom:14px">
        <li><strong>Severity</strong> — colour-coded violation level</li>
        <li><strong>Risk score</strong> — 0–100, higher means more flags</li>
        <li><strong>✨ Insight</strong> — one-line AI summary of what's happening</li>
        <li><strong>Camera</strong> — peek at the live webcam feed</li>
        <li><strong>Timeline</strong> — every event, frame-by-frame</li>
      </ul>
      <div style="background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:8px;padding:12px 14px;font-size:12px;color:var(--text-mid);line-height:1.55">
        <strong style="color:var(--accent)">Tip:</strong> AI never auto-terminates a session. Every flag is a recommendation; you decide.
      </div>`,
  },
  {
    title: 'You\'re ready',
    body: `<div style="text-align:center;padding:20px 0">
        <div style="font-size:48px;margin-bottom:14px">🎯</div>
        <p style="font-size:16px;color:var(--text-high);font-weight:600;margin-bottom:8px">Set up your first exam now</p>
        <p style="font-size:13px;color:var(--text-muted);line-height:1.6;max-width:380px;margin:0 auto">
          Three clicks: Questions → ✨ Generate → +N → Exam.<br>
          You'll have a usable exam in under 60 seconds.
        </p>
        <div style="margin-top:24px;padding:12px 16px;background:var(--surface-1);border:1px solid var(--border-subtle);border-radius:8px;font-size:12px;color:var(--text-muted);line-height:1.6;max-width:380px;margin-left:auto;margin-right:auto">
          Need help? The <strong style="color:var(--accent)">?</strong> button (top-right) reopens this tour anytime. For real questions: <span style="color:var(--accent)">support@procta.net</span>
        </div>
      </div>`,
  },
];
let _onboardIdx = 0;

function _onboardRender(){
  const dots = document.getElementById('onboard-dots');
  const content = document.getElementById('onboard-content');
  const prev = document.getElementById('onboard-prev-btn');
  const next = document.getElementById('onboard-next-btn');
  const skip = document.getElementById('onboard-skip-btn');
  if(!dots || !content) return;
  // Render dots
  dots.innerHTML = _ONBOARD_STEPS.map((_, i) => `
    <button data-action="_onboardJump" data-args='${_jsonArgsForAttr(i)}' title="Step ${i+1}"
      style="width:${i===_onboardIdx?'24px':'8px'};height:8px;border-radius:99px;
             background:${i===_onboardIdx?'var(--accent)':'var(--border-strong)'};
             border:none;cursor:pointer;padding:0;
             transition:width var(--duration-fast) var(--ease-standard),background var(--duration-fast) var(--ease-standard)"></button>
  `).join('');
  // Render step content
  const step = _ONBOARD_STEPS[_onboardIdx];
  content.innerHTML = `
    <h2 style="font-size:22px;font-weight:600;letter-spacing:-0.02em;color:var(--text-high);margin-bottom:14px">${step.title}</h2>
    ${step.body}
  `;
  // Button states
  prev.disabled = (_onboardIdx === 0);
  prev.style.opacity = prev.disabled ? '0.4' : '1';
  prev.style.cursor = prev.disabled ? 'not-allowed' : 'pointer';
  if(_onboardIdx === _ONBOARD_STEPS.length - 1){
    next.textContent = 'Got it — let\'s go';
    skip.textContent = 'Close';
  } else {
    next.textContent = 'Next →';
    skip.textContent = 'Skip tour';
  }
}

function onboardOpen(){
  _onboardIdx = 0;
  document.getElementById('onboard-modal').classList.remove('hidden');
  _onboardRender();
}

function onboardNext(){
  if(_onboardIdx < _ONBOARD_STEPS.length - 1){
    _onboardIdx++;
    _onboardRender();
  } else {
    _onboardComplete();
  }
}

function onboardPrev(){
  if(_onboardIdx > 0){
    _onboardIdx--;
    _onboardRender();
  }
}

function _onboardJump(i){
  _onboardIdx = Math.max(0, Math.min(_ONBOARD_STEPS.length - 1, i));
  _onboardRender();
}

function onboardSkip(){
  // Skip and complete are the same action — both mark "seen" in
  // localStorage. The user opted out of the wizard either way; we
  // shouldn't pop it again next time they log in.
  _onboardComplete();
}

function _onboardComplete(){
  try{ localStorage.setItem('procta_onboarded', 'true'); }catch(_){/* private mode */}
  document.getElementById('onboard-modal').classList.add('hidden');
}

// Auto-show on first dashboard load. Two checks:
//   1. localStorage flag — never seen before
//   2. dashboard fully loaded (auth-overlay hidden) — don't fire on
//      the login screen, only after the teacher is signed in
function _onboardMaybeShow(){
  let seen = false;
  try{ seen = localStorage.getItem('procta_onboarded') === 'true'; }catch(_){}
  if(seen) return;
  const auth = document.getElementById('auth-overlay');
  // Wait for auth-overlay to be hidden (teacher logged in)
  if(auth && auth.classList.contains('hidden') === false){
    // Re-check every 500ms until login completes; cap at 30 seconds.
    let attempts = 0;
    const probe = setInterval(() => {
      attempts++;
      if(attempts > 60){
        clearInterval(probe);
        return;
      }
      if(!auth.classList.contains('hidden')){
        clearInterval(probe);
        onboardOpen();
      }
    }, 500);
    return;
  }
  onboardOpen();
}

// Add a "?" help button to the topbar so the wizard is re-openable
// after dismissal. Inserted on DOMContentLoaded so the topbar exists.
document.addEventListener('DOMContentLoaded', () => {
  const right = document.querySelector('.topbar-right') || document.querySelector('.topbar');
  if(right && !document.getElementById('onboard-trigger')){
    const btn = document.createElement('button');
    btn.id = 'onboard-trigger';
    btn.className = 'btn btn-secondary btn-sm';
    btn.title = 'Open the getting-started tour';
    btn.style.cssText = 'padding:6px 12px;font-size:13px;font-weight:600';
    btn.textContent = '?';
    btn.onclick = () => onboardOpen();
    right.insertBefore(btn, right.firstChild);
  }
  // Trigger maybe-show after a short delay so login flow has time
  // to flip the auth-overlay class.
  setTimeout(_onboardMaybeShow, 800);

  // Restore tab from URL hash (set by switchTab) so refresh doesn't
  // lose the active tab. Runs after OAuth token scrubbing (the IIFE
  // above) so the hash is clean.
  var _hashTab = window.location.hash.match(/^#tab-(.+)$/);
  if (_hashTab) {
    var _tabName = _hashTab[1];
    if (_tabButtonForName(_tabName)) {
      switchTab(_tabName);
    }
  }
});

// Handle browser back/forward for tab history
window.addEventListener('hashchange', function(){
  var m = window.location.hash.match(/^#tab-(.+)$/);
  if (m) {
    var t = m[1];
    if (_tabButtonForName(t)) {
      switchTab(t);
    }
  }
});

// ── TABS ────────────────────────────────────────────────────────
function _tabButtonForName(tab){
  if(!/^[a-z0-9-]+$/i.test(tab || '')) return null;
  return document.querySelector('.tab[data-tab="' + tab + '"]');
}

function switchTab(tab){
  // Update both the visual active state and the ARIA state in lockstep
  // so screen readers announce the change. Without aria-selected,
  // keyboard users hear no feedback when they arrow between tabs.
  document.querySelectorAll('.tab').forEach(t => {
    const on = t.dataset.tab === tab;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+tab));
  // Defensive: _resDisconnectObserver was removed during an earlier
  // refactor but this call site was missed. Guarded the same way as
  // refreshPendingGradeBadge below so a missing observer can't throw
  // and break tab switching.
  if(tab!=='results' && typeof _resDisconnectObserver==='function') _resDisconnectObserver();
  if(tab==='results' && resultsData.length===0) refreshResults();
  if(tab==='results' && typeof refreshPendingGradeBadge==='function') refreshPendingGradeBadge();
  if(tab==='questions' && qData.length===0) loadQuestions();
  if(tab==='chat'){
    if(typeof chatClearActiveUnread==='function') chatClearActiveUnread();
    if(typeof chatClearTabBadge==='function') chatClearTabBadge();
  }
  if(tab==='analytics') loadAnalytics();
  if(tab==='history') refreshStudentList();
   if(tab==='tools'){
     try{ if(typeof loadRegisteredCount==='function') loadRegisteredCount(); }catch(_){}
     try{ if(typeof loadAccessCode==='function') loadAccessCode(); }catch(_){}
     try{ if(typeof loadSchedule==='function') loadSchedule(); }catch(_){}
     try{ if(typeof loadShuffleConfig==='function') loadShuffleConfig(); }catch(_){}
     try{ if(typeof loadTemplates==='function') loadTemplates(); }catch(_){}
     try{ if(typeof loadGoogleClassroom==='function') loadGoogleClassroom(); }catch(_){}
   }
  if(tab==='org') loadOrgOverview();
  if(tab==='security') loadSecurity();
  if(tab==='members') loadMembers();
  if(tab==='billing') loadBilling();
  if(tab==='org-settings') loadOrgSettings();
  if(tab==='all-orgs') loadAllOrgs();
  if(tab==='issues') loadIssues();
  // Persist tab in URL hash so refresh doesn't lose state
  if (window.location.hash !== '#tab-' + tab) {
    history.replaceState(null, '', '#tab-' + tab);
  }
}

// Standard ARIA tablist keyboard pattern: ArrowLeft / ArrowRight cycle
// through tabs (wrapping at the ends), Home/End jump to first/last.
// Wired once on page load so dynamically rendered tab content doesn't
// need to re-bind. Idempotent.
(function _initTabKeyboard(){
  const tabs = document.querySelectorAll('.tabs [role="tab"]');
  if(!tabs.length) return;
  tabs.forEach((tab, idx) => {
    tab.addEventListener('keydown', (e) => {
      let target = null;
      if(e.key === 'ArrowRight') target = tabs[(idx + 1) % tabs.length];
      else if(e.key === 'ArrowLeft') target = tabs[(idx - 1 + tabs.length) % tabs.length];
      else if(e.key === 'Home') target = tabs[0];
      else if(e.key === 'End') target = tabs[tabs.length - 1];
      if(target){
        e.preventDefault();
        target.focus();
        switchTab(target.dataset.tab);
      }
    });
  });
})();

// ── ORG ROLE / TABS ────────────────────────────────────────────
let currentOrgRole = 'teacher';

function decodeJWT(token){
  try{ return JSON.parse(atob(token.split('.')[1])); }catch(e){ return null; }
}

function applyOrgRole(org_role){
  currentOrgRole = org_role || 'teacher';
  // Tabs use inline `style.display = ''` / 'none' since they belong to a
  // flex row. Other role-gated elements (teacher-filter dropdowns,
  // analytics filter row) get the same treatment so admin-only UI
  // appears/disappears uniformly.
  document.querySelectorAll('[data-roles]').forEach(el => {
    const roles = (el.dataset.roles || '').split(' ');
    el.style.display = roles.includes(currentOrgRole) ? '' : 'none';
  });
  // Populate the teacher dropdowns the first time we discover admin role.
  if(currentOrgRole === 'admin' || currentOrgRole === 'superadmin'){
    loadOrgMembers().catch(() => {/* dropdown stays empty, filter still works */});
  }
  // Surface the active role in the topbar so admins know they're
  // looking at org-wide controls vs a teacher's day-to-day view.
  const badge = document.getElementById('topbar-role-badge');
  if(badge){
    const labels = {teacher:'Teacher', admin:'Admin', superadmin:'Super Admin'};
    badge.textContent = labels[currentOrgRole] || currentOrgRole;
    badge.dataset.role = currentOrgRole;
    badge.style.display = '';
  }
  // Default-tab routing — per role, in priority order. If the currently
  // active tab is hidden for this role (e.g. a super admin landing on
  // the old `live` default), pick the first preferred tab that's visible.
  const _defaults = {
    teacher:    ['live'],
    admin:      ['live', 'org'],
    superadmin: ['all-orgs', 'issues'],
  };
  const activeTab = document.querySelector('.tab.active');
  const activeVisible = activeTab && activeTab.style.display !== 'none';
  if(!activeVisible){
    const candidates = _defaults[currentOrgRole] || ['live'];
    for(const t of candidates){
      const btn = document.querySelector(`.tab[data-tab="${t}"]`);
      if(btn && btn.style.display !== 'none'){
        switchTab(t);
        break;
      }
    }
  }
}

function _onAuthDone(){
  const payload = decodeJWT(authToken);
  const role = (payload && payload.org_role) || (currentTeacherProfile && currentTeacherProfile.org_role);
  if(role) applyOrgRole(role);
}

// Patch into saveTokens
const _origSaveTokens = _saveTokens;
_saveTokens = function(access, refresh){
  _origSaveTokens(access, refresh);
  _onAuthDone();
};

async function loadOrgMembers(){
  if(!(currentOrgRole === 'admin' || currentOrgRole === 'superadmin')) return;
  const url = currentOrgRole === 'superadmin'
    ? `${BASE}/api/v1/admin/all-teachers`
    : `${BASE}/api/v1/org/members`;
  const r = await authFetch(url);
  if(!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  const rows = currentOrgRole === 'superadmin' ? (d.teachers || []) : (d.members || []);
  orgTeacherOptions = rows.map(t => ({
    id: String(t.id || ''),
    name: t.full_name || t.email || 'Teacher',
    email: t.email || '',
    org_id: t.org_id || '',
    org_name: t.org_name || '',
  })).filter(t => t.id);
  const opts = ['<option value="">All teachers</option>'].concat(orgTeacherOptions.map(t => {
    const suffix = currentOrgRole === 'superadmin' && t.org_name ? ` · ${t.org_name}` : '';
    const label = `${t.name}${suffix}`;
    return `<option value="${escAttr(t.id)}">${_escHtml(label)}</option>`;
  })).join('');
  document.querySelectorAll('.teacher-filter').forEach(sel => {
    sel.innerHTML = opts;
    sel.value = currentTeacherFilter;
  });
}

function applyTeacherFilter(source){
  const sel = document.getElementById(`${source}-teacher-filter`);
  currentTeacherFilter = sel ? sel.value : '';
  document.querySelectorAll('.teacher-filter').forEach(other => { other.value = currentTeacherFilter; });
  _analyticsCache = {};
  if(source === 'live') refreshLive();
  else if(source === 'results') refreshResults();
  else if(source === 'history') refreshStudentList();
  else if(source === 'analytics') loadAnalytics();
  else refreshAll();
}

// ── ORG API ────────────────────────────────────────────────────
async function loadOrgOverview(){
  try{
    const r = await authFetch(`${BASE}/api/v1/org`);
    if(!r.ok) return;
    const d = await r.json();
    document.getElementById('org-name').textContent = d.name || '--';
    document.getElementById('org-plan').textContent = d.plan || '--';
  }catch(_){}
  try{
    const r = await authFetch(`${BASE}/api/v1/org/billing`);
    if(!r.ok) return;
    const b = await r.json();
    document.getElementById('org-plan').textContent = b.plan || '--';
    document.getElementById('org-students').textContent = (b.student_count||0) + ' / ' + (b.max_students||30);
    const banner = document.getElementById('org-trial-banner');
    if(b.status === 'trialing' && b.trial_end){
      const remaining = Math.max(0, Math.ceil((new Date(b.trial_end) - new Date()) / 86400000));
      document.getElementById('org-trial-days').textContent = remaining;
      banner.style.display = '';
      // Urgency styling
      banner.style.background = remaining <= 1 ? 'var(--red-bg,rgba(239,68,68,0.1))' : remaining <= 3 ? 'var(--amber-bg,rgba(245,158,11,0.1))' : 'var(--accent-bg)';
      banner.style.borderColor = remaining <= 1 ? 'var(--red)' : remaining <= 3 ? 'var(--amber)' : 'var(--accent)';
      banner.style.color = remaining <= 1 ? 'var(--red)' : remaining <= 3 ? 'var(--amber)' : 'var(--accent-fg)';
      // Show upgrade button if <= 3 days
      if(remaining <= 3 && !document.getElementById('org-upgrade-btn')){
        const btn = document.createElement('a');
        btn.id = 'org-upgrade-btn';
        btn.href = '#';
        btn.onclick = (e) => { e.preventDefault(); showUpgradeModal('Your trial ends in ' + remaining + ' day' + (remaining === 1 ? '' : 's') + '. Choose a plan to continue.'); };
        btn.textContent = 'Upgrade now →';
        btn.style.cssText = 'margin-left:12px;font-weight:700;text-decoration:underline';
        banner.appendChild(btn);
      }
      // Update topbar trial badge
      const badge = document.getElementById('topbar-trial-badge');
      if(badge){
        badge.style.display = '';
        badge.textContent = '🔥 ' + remaining + ' day' + (remaining === 1 ? '' : 's') + ' left';
        badge.style.background = remaining <= 1 ? 'rgba(239,68,68,0.15)' : remaining <= 3 ? 'rgba(245,158,11,0.15)' : 'rgba(91,138,240,0.12)';
        badge.style.color = remaining <= 1 ? 'var(--red)' : remaining <= 3 ? 'var(--amber)' : 'var(--accent-light)';
      }
    } else {
      banner.style.display = 'none';
      const badge = document.getElementById('topbar-trial-badge');
      if(badge) badge.style.display = 'none';
    }
  }catch(_){}
  try{
    const r = await authFetch(`${BASE}/api/v1/org/members`);
    if(!r.ok) return;
    const m = await r.json();
    document.getElementById('org-teachers').textContent = (m.members||[]).length;
  }catch(_){}
}

async function loadOrgSettings(){
  try{
    const r = await authFetch(`${BASE}/api/v1/org`);
    if(!r.ok) return;
    const d = await r.json();
    const name = document.getElementById('settings-org-name');
    const result = document.getElementById('settings-result');
    if(name) name.value = d.name || '';
    if(result) result.textContent = '';
  }catch(_){}
}

async function loadMembers(){
  try{
    const r = await authFetch(`${BASE}/api/v1/org/members`);
    if(!r.ok) return;
    const d = await r.json();
    const tbody = document.getElementById('members-tbody');
    const members = d.members || [];
    const countEl = document.getElementById('members-count');
    if(countEl) countEl.textContent = String(members.length);
    tbody.innerHTML = members.map(m => `
      <tr>
        <td>${escHtml(m.full_name||'--')}</td>
        <td>${escHtml(m.email)}</td>
        <td>${escHtml(m.org_role)}</td>
        <td>${m.created_at||'--'}</td>
        <td>${m.org_role==='teacher' ? `<button class="btn btn-secondary btn-sm" style="color:var(--red);font-size:11px;padding:4px 8px" data-action="removeOrgMember" data-args='${_jsonArgsForAttr(m.id)}'>Remove</button>` : ''}</td>
      </tr>
    `).join('');
  }catch(_){}
}

// ── UPGRADE MODAL ────────────────────────────────────────────────
function showUpgradeModal(msg){
  const title = document.getElementById('upgrade-title');
  const desc = document.getElementById('upgrade-desc');
  document.getElementById('upgrade-modal-status').textContent = '';
  if(msg) desc.textContent = msg;
  document.getElementById('upgrade-modal').classList.remove('hidden');
}

function closeUpgradeModal(){
  document.getElementById('upgrade-modal').classList.add('hidden');
}

function trialBannerClick(){
  const badge = document.getElementById('topbar-trial-badge');
  const days = parseInt(badge.textContent.match(/\d+/)?.[0] || '0');
  showUpgradeModal('Your trial ends in ' + days + ' day' + (days === 1 ? '' : 's') + '. Upgrade to keep using Procta.');
}

// ── SECURITY (2FA + SESSIONS) ──────────────────────────────────
function loadSecurity(){
  load2FAStatus();
  loadSessions();
}

// 2FA UI — email-OTP (replaced TOTP/Google Authenticator 2026-05-23).
// No QR codes, no authenticator app, no backup codes. When 2FA is on,
// every login emails a 6-digit code via app/services/email_otp.py +
// app/emailer.py:send_2fa_otp_email — the login handler in
// app/routers/auth.py:teacher_login orchestrates the flow.
async function load2FAStatus(){
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/2fa/status`);
    if(!r.ok) return;
    const d = await r.json();
    const statusEl = document.getElementById('security-2fa-status');
    const enableBtn = document.getElementById('security-2fa-enable-btn');
    const disableBtn = document.getElementById('security-2fa-disable-btn');
    const unverifiedEl = document.getElementById('security-2fa-email-unverified');
    if(d.enabled){
      statusEl.innerHTML = '✅ Email-based two-factor authentication is <strong style="color:var(--emerald)">enabled</strong>. We\'ll email a 6-digit code on every sign-in.';
      enableBtn.style.display = 'none';
      if(disableBtn) disableBtn.style.display = '';
      if(unverifiedEl) unverifiedEl.style.display = 'none';
    } else if(!d.email_verified){
      statusEl.innerHTML = 'ℹ️ Two-factor authentication is <strong style="color:var(--amber)">not enabled</strong>.';
      enableBtn.style.display = 'none';
      if(disableBtn) disableBtn.style.display = 'none';
      if(unverifiedEl) unverifiedEl.style.display = '';
    } else {
      statusEl.innerHTML = 'ℹ️ Two-factor authentication is <strong style="color:var(--amber)">not enabled</strong>.';
      enableBtn.style.display = '';
      if(disableBtn) disableBtn.style.display = 'none';
      if(unverifiedEl) unverifiedEl.style.display = 'none';
    }
  }catch(_){}
}

// Re-auth helper shared by enable/disable. Prompts for the user's
// password and exchanges it for a 5-minute reauth_token. Centralised
// here so both flows use identical logic.
async function _get2FAReauthToken(action){
  const password = await appPrompt(`Enter your password to ${action}:`, '', {title:'Re-authentication required', okText:'Continue', inputType:'password'});
  if(!password) return null;
  const rr = await authFetch(`${BASE}/api/v1/auth/reauth`, {
    method:'POST',
    body: JSON.stringify({password})
  });
  if(!rr.ok){ const d=await rr.json().catch(()=>({})); throw new Error(d.detail||'Re-authentication failed'); }
  const rd = await rr.json();
  return rd.reauth_token;
}

async function enable2FA(){
  const resultEl = document.getElementById('security-2fa-result');
  resultEl.style.color = 'var(--text-muted)';
  resultEl.textContent = '';
  try{
    const reauth_token = await _get2FAReauthToken('enable');
    if(!reauth_token) return;
    resultEl.textContent = 'Enabling...';
    const r = await authFetch(`${BASE}/api/v1/auth/2fa/enable`, {
      method:'POST',
      body: JSON.stringify({reauth_token})
    });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail||'Failed to enable 2FA'); }
    resultEl.textContent = '✅ Enabled — next sign-in will require an email code.';
    resultEl.style.color = 'var(--emerald)';
    load2FAStatus();
  }catch(e){
    resultEl.textContent = e.message || 'Enable failed';
    resultEl.style.color = 'var(--red)';
  }
}

async function disable2FA(){
  const resultEl = document.getElementById('security-2fa-result');
  resultEl.style.color = 'var(--text-muted)';
  resultEl.textContent = '';
  try{
    const reauth_token = await _get2FAReauthToken('disable');
    if(!reauth_token) return;
    resultEl.textContent = 'Disabling...';
    const r = await authFetch(`${BASE}/api/v1/auth/2fa/disable`, {
      method:'POST',
      body: JSON.stringify({reauth_token})
    });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail||'Disable failed'); }
    resultEl.textContent = 'Two-factor authentication disabled.';
    resultEl.style.color = 'var(--emerald)';
    load2FAStatus();
  }catch(e){
    resultEl.textContent = e.message || 'Disable failed';
    resultEl.style.color = 'var(--red)';
  }
}

async function loadSessions(){
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/sessions`);
    if(!r.ok) return;
    const d = await r.json();
    const el = document.getElementById('security-sessions');
    if(!(d.sessions||[]).length){
      el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:12px">No active sessions</div>';
      return;
    }
    el.innerHTML = (d.sessions||[]).map(s => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-subtle)">
        <div style="font-size:12px;color:var(--text)">${escHtml(s.user_agent||'Unknown browser')}</div>
        <div style="font-size:11px;color:var(--muted);font-family:monospace">${s.ip||''}</div>
        <button class="btn btn-ghost btn-sm" data-action="revokeSession" data-args='${_jsonArgsForAttr(s.jti)}' style="font-size:10px;color:var(--red);padding:2px 6px">Revoke</button>
      </div>
    `).join('');
  }catch(_){}
}

async function revokeSession(jti){
  if(!(await appConfirm('Revoke this session? The device will be signed out immediately.', 'Revoke session', {okText:'Revoke'}))) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/sessions/${encodeURIComponent(jti)}/revoke`, {method:'POST'});
    if(!r.ok) throw new Error();
    loadSessions();
  }catch(_){}
}

async function revokeOtherSessions(){
  if(!(await appConfirm('Sign out all other devices? You will stay signed in on this device.', 'Sign out other devices', {okText:'Sign out'}))) return;
  const resultEl = document.getElementById('security-sessions-result');
  resultEl.textContent = 'Revoking...';
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/sessions/revoke-others`, {method:'POST'});
    if(!r.ok) throw new Error();
    resultEl.textContent = '✅ Other sessions revoked.';
    loadSessions();
  }catch(e){ resultEl.textContent = 'Failed: '+e.message; }
}



async function loadPendingGrades(){
  const eid = currentExamId;
  const list = document.getElementById('grade-list');
  list.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted)"><span class="spinner"></span> Loading…</div>';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/pending-grades?exam_id=${encodeURIComponent(eid)}`);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d = await r.json();
    _pendingGrades = d.answers || [];
    const meta = document.getElementById('grade-meta');
    const summary = document.getElementById('grade-summary');
    const total = _pendingGrades.length;
    const confirmed = _pendingGrades.filter(a => a.teacher_score != null).length;
    const high = _pendingGrades.filter(a => a.ai_confidence === 'high' && a.teacher_score == null).length;
    const medium = _pendingGrades.filter(a => a.ai_confidence === 'medium' && a.teacher_score == null).length;
    const low = _pendingGrades.filter(a => a.ai_confidence === 'low' && a.teacher_score == null).length;
    if(meta) meta.textContent = `${total} total · ${confirmed} confirmed`;
    if(summary) summary.innerHTML = `${total - confirmed} pending · <span style="color:var(--emerald)">${high} high</span> · <span style="color:var(--amber)">${medium} med</span> · <span style="color:var(--red)">${low} low</span>`;
    renderPendingGrades();
  }catch(e){
    list.innerHTML = `<div style="color:var(--red);padding:20px">Failed to load: ${escAttr(String(e))}</div>`;
  }
}

function renderPendingGrades(){
  const list = document.getElementById('grade-list');
  if(!_pendingGrades.length){
    list.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted)">No short-answer responses to review for this exam.</div>';
    return;
  }
  list.innerHTML = _pendingGrades.map((a,i)=>{
    const ai = a.ai_score!=null ? `${a.ai_score}/${a.max_score}` : '—';
    const conf = a.ai_confidence || '';
    const confColor = conf==='high' ? 'var(--emerald)' : conf==='medium' ? 'var(--amber)' : 'var(--muted)';
    const isGraded = a.teacher_score!=null;
    return `<div class="grade-row" data-aid="${escAttr(a.id)}" style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;background:var(--card,#161a22);${isGraded?'opacity:0.55':''}">
      <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:6px;font-size:11px;color:var(--muted);font-family:var(--font-mono)">
        <span>${escAttr(a.roll_number||'?')} · ${escAttr(a.full_name||'')}</span>
        <span>${isGraded?'✓ confirmed':'pending'}</span>
      </div>
      <div style="font-size:13px;font-weight:600;margin-bottom:4px">${escAttr(a.question||'')}</div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px"><strong>Reference:</strong> ${escAttr(a.reference||'')}${a.rubric?`<br><strong>Rubric:</strong> ${escAttr(a.rubric)}`:''}</div>
      <div style="background:var(--surface-1,#0d1117);border-left:3px solid var(--accent);padding:8px 10px;border-radius:0 6px 6px 0;font-size:13px;line-height:1.5;white-space:pre-wrap;margin-bottom:8px">${escAttr(a.student_answer||'(blank)')}</div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px">
        <span style="color:var(--muted)">AI suggests:</span>
        <strong>${ai}</strong>
        ${conf?`<span style="color:${confColor};font-size:10px;text-transform:uppercase;font-weight:600">${escAttr(conf)} conf</span>`:''}
        ${a.ai_feedback?`<span style="color:var(--muted);font-style:italic;flex:1;min-width:200px">"${escAttr(a.ai_feedback)}"</span>`:''}
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
        <label style="font-size:11px;color:var(--muted)">Final score</label>
        <input type="number" id="grade-input-${i}" min="0" max="${escAttr(String(a.max_score||1))}" step="0.5"
               value="${escAttr(String(a.teacher_score!=null?a.teacher_score:(a.ai_score!=null?a.ai_score:'')))}"
               style="width:80px;padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--surface-1);color:inherit">
        <span style="color:var(--muted);font-size:11px">/ ${escAttr(String(a.max_score||1))}</span>
        <button class="btn btn-primary btn-sm" data-action="confirmGrade" data-args='${_jsonArgsForAttr(i)}' ${isGraded?'disabled':''} style="margin-left:auto">${isGraded?'Confirmed':'Confirm'}</button>
      </div>
    </div>`;
  }).join('');
}

async function runGradeSuggest(){
  const btn = document.getElementById('grade-suggest-btn');
  const status = document.getElementById('grade-summary');
  // Only ask the AI for answers that don't already have a suggestion —
  // saves Groq calls and keeps the rate-limit headroom for retries.
  const ids = _pendingGrades.filter(a => a.ai_score==null && a.teacher_score==null).map(a => a.id);
  if(!ids.length){ status.textContent = 'All pending answers already have AI suggestions.'; return; }
  btn.disabled = true;
  status.textContent = `Asking AI to grade ${ids.length}…`;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/grade-suggest`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({answer_ids: ids})
    });
    if(!r.ok){
      const txt = await r.text();
      throw new Error(`HTTP ${r.status}: ${txt}`);
    }
    const d = await r.json();
    status.textContent = `AI graded ${d.graded||0} answer(s).`;
    await loadPendingGrades();
  }catch(e){
    status.textContent = 'Suggest failed: '+String(e);
  }finally{
    btn.disabled = false;
  }
}

async function confirmGrade(i){
  const a = _pendingGrades[i];
  if(!a) return;
  const inp = document.getElementById(`grade-input-${i}`);
  const score = parseFloat(inp.value);
  if(isNaN(score) || score<0 || score>parseFloat(a.max_score||1)){
    showModal(`Score must be between 0 and ${a.max_score||1}.`);
    return;
  }
  const status = document.getElementById('grade-summary');
  status.textContent = 'Confirming…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/grade-confirm`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({answer_id: a.id, score})
    });
    if(!r.ok){
      const txt = await r.text();
      throw new Error(`HTTP ${r.status}: ${txt}`);
    }
    a.teacher_score = score;
    renderPendingGrades();
    refreshPendingGradeBadge();
    status.textContent = `Saved ${score}/${a.max_score} for ${a.roll_number}.`;
  }catch(e){
    status.textContent = 'Confirm failed: '+String(e);
  }
}

async function gradeBulkAccept(confidence){
  const status = document.getElementById('grade-summary');
  const eid = currentExamId;
  if(!eid){ status.textContent = 'Select an exam first.'; return; }
  const body = {exam_id: eid, action: 'accept'};
  let label = 'all';
  if(confidence === 'high'){
    body.confidence_filter = 'high';
    label = 'high-confidence';
  }
  status.textContent = `Accepting ${label} suggestions...`;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/grade-confirm-bulk`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    if(!r.ok){ const t=await r.text(); throw new Error(`HTTP ${r.status}: ${t}`); }
    const d = await r.json();
    status.textContent = `✅ Accepted ${d.confirmed} (${d.skipped} skipped, ${d.sessions_recompiled} sessions recompiled).`;
    await loadPendingGrades();
  }catch(e){ status.textContent = 'Failed: '+String(e); }
}

async function gradeBulkReject(){
  const status = document.getElementById('grade-summary');
  const eid = currentExamId;
  if(!eid){ status.textContent = 'Select an exam first.'; return; }
  if(!(await appConfirm('Set ALL unconfirmed short-answer scores to 0? This can be reverted by re-grading.', 'Reject pending scores', {okText:'Set to 0'}))) return;
  status.textContent = 'Rejecting all...';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/grade-confirm-bulk`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({exam_id: eid, action: 'reject'})
    });
    if(!r.ok){ const t=await r.text(); throw new Error(`HTTP ${r.status}: ${t}`); }
    const d = await r.json();
    status.textContent = `❌ Rejected ${d.confirmed} (${d.skipped} skipped).`;
    await loadPendingGrades();
  }catch(e){ status.textContent = 'Failed: '+String(e); }
}

let _pendingGrades = [];
async function refreshPendingGradeBadge(){
  // Fetch the count once on tab switch + post-confirm so the toolbar
  // chip nudges the teacher when there's review work waiting.
  const eid = currentExamId;
  if(!eid){ return; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/pending-grades?exam_id=${encodeURIComponent(eid)}`);
    if(!r.ok) return;
    const d = await r.json();
    const n = (d.answers||[]).filter(a => a.teacher_score==null).length;
    const chip = document.getElementById('pending-grade-count');
    if(!chip) return;
    if(n>0){ chip.textContent = String(n); chip.style.display='inline-block'; }
    else { chip.style.display='none'; }
  }catch(e){ /* silent — modal still works on demand */ }
}


function openGradeReview(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  document.getElementById('grade-modal').classList.remove('hidden');
  loadPendingGrades();
}


function closeGradeReview(){
  document.getElementById('grade-modal').classList.add('hidden');
}

async function openRoomCamView(sid){
  _roomCamSid = sid;
  _roomCamOpened = true;
  const img = document.getElementById('roomcam-img');
  const ph = document.getElementById('roomcam-placeholder');
  const meta = document.getElementById('roomcam-meta');
  const statusEl = document.getElementById('roomcam-status');
  document.getElementById('roomcam-approve-btn').style.display = '';
  img.style.display = 'none'; ph.style.display = '';
  meta.textContent = sid;
  statusEl.innerHTML = '● Connecting';
  document.getElementById('roomcam-modal').classList.remove('hidden');

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/start`, {method:'POST'});
    if(!r.ok) throw new Error();
  }catch(e){ statusEl.innerHTML = '● Failed'; return; }

  _roomCamFrameTimer = setInterval(_pollRoomCamFrame, 1500);
  _roomCamKeepaliveTimer = setInterval(_roomCamKeepalive, 30000);
}

function _pollRoomCamFrame(){
  if(!_roomCamSid) return;
  const img = document.getElementById('roomcam-img');
  const ph = document.getElementById('roomcam-placeholder');
  const statusEl = document.getElementById('roomcam-status');
  const tsEl = document.getElementById('roomcam-ts');
  const t = Date.now();
  const headers = {};
  if(authToken) headers.Authorization = `Bearer ${authToken}`;
  fetchWithTimeout(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_roomCamSid)}/room-cam/frame?t=${t}`, {
    credentials: 'include',
    headers,
  }).then(r => {
    if(!r.ok) throw new Error();
    return r.blob();
  }).then(blob => {
    if(_roomCamOpened){
      img.src = URL.createObjectURL(blob); img.style.display = ''; ph.style.display = 'none';
      tsEl.textContent = new Date().toLocaleTimeString();
      statusEl.innerHTML = '● Live';
    }
  }).catch(() => {
    statusEl.innerHTML = '●&#160;Offline';
  });
}

async function _roomCamKeepalive(){
  if(_roomCamSid){
    try{ await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_roomCamSid)}/room-cam/keepalive`, {method:'POST'}); }catch(_){}
  }
}

async function closeRoomCamView(){
  _roomCamOpened = false;
  if(_roomCamFrameTimer){ clearInterval(_roomCamFrameTimer); _roomCamFrameTimer = null; }
  if(_roomCamKeepaliveTimer){ clearInterval(_roomCamKeepaliveTimer); _roomCamKeepaliveTimer = null; }
  const img = document.getElementById('roomcam-img');
  if(img && img.src && img.src.startsWith('blob:')){ URL.revokeObjectURL(img.src); img.removeAttribute('src'); }
  document.getElementById('roomcam-modal').classList.add('hidden');
  if(_roomCamSid){
    try{ await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_roomCamSid)}/room-cam/stop`, {method:'POST'}); }catch(_){}
    _roomCamSid = null;
  }
}

async function roomCamApprove(){
  if(!_roomCamSid) return;
  const resultEl = document.getElementById('roomcam-result');
  resultEl.textContent = 'Approving...'; resultEl.style.color = 'var(--muted)';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_roomCamSid)}/room-cam/approve`, {method:'POST'});
    if(!r.ok) throw new Error();
    resultEl.textContent = '✅ Approved'; resultEl.style.color = 'var(--emerald)';
    document.getElementById('roomcam-approve-btn').style.display = 'none';
  }catch(e){ resultEl.textContent = 'Failed'; resultEl.style.color = 'var(--red)'; }
}

async function roomCamReject(){
  if(!_roomCamSid) return;
  const resultEl = document.getElementById('roomcam-result');
  resultEl.textContent = 'Rejecting...'; resultEl.style.color = 'var(--muted)';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_roomCamSid)}/room-cam/reject`, {method:'POST'});
    if(!r.ok) throw new Error();
    resultEl.textContent = '❌ Rejected — student must reposition phone'; resultEl.style.color = 'var(--amber)';
  }catch(e){ resultEl.textContent = 'Failed'; resultEl.style.color = 'var(--red)'; }
}




let _showingAudit = false;

function toggleGradeAudit(){
  _showingAudit = !_showingAudit;
  document.getElementById('grade-list').style.display = _showingAudit ? 'none' : '';
  document.getElementById('grade-audit').style.display = _showingAudit ? '' : 'none';
  document.getElementById('grade-audit-btn').textContent = _showingAudit ? '← Back to pending' : 'Audit trail';
  if(_showingAudit) loadGradeAudit();
}

async function loadGradeAudit(){
  const eid = currentExamId;
  if(!eid) return;
  const eventsEl = document.getElementById('grade-audit-events');
  const statsEl = document.getElementById('grade-audit-stats');
  eventsEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">Loading…</div>';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/grading-audit?exam_id=${encodeURIComponent(eid)}&limit=200`);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d = await r.json();
    const s = d.stats || {};
    statsEl.innerHTML = `
      <div class="stat-tile" style="padding:0"><div class="stat-tile-label">Total graded</div><div class="stat-tile-value accent">${s.total||0}</div></div>
      <div class="stat-tile" style="padding:0"><div class="stat-tile-label">AI accept rate</div><div class="stat-tile-value" style="color:var(--emerald)">${s.ai_accept_rate||0}%</div></div>
      <div class="stat-tile" style="padding:0"><div class="stat-tile-label">Accepted</div><div class="stat-tile-value" style="color:var(--emerald)">${s.accepted||0}</div></div>
      <div class="stat-tile" style="padding:0"><div class="stat-tile-label">Overridden</div><div class="stat-tile-value" style="color:var(--amber)">${s.overridden||0}</div></div>
      <div class="stat-tile" style="padding:0"><div class="stat-tile-label">Rejected</div><div class="stat-tile-value" style="color:var(--red)">${s.rejected||0}</div></div>
    `;
    const events = d.events || [];
    if(!events.length){
      eventsEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted)">No grading history yet.</div>';
      return;
    }
    eventsEl.innerHTML = events.map(e => {
      const actionLabel = {'confirmed':'Confirmed','bulk_accept':'Bulk accepted','bulk_reject':'Bulk rejected','overridden':'Overridden'}[e.action]||e.action;
      const actionColor = e.action==='overridden'?'var(--amber)':e.action==='bulk_reject'?'var(--red)':'var(--emerald)';
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-subtle)">
        <div><span style="color:${actionColor};font-weight:600;font-size:11px;text-transform:uppercase">${_escHtml(actionLabel)}</span>
        <span style="color:var(--text);font-size:12px;margin-left:8px">AI: ${e.ai_score!=null?e.ai_score+'/':'—/'}${e.max_score} → ${e.teacher_score}</span></div>
        <div style="font-size:10px;color:var(--muted);font-family:monospace">${e.created_at?(new Date(e.created_at).toLocaleDateString()):''}</div>
      </div>`;
    }).join('');
  }catch(e){
    eventsEl.innerHTML = `<div style="color:var(--red);padding:20px">Failed to load: ${escAttr(String(e))}</div>`;
  }
}
async function saveOrgName(){
  const name = document.getElementById('settings-org-name').value.trim();
  const resultEl = document.getElementById('settings-result');
  if(!name){ resultEl.textContent = 'Name is required'; resultEl.style.color = 'var(--red)'; return; }
  resultEl.textContent = 'Saving...'; resultEl.style.color = 'var(--text-secondary)';
  try{
    const r = await authFetch(`${BASE}/api/v1/org`, {
      method:'PATCH',
      body: JSON.stringify({ name }),
    });
    if(!r.ok){
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `Save failed (${r.status})`);
    }
    const d = await r.json();
    resultEl.textContent = 'Saved';
    resultEl.style.color = 'var(--green, #3dd9a8)';
    // Reflect the canonical name returned by the server (in case it
    // normalised whitespace etc).
    if(d && d.name){
      const el = document.getElementById('settings-org-name');
      if(el) el.value = d.name;
    }
  }catch(e){
    resultEl.textContent = e.message || 'Save failed';
    resultEl.style.color = 'var(--red)';
  }
}

function sortLive(key){
  if(liveSortKey===key) liveSortAsc=!liveSortAsc;
  else{liveSortKey=key;liveSortAsc=true;}
  renderLive();
}
function filterLive(){renderLive();}

function _riskClass(score){
  const n = Number(score || 0);
  if(n > 70) return 'critical';
  if(n > 40) return 'high';
  if(n > 15) return 'moderate';
  return 'low';
}

function _severityRank(sev){
  return {critical:4, high:3, medium:2, low:1, info:0}[String(sev || '').toLowerCase()] ?? 0;
}

function _calBadge(cal){
  const tier = (cal && cal.tier) || 'missing';
  const label = {stable:'Stable', normal:'Normal', loose:'Loose', tight:'Tight', missing:'--'}[tier] || tier;
  return `<span class="badge" title="${escAttr((cal && cal.reason) || '')}">${_escHtml(label)}</span>`;
}

function renderLiveStats(activeRows=[], allRows=[]){
  const el = document.getElementById('live-stats');
  if(!el) return;
  const all = Array.isArray(allRows) ? allRows : [];
  const active = Array.isArray(activeRows) ? activeRows : [];
  const submitted = all.filter(s => s.submitted || s.live_state === 'submitted').length;
  const stale = all.filter(s => s.live_state === 'stale').length;
  const highRisk = all.filter(s => Number(s.risk_score || 0) > 40).length;
  el.innerHTML = `
    <div class="stat-tile"><div class="stat-tile-label">Live Now</div><div class="stat-tile-value accent">${active.length}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">All Sessions</div><div class="stat-tile-value">${all.length}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">High Risk</div><div class="stat-tile-value" style="color:var(--red)">${highRisk}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Submitted</div><div class="stat-tile-value success">${submitted}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Stale</div><div class="stat-tile-value">${stale}</div></div>
  `;
}

function renderLive(){
  const body = document.getElementById('live-body');
  if(!body) return;
  const q = (document.getElementById('live-search')?.value || '').toLowerCase().trim();
  const sevFilter = document.getElementById('live-sev-filter')?.value || 'all';
  let rows = [...(liveData || [])];
  if(q){
    rows = rows.filter(s => [s.session_id, s.last_event, s.last_severity, s.live_state]
      .some(v => String(v || '').toLowerCase().includes(q)));
  }
  if(sevFilter !== 'all'){
    rows = rows.filter(s => String(s.last_severity || '').toLowerCase() === sevFilter);
  }
  rows.sort((a,b)=>{
    let va = a[liveSortKey], vb = b[liveSortKey];
    if(liveSortKey === 'last_severity'){ va = _severityRank(va); vb = _severityRank(vb); }
    else if(liveSortKey === 'risk_score' || liveSortKey === 'heartbeat_age_sec'){ va = Number(va || 0); vb = Number(vb || 0); }
    else { va = String(va || ''); vb = String(vb || ''); }
    const cmp = (typeof va === 'number' && typeof vb === 'number') ? va - vb : va.localeCompare(vb);
    return liveSortAsc ? cmp : -cmp;
  });
  if(!rows.length){
    body.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:18px">No live sessions found.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(s => {
    const sid = s.session_id || s.session_key || '';
    const risk = s.risk_score == null ? '--' : String(s.risk_score);
    const state = s.submitted ? 'Submitted' : (s.live_state || 'Active');
    return `<tr data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}' style="cursor:pointer">
      <td><span style="font-family:var(--font-mono);font-size:11px">${_escHtml(sid)}</span></td>
      <td>${_escHtml((s.last_event || '--').replace(/_/g,' '))}</td>
      <td><span class="sev ${escAttr(String(s.last_severity || 'low').toLowerCase())}">${_escHtml(s.last_severity || '--')}</span></td>
      <td><span class="badge">${_escHtml(risk)}</span></td>
      <td>${_calBadge(s.calibration)}</td>
      <td>${_escHtml(s.last_seen || (s.heartbeat_age_sec != null ? `${s.heartbeat_age_sec}s ago` : '--'))}</td>
      <td>${_escHtml(state)}</td>
      <td>
        <button class="btn btn-secondary btn-sm" data-action="openTriage" data-args='${_jsonArgsForAttr(sid)}'>Insight</button>
        <button class="btn btn-secondary btn-sm" data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}'>Timeline</button>
      </td>
    </tr>`;
  }).join('');
}

function renderResultsStats(){
  const el = document.getElementById('results-stats');
  if(!el) return;
  const rows = resultsData || [];
  const avg = rows.length ? Math.round(rows.reduce((s,r)=>s + Number(r.percentage || 0), 0) / rows.length) : 0;
  const highRisk = rows.filter(r => Number(r.risk_score || 0) > 40).length;
  const violations = rows.reduce((s,r)=>s + Number(r.violation_count || 0), 0);
  el.innerHTML = `
    <div class="stat-tile"><div class="stat-tile-label">Submissions</div><div class="stat-tile-value accent">${rows.length}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Avg Score</div><div class="stat-tile-value">${avg}%</div></div>
    <div class="stat-tile"><div class="stat-tile-label">High Risk</div><div class="stat-tile-value" style="color:var(--red)">${highRisk}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Violations</div><div class="stat-tile-value">${violations}</div></div>
  `;
}

function renderResults(){
  const body = document.getElementById('results-body');
  if(!body) return;
  const q = (document.getElementById('results-search')?.value || '').toLowerCase().trim();
  const riskFilter = document.getElementById('results-risk-filter')?.value || 'all';
  let rows = [...(resultsData || [])];
  if(q){
    rows = rows.filter(r => [r.roll_number, r.full_name, r.email, r.session_id]
      .some(v => String(v || '').toLowerCase().includes(q)));
  }
  if(riskFilter !== 'all'){
    rows = rows.filter(r => _riskClass(r.risk_score) === riskFilter);
  }
  rows.sort((a,b)=>{
    let va = a[resSortKey], vb = b[resSortKey];
    if(['score','total','percentage','violation_count','risk_score','time_taken_secs'].includes(resSortKey)){
      va = Number(va || 0); vb = Number(vb || 0);
    } else {
      va = String(va || ''); vb = String(vb || '');
    }
    const cmp = (typeof va === 'number' && typeof vb === 'number') ? va - vb : va.localeCompare(vb);
    return resSortAsc ? cmp : -cmp;
  });
  if(!rows.length){
    body.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:18px">No results found.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => {
    const sid = r.session_id || r.session_key || '';
    return `<tr data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}' style="cursor:pointer">
      <td>${_escHtml(r.roll_number || '--')}</td>
      <td>${_escHtml(r.full_name || '--')}</td>
      <td>${_escHtml(r.score ?? '--')} / ${_escHtml(r.total ?? '--')}</td>
      <td>${_escHtml(r.percentage ?? '--')}%</td>
      <td>${_escHtml(r.violation_count ?? 0)}</td>
      <td>${_riskBadge(r.risk_score)}</td>
      <td>${_calBadge(r.calibration)}</td>
      <td>${_fmtDuration(r.time_taken_secs || 0)}</td>
      <td>${_escHtml(r.submitted_at || '--')}</td>
      <td>
        <button class="btn btn-secondary btn-sm" data-action="dlScorecard" data-args='${_jsonArgsForAttr(sid)}'>Scorecard</button>
        <button class="btn btn-secondary btn-sm" data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}'>Timeline</button>
      </td>
    </tr>`;
  }).join('');
}

function filterResults(){renderResults();}

async function refreshIdReviews(){
  const section = document.getElementById('id-reviews-section');
  const list = document.getElementById('id-reviews-list');
  const count = document.getElementById('id-reviews-count');
  if(!section || !list || !count) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/pending-verifications${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const rows = d.pending || [];
    count.textContent = rows.length;
    section.style.display = rows.length ? '' : 'none';
    list.innerHTML = rows.map(v => `
      <div class="id-review-card">
        <div style="display:flex;align-items:center;gap:10px;justify-content:space-between">
          <div>
            <div style="font-weight:600;color:var(--text-high)">${_escHtml(v.full_name || v.roll_number || 'Student')}</div>
            <div style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono)">${_escHtml(v.session_key || '')}</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-secondary btn-sm" data-action="decideIdReview" data-args='${_jsonArgsForAttr(v.id,v.session_key,'approved')}'>Approve</button>
            <button class="btn btn-secondary btn-sm" data-action="decideIdReview" data-args='${_jsonArgsForAttr(v.id,v.session_key,'retake')}'>Retake</button>
            <button class="btn btn-secondary btn-sm" data-action="decideIdReview" data-args='${_jsonArgsForAttr(v.id,v.session_key,'rejected')}' style="color:var(--red)">Reject</button>
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
          ${v.selfie_url ? `<img src="${escAttr(v.selfie_url)}" style="width:96px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--border-subtle)">` : ''}
          ${v.id_url ? `<img src="${escAttr(v.id_url)}" style="width:96px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--border-subtle)">` : ''}
          <span style="font-size:11px;color:var(--text-muted);align-self:center">${_escHtml(v.created_at || '')}</span>
        </div>
      </div>
    `).join('');
  }catch(e){
    console.warn('refreshIdReviews', e);
    section.style.display = 'none';
  }
}

async function decideIdReview(violationId, sessionKey, decision){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/id-decision`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({violation_id: violationId, session_key: sessionKey, decision})
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    await refreshIdReviews();
    await refreshLive();
  }catch(e){
    showModal('ID review failed', e.message || 'Could not save review decision.');
  }
}

function openTimelineForSession(sid){
  if(!sid) return;
  currentSessionId = sid;
  openTimeline();
}


function _setExporting(btnId, loading){
  const btn = document.getElementById(btnId);
  if(!btn) return;
  if(loading){
    btn._exportLabel = btn._exportLabel || btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Downloading…';
  } else {
    btn.disabled = false;
    btn.textContent = btn._exportLabel || btn.textContent;
    delete btn._exportLabel;
  }
}

function exportExcel(){
  const eid = currentExamId || 'all';
  fetchBlob(`${BASE}/api/v1/export-excel${_examQuery('?')}`, `results_${eid}.xlsx`, 'btn-export-excel');
}
function exportCSV(){
  fetchBlob(`${BASE}/api/v1/export-csv${_examQuery('?')}`, 'results.csv', 'btn-export-csv');
}
async function fetchBlob(url, filename, btnId){
  if(btnId) _setExporting(btnId, true);
  try {
    const r = await authFetch(url);
    if(!r.ok) throw new Error(`Download failed: HTTP ${r.status}`);
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  } finally {
    if(btnId) _setExporting(btnId, false);
  }
}

function dlPDF(sid){
  fetchBlob(`${BASE}/api/v1/export-pdf/${encodeURIComponent(sid)}`, `report_${sid.split('_')[0]}.pdf`);
}
function downloadPDF(){
  if(currentSessionId) dlPDF(currentSessionId);
}
function dlScorecard(sid){
  fetchBlob(`${BASE}/api/v1/admin/scorecard-pdf/${encodeURIComponent(sid)}`, `scorecard_${sid.split('_')[0]}.pdf`);
}

function dlAllScorecards(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  fetchBlob(`${BASE}/api/v1/admin/scorecard-zip?exam_id=${encodeURIComponent(eid)}`, `scorecards_${eid}.zip`, 'btn-scorecard-zip');
}
// ── GRADE REVIEW (short-answer AI grading) ───────────────────────
// Backed by /api/admin/pending-grades, /api/admin/grade-suggest,
// /api/admin/grade-confirm. The AI provides a score + rationale; the
// teacher confirms or overrides before it counts. We never write
// teacher_score automatically — that's the trust boundary.



async function closeLiveView(){
  const sid = _liveViewSid;
  _liveViewSid = null;
  _liveViewLastFrameAt = 0;
  if(_liveViewFrameTimer){ clearInterval(_liveViewFrameTimer); _liveViewFrameTimer = null; }
  if(_liveViewKeepaliveTimer){ clearInterval(_liveViewKeepaliveTimer); _liveViewKeepaliveTimer = null; }
  if(_liveViewStaleTimer){ clearInterval(_liveViewStaleTimer); _liveViewStaleTimer = null; }
  // Release the current blob URL so the browser doesn't hold the
  // last-frame bytes in memory after the panel closes.
  const img = document.getElementById('liveview-img');
  if(img && img.src && img.src.startsWith('blob:')){
    URL.revokeObjectURL(img.src);
    img.removeAttribute('src');
  }
  document.getElementById('liveview-modal').classList.add('hidden');
  if(sid){
    try{
      await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/live-view/stop`, {method:'POST'});
    }catch(e){/* server-side TTL will clean up within 60s anyway */}
  }
}

// Tear down cleanly if the teacher closes the browser tab — without
// this, the server-side flag would linger for up to 60s.
window.addEventListener('beforeunload', () => {
  if(_liveViewSid){
    // sendBeacon is fire-and-forget but reliable on tab close.
    try{
      const url = `${BASE}/api/v1/admin/sessions/${encodeURIComponent(_liveViewSid)}/live-view/stop`;
      navigator.sendBeacon && navigator.sendBeacon(url);
    }catch(_){}
  }
});

// ── ROOM CAMERA VIEW ─────────────────────────────────────────────
let _roomCamSid = null;
let _roomCamFrameTimer = null;
let _roomCamKeepaliveTimer = null;
let _roomCamOpened = false;



function closeModal(){
  document.getElementById('detail-modal').classList.remove('open');
  currentSessionId=null;
}



function closeTriage(){
  document.getElementById('triage-modal').classList.add('hidden');
  _triageSid = null;
}

async function openTriage(sid){
  if(!sid) return;
  _triageSid = sid;
  const modal = document.getElementById('triage-modal');
  const body = document.getElementById('triage-body');
  const meta = document.getElementById('triage-meta');
  const stats = document.getElementById('triage-stats');
  const timelineBtn = document.getElementById('triage-open-timeline');
  modal.classList.remove('hidden');
  meta.textContent = sid;
  stats.textContent = '';
  body.style.color = 'var(--text)';
  if(timelineBtn) timelineBtn.disabled = false;
  body.innerHTML = '<div class="spinner" style="margin-right:10px"></div><span style="color:var(--muted)">Analysing recent activity…</span>';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/triage`);
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail || `Failed to load insight (${r.status})`);
    const summary = d.summary || d.insight || d;
    const text = summary.summary || summary.text || summary.verdict || 'No concerning pattern detected in the recent events.';
    body.textContent = text;
    const pieces = [];
    if(summary.risk_level) pieces.push('Risk: ' + summary.risk_level);
    if(summary.event_count != null) pieces.push('Events: ' + summary.event_count);
    if(summary.cached) pieces.push('Cached');
    stats.textContent = pieces.join(' · ');
  }catch(e){
    body.textContent = e.message || 'Could not load AI insight.';
    body.style.color = 'var(--red)';
    if(timelineBtn) timelineBtn.disabled = false;
  }
}



async function doInviteTeacher(){
  const email = document.getElementById('invite-email').value.trim();
  const name = document.getElementById('invite-name').value.trim();
  const resultEl = document.getElementById('teacher-invite-result');
  if(!email){ resultEl.textContent = 'Email is required'; resultEl.style.color = 'var(--red)'; return; }
  resultEl.textContent = 'Sending...'; resultEl.style.color = 'var(--text-secondary)';
  try{
    const r = await authFetch(`${BASE}/api/v1/org/invite`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email, full_name:name})
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail||'Failed to send invite');
    }
    resultEl.textContent = 'Invitation sent!'; resultEl.style.color = 'var(--emerald)';
    setTimeout(hideInviteTeacherModal, 1500);
  }catch(e){
    resultEl.textContent = e.message; resultEl.style.color = 'var(--red)';
  }
}



async function emailAllScorecards(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const msg = await appPrompt(
    "Email scorecards to all completed students?\n\n" +
    "Students who have already been emailed will be skipped.\n" +
    "Optionally add a short note (shown in every email), or leave blank:",
    "",
    {title:'Email scorecards', okText:'Send emails', multiline:true}
  );
  if (msg === null) return;  // user cancelled
  const body = { custom_message: (msg || "").trim() };
  try {
    const r = await authFetch(
      `${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/email-scorecards`,
      { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }
    );
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    const parts = [
      `Sent: ${d.sent || 0}`,
      `Already emailed: ${d.already_sent || 0}`,
      `No email on file: ${d.skipped_no_email || 0}`,
      `Failed: ${d.failed || 0}`,
      `Total sessions: ${d.total || 0}`,
    ];
    showModal("Scorecard email batch complete.\n\n" + parts.join("\n"));
  } catch (e) {
    showModal("Failed to email scorecards: " + e.message);
  }
}

// ── TOOLS ───────────────────────────────────────────────────────


function hideInviteTeacherModal(){
  document.getElementById('invite-modal').style.display = 'none';
}



async function refreshAll(){
  const gen = ++_refreshGen;
  document.getElementById('refresh-spin').style.display='inline-block';
  await Promise.all([refreshLive(), refreshIdReviews(), refreshResults(), loadFailedCount(), loadAccessCode(), loadRegisteredCount(), loadSchedule(), loadShuffleConfig(), loadInvites()]);
  if(gen !== _refreshGen) return; // stale — user switched exam during load
  document.getElementById('refresh-spin').style.display='none';
  document.getElementById('last-refresh').textContent='Updated '+new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:true});
}

// ── PENDING ID REVIEWS ──────────────────────────────────────────
let _prevIdReviewCount = 0;


async function refreshLive(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    liveData = d.all_sessions || d.sessions || [];
    renderLiveStats(d.sessions||[], liveData);
    renderLive();
    decorateSessionFlagButtons('live');
  }catch(e){ console.error('refreshLive',e); }
}



async function refreshResults(){
  try{
    const r = await authFetch(`${BASE}/api/v1/results${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    resultsData = d.results || [];
    renderResultsStats();
    renderResults();
    decorateSessionFlagButtons('results');
  }catch(e){ console.error('refreshResults',e); }
}

function _issueLabel(value){
  return {
    'bug':'Bug',
    'question':'Question',
    'feature':'Feature request',
    'session-issue':'Session issue',
    'other':'Other',
    'open':'Open',
    'triaged':'Triaged',
    'resolved':'Resolved',
    'low':'Low',
    'normal':'Normal',
    'high':'High',
  }[value] || value || '—';
}

function _sessionIdFromRow(row){
  return row && (row.session_id || row.session_key || row.id || '');
}

function decorateSessionFlagButtons(kind){
  if(currentOrgRole !== 'teacher') return;
  const body = document.getElementById(kind === 'results' ? 'results-body' : 'live-body');
  const rows = kind === 'results' ? resultsData : liveData;
  if(!body || !Array.isArray(rows)) return;
  body.querySelectorAll('tr').forEach((tr, idx) => {
    if(tr.querySelector('.session-flag-btn')) return;
    const row = rows[idx] || {};
    const sid = _sessionIdFromRow(row);
    if(!sid) return;
    const lastCell = tr.lastElementChild;
    if(!lastCell) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'session-flag-btn';
    btn.title = 'Report an issue for this session';
    btn.textContent = '⚑';
    btn.onclick = (event) => {
      event.stopPropagation();
      openIssueReport({
        category: 'session-issue',
        session_id: sid,
        exam_id: row.exam_id || currentExamId || '',
      });
    };
    lastCell.appendChild(btn);
  });
}

function openIssueReport(context={}){
  if(currentOrgRole !== 'teacher') return;
  issueReportContext = {
    session_id: context.session_id || '',
    exam_id: context.exam_id || currentExamId || '',
  };
  const modal = document.getElementById('issue-report-modal');
  const category = document.getElementById('issue-category');
  const severity = document.getElementById('issue-severity');
  const desc = document.getElementById('issue-description');
  const include = document.getElementById('issue-include-context');
  const contextLabel = document.getElementById('issue-context-label');
  const err = document.getElementById('issue-report-error');
  if(category) category.value = context.category || (issueReportContext.session_id ? 'session-issue' : 'bug');
  if(severity) severity.value = context.severity || 'normal';
  if(desc) desc.value = '';
  if(include){
    include.checked = !!issueReportContext.session_id;
    include.disabled = !issueReportContext.session_id;
  }
  if(contextLabel){
    contextLabel.textContent = issueReportContext.session_id
      ? `Context: session ${issueReportContext.session_id}${issueReportContext.exam_id ? `, exam ${issueReportContext.exam_id}` : ''}`
      : 'No session context selected.';
  }
  if(err) err.textContent = '';
  if(modal) modal.classList.remove('hidden');
  setTimeout(()=>{ if(desc) desc.focus(); }, 0);
}

function closeIssueReport(){
  const modal = document.getElementById('issue-report-modal');
  if(modal) modal.classList.add('hidden');
}

async function submitIssueReport(){
  const btn = document.getElementById('issue-submit-btn');
  const err = document.getElementById('issue-report-error');
  const include = document.getElementById('issue-include-context');
  const description = (document.getElementById('issue-description')?.value || '').trim();
  if(description.length < 20){
    if(err) err.textContent = 'Please add at least 20 characters so support has enough context.';
    return;
  }
  const body = {
    category: document.getElementById('issue-category')?.value || 'bug',
    severity: document.getElementById('issue-severity')?.value || 'normal',
    description,
  };
  if(include && include.checked && issueReportContext.session_id){
    body.session_id = issueReportContext.session_id;
    if(issueReportContext.exam_id) body.exam_id = issueReportContext.exam_id;
  }
  if(btn){ btn.disabled = true; btn.textContent = 'Submitting...'; }
  try{
    const r = await authFetch(`${BASE}/api/v1/issues`, {method:'POST', body:JSON.stringify(body)});
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    closeIssueReport();
    showModal('Issue reported', 'Thanks. The Procta team can now triage this from the Issues inbox.');
  }catch(e){
    if(err) err.textContent = e.message || 'Failed to submit issue.';
  }finally{
    if(btn){ btn.disabled = false; btn.textContent = 'Submit'; }
  }
}

async function loadIssues(){
  if(currentOrgRole !== 'superadmin') return;
  if(!orgTeacherOptions.length){
    try{ await loadOrgMembers(); }catch(_){}
  }
  const body = document.getElementById('issues-body');
  if(body) body.innerHTML = '<tr><td colspan="7" class="empty-state">Loading issues...</td></tr>';
  const status = document.getElementById('issues-status-filter')?.value || 'open';
  const category = document.getElementById('issues-category-filter')?.value || 'all';
  const orgId = document.getElementById('issues-org-filter')?.value || '';
  const params = new URLSearchParams();
  if(status && status !== 'all') params.set('status', status);
  if(category && category !== 'all') params.set('category', category);
  if(orgId) params.set('org_id', orgId);
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/issues${params.toString() ? `?${params}` : ''}`);
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    issuesData = d.issues || [];
    const badge = document.getElementById('issues-open-badge');
    if(badge){
      const count = d.open_count || 0;
      badge.textContent = count;
      badge.style.display = count ? '' : 'none';
    }
    _populateIssueOrgFilter(issuesData);
    renderIssuesTable();
  }catch(e){
    if(body) body.innerHTML = `<tr><td colspan="7" class="empty-state">Failed to load issues: ${_escHtml(e.message || e)}</td></tr>`;
  }
}

function _populateIssueOrgFilter(rows){
  const sel = document.getElementById('issues-org-filter');
  if(!sel) return;
  const seen = new Map();
  rows.forEach(i => {
    if(i.org_id && !seen.has(i.org_id)) seen.set(i.org_id, i.org_name || i.org_id);
  });
  orgTeacherOptions.forEach(t => {
    if(t.org_id && !seen.has(t.org_id)) seen.set(t.org_id, t.org_name || t.org_id);
  });
  const current = sel.value;
  sel.innerHTML = '<option value="">All orgs</option>' + Array.from(seen.entries())
    .sort((a,b)=>String(a[1]).localeCompare(String(b[1])))
    .map(([id,name])=>`<option value="${escAttr(id)}">${_escHtml(name)}</option>`)
    .join('');
  sel.value = current;
}

function renderIssuesTable(){
  const body = document.getElementById('issues-body');
  if(!body) return;
  if(!issuesData.length){
    body.innerHTML = '<tr><td colspan="7" class="empty-state">No issues match this filter.</td></tr>';
    return;
  }
  body.innerHTML = issuesData.map(i => {
    const desc = (i.description || '').length > 60 ? `${i.description.slice(0,60)}...` : (i.description || '');
    return `<tr class="issue-row ${currentIssueId===i.id?'active':''}" data-action="openIssueDetail" data-args='${_jsonArgsForAttr(i.id)}'>
      <td><span class="issue-badge status-${escAttr(i.status)}">${_escHtml(_issueLabel(i.status))}</span></td>
      <td><span class="issue-badge severity-${escAttr(i.severity)}">${_escHtml(_issueLabel(i.severity))}</span></td>
      <td>${_escHtml(i.org_name || i.org_id || '—')}</td>
      <td>${_escHtml(i.teacher_name || i.teacher_email || '—')}</td>
      <td>${_escHtml(_issueLabel(i.category))}</td>
      <td>${_escHtml(desc)}</td>
      <td style="font-size:12px;color:var(--muted)">${_escHtml(i.created_at || '—')}</td>
    </tr>`;
  }).join('');
}

function openIssueDetail(issueId){
  currentIssueId = issueId;
  const i = issuesData.find(x => x.id === issueId);
  const detail = document.getElementById('issue-detail');
  renderIssuesTable();
  if(!detail || !i) return;
  detail.innerHTML = `
    <div class="issue-detail-head">
      <span class="issue-badge status-${escAttr(i.status)}">${_escHtml(_issueLabel(i.status))}</span>
      <span class="issue-badge severity-${escAttr(i.severity)}">${_escHtml(_issueLabel(i.severity))}</span>
    </div>
    <h3>${_escHtml(_issueLabel(i.category))}</h3>
    <p class="issue-meta">${_escHtml(i.org_name || i.org_id || 'Unknown org')} · ${_escHtml(i.teacher_name || i.teacher_email || 'Unknown teacher')}</p>
    <div class="issue-description">${_escHtml(i.description || '')}</div>
    ${i.session_id ? `<div class="issue-context">Session: <code>${_escHtml(i.session_id)}</code>${i.exam_id ? `<br>Exam: <code>${_escHtml(i.exam_id)}</code>` : ''}</div>` : ''}
    <label class="issue-field">Status
      <select id="issue-detail-status" class="input">
        ${['open','triaged','resolved'].map(s=>`<option value="${s}" ${i.status===s?'selected':''}>${_escHtml(_issueLabel(s))}</option>`).join('')}
      </select>
    </label>
    <label class="issue-field">Superadmin note
      <textarea id="issue-detail-note" class="input" rows="4">${_escHtml(i.superadmin_note || '')}</textarea>
    </label>
    <div id="issue-detail-result" class="issue-detail-result"></div>
    <button class="btn btn-primary btn-sm" data-action="updateIssueStatus">Save</button>
  `;
}

async function updateIssueStatus(){
  const i = issuesData.find(x => x.id === currentIssueId);
  if(!i) return;
  const result = document.getElementById('issue-detail-result');
  const body = {
    status: document.getElementById('issue-detail-status')?.value || i.status,
    superadmin_note: document.getElementById('issue-detail-note')?.value || '',
  };
  if(result){ result.textContent = 'Saving...'; result.style.color = 'var(--muted)'; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/issues/${encodeURIComponent(i.id)}`, {
      method:'PATCH',
      body:JSON.stringify(body),
    });
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    Object.assign(i, d.issue || body);
    if(result){ result.textContent = 'Saved'; result.style.color = 'var(--emerald)'; }
    openIssueDetail(i.id);
  }catch(e){
    if(result){ result.textContent = e.message || 'Save failed'; result.style.color = 'var(--red)'; }
  }
}



function showInviteTeacherModal(){
  document.getElementById('invite-modal').style.display = 'flex';
  document.getElementById('invite-email').value = '';
  document.getElementById('invite-name').value = '';
  document.getElementById('teacher-invite-result').textContent = '';
}



function sortResults(key){
  if(resSortKey===key) resSortAsc=!resSortAsc;
  else{resSortKey=key;resSortAsc=key==='roll_number'||key==='full_name';}
  renderResults();
}


async function upgradePlan(planId){
  const resultEl = document.getElementById('upgrade-result');
  resultEl.textContent = 'Opening secure checkout...'; resultEl.style.color = 'var(--text-secondary)';
  try{
    await loadRazorpayCheckout();
    const r = await authFetch(`${BASE}/api/v1/billing/checkout/order`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({plan_id:planId})
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail||'Upgrade failed');
    }
    const d = await r.json();
    await openRazorpayCheckout(d, resultEl);
  }catch(e){
    resultEl.textContent = e.message; resultEl.style.color = 'var(--red)';
  }
}

function loadRazorpayCheckout(){
  if(window.Razorpay) return Promise.resolve();
  if(razorpayCheckoutPromise) return razorpayCheckoutPromise;
  razorpayCheckoutPromise = new Promise((resolve, reject)=>{
    const existing = document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]');
    if(existing){
      existing.addEventListener('load', resolve, {once:true});
      existing.addEventListener('error', ()=>reject(new Error('Failed to load Razorpay checkout.')), {once:true});
      return;
    }
    const s = document.createElement('script');
    s.src = 'https://checkout.razorpay.com/v1/checkout.js';
    s.async = true;
    s.onload = resolve;
    s.onerror = ()=>reject(new Error('Failed to load Razorpay checkout.'));
    document.head.appendChild(s);
  });
  return razorpayCheckoutPromise;
}

function openRazorpayCheckout(order, resultEl){
  return new Promise((resolve, reject)=>{
    if(!window.Razorpay) return reject(new Error('Razorpay checkout did not load.'));
    const profile = currentTeacherProfile || {};
    const rzp = new window.Razorpay({
      key: order.key_id,
      amount: order.amount,
      currency: order.currency || 'INR',
      name: 'Procta',
      description: order.description || `${order.plan_name || 'Procta'} plan`,
      order_id: order.order_id,
      prefill: {
        name: profile.full_name || '',
        email: profile.email || '',
      },
      notes: {
        plan_id: order.plan_id || '',
      },
      theme: {color: '#2563eb'},
      modal: {
        confirm_close: true,
        ondismiss: ()=> {
          resultEl.textContent = 'Payment cancelled. No changes were made.';
          resultEl.style.color = 'var(--text-secondary)';
          resolve();
        },
      },
      handler: async function(resp){
        resultEl.textContent = 'Verifying payment...';
        resultEl.style.color = 'var(--text-secondary)';
        try{
          const verifyRes = await authFetch(`${BASE}/api/v1/billing/checkout/verify`, {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
              plan_id: order.plan_id,
              razorpay_order_id: resp.razorpay_order_id,
              razorpay_payment_id: resp.razorpay_payment_id,
              razorpay_signature: resp.razorpay_signature,
            })
          });
          if(!verifyRes.ok){
            const d = await verifyRes.json().catch(()=>({}));
            throw new Error(d.detail || `Payment verification failed (${verifyRes.status})`);
          }
          resultEl.textContent = 'Payment verified. Your plan is active.';
          resultEl.style.color = 'var(--emerald)';
          await loadBilling();
          resolve();
        }catch(e){
          resultEl.textContent = e.message || 'Payment verification failed.';
          resultEl.style.color = 'var(--red)';
          reject(e);
        }
      }
    });
    rzp.on('payment.failed', function(response){
      const msg = response && response.error && response.error.description
        ? response.error.description
        : 'Payment failed. Please try again.';
      resultEl.textContent = msg;
      resultEl.style.color = 'var(--red)';
    });
    rzp.open();
  });
}

async function loadBilling(){
  const planEl = document.getElementById('billing-plan');
  const statusEl = document.getElementById('billing-status');
  const usageEl = document.getElementById('billing-usage');
  const resultEl = document.getElementById('upgrade-result');
  const invoicesWrap = document.getElementById('billing-invoices');
  const invoicesBody = document.getElementById('billing-invoices-body');
  if(resultEl){ resultEl.textContent = 'Loading billing...'; resultEl.style.color = 'var(--text-secondary)'; }
  try{
    const [billingRes, invoiceRes] = await Promise.all([
      authFetch(`${BASE}/api/v1/org/billing`),
      authFetch(`${BASE}/api/v1/billing/invoices`).catch(()=>null),
    ]);
    if(!billingRes.ok){
      const d = await billingRes.json().catch(()=>({}));
      throw new Error(d.detail || `Billing failed (${billingRes.status})`);
    }
    const b = await billingRes.json();
    planEl.textContent = (b.plan || '--').toUpperCase();
    statusEl.textContent = b.status || '--';
    usageEl.textContent = `${b.student_count || 0}/${b.max_students || 0}`;
    if(resultEl) resultEl.textContent = '';

    if(invoiceRes && invoiceRes.ok){
      const invData = await invoiceRes.json();
      const invoices = invData.invoices || [];
      if(invoices.length){
        invoicesWrap.style.display = '';
        invoicesBody.innerHTML = invoices.map(inv => {
          const amountPaise = inv.amount != null ? Number(inv.amount) : 0;
          const amountInr = amountPaise / 100;
          const amount = amountInr > 0 ? `₹${amountInr.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}` : '—';
          const date = inv.created_at ? new Date(inv.created_at).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—';
          const link = inv.pdf_url ? `<a href="${escAttr(inv.pdf_url)}" target="_blank" rel="noreferrer" style="color:var(--accent)">PDF</a>` : '<span style="color:var(--text-muted)">—</span>';
          const statusColors = {paid:'var(--emerald)',pending:'var(--yellow)',failed:'var(--red)',cancelled:'var(--text-muted)'};
          const statusColor = statusColors[inv.status] || 'var(--text-muted)';
          const statusHtml = `<span style="color:${statusColor};font-weight:500">${escAttr((inv.status||'—').charAt(0).toUpperCase()+(inv.status||'—').slice(1))}</span>`;
          return `<tr style="border-bottom:1px solid var(--border-subtle)">
            <td style="padding:8px 12px;white-space:nowrap">${escAttr(date)}</td>
            <td style="padding:8px 12px">${escAttr(inv.description || inv.id || 'Invoice')}</td>
            <td style="padding:8px 12px;text-align:right;font-variant-numeric:tabular-nums">${escAttr(amount)}</td>
            <td style="padding:8px 12px;text-align:center">${statusHtml}</td>
            <td style="padding:8px 12px;text-align:right">${link}</td>
          </tr>`;
        }).join('');
      } else {
        invoicesWrap.style.display = '';
        invoicesBody.innerHTML = '<tr><td colspan="5" style="padding:24px;text-align:center;color:var(--text-muted);font-size:13px">No invoices yet</td></tr>';
      }
    } else {
      invoicesWrap.style.display = '';
      invoicesBody.innerHTML = '<tr><td colspan="5" style="padding:24px;text-align:center;color:var(--text-muted);font-size:13px">Invoice history unavailable</td></tr>';
    }
  }catch(e){
    if(resultEl){ resultEl.textContent = e.message || 'Failed to load billing'; resultEl.style.color = 'var(--red)'; }
    if(planEl) planEl.textContent = '--';
    if(statusEl) statusEl.textContent = '--';
    if(usageEl) usageEl.textContent = '--';
  }
}



// "Full Risk Breakdown" button on the session-detail modal. The dedicated
// risk-breakdown view was never built — the forensics timeline already
// surfaces every risk event with severity + timestamps, so we route the
// button there. Replace this with a real breakdown UI when one exists.
function viewRiskDetail(){
  if(currentSessionId) openTimeline();
}

// "Open full timeline" button on the triage modal — closes triage, swaps
// the currentSessionId to whatever triage was viewing, then opens the
// forensics timeline for it.
function _triageOpenTimeline(){
  const sid = _triageSid;
  closeTriage();
  if(sid){ currentSessionId = sid; openTimeline(); }
}

// ── EXPORTS ─────────────────────────────────────────────────────
let _exporting = {};


async function loadGoogleClassroom(){
  const card = document.getElementById('google-classroom-card');
  const statusEl = document.getElementById('google-status');
  const resultEl = document.getElementById('google-result');
  const connectBtn = document.getElementById('google-connect-btn');
  const coursesDiv = document.getElementById('google-courses');
  if(!card) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/google/courses`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if(!d.connected){
      connectBtn.style.display = '';
      coursesDiv.style.display = 'none';
      statusEl.textContent = 'Not connected. Link your Google account to sync courses and rosters.';
      resultEl.textContent = '';
      return;
    }
    connectBtn.style.display = 'none';
    statusEl.textContent = `Connected as ${escHtml(d.email)}`;
    if(d.error){
      statusEl.textContent += ' (' + d.error + ')';
      connectBtn.style.display = '';
      return;
    }
    // Show course list
    coursesDiv.style.display = '';
    const listEl = document.getElementById('google-course-list');
    listEl.innerHTML = (d.courses||[]).map(c => `
      <label style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-subtle);cursor:pointer" data-cid="${escAttr(c.id)}">
        <input type="checkbox" ${c.linked?'checked':''} data-change-action="_toggleGoogleCourseWrap" data-course-id='${escAttr(c.id)}'>
        <span style="font-size:12px;color:${c.linked?'var(--accent-light)':'var(--text-secondary)'}">${escHtml(c.name)} ${c.section?'('+escHtml(c.section)+')':''}</span>
      </label>
    `).join('');
    // Populate exam select
    const sel = document.getElementById('google-exam-select');
    const exams = document.querySelectorAll('#exam-select option');
    if(sel){
      const curr = sel.value;
      sel.innerHTML = '<option value="">Select exam…</option>';
      document.querySelectorAll('#exam-select option').forEach(o => {
        if(o.value) sel.innerHTML += `<option value="${escAttr(o.value)}">${escHtml(o.text)}</option>`;
      });
      if(curr) sel.value = curr;
    }
    resultEl.textContent = `Found ${d.courses.length} course(s)`;
  }catch(e){
    connectBtn.style.display = '';
    coursesDiv.style.display = 'none';
    statusEl.textContent = 'Failed to load Google Classroom data.';
  }
}

async function connectGoogle(){
  const resultEl = document.getElementById('google-result');
  resultEl.textContent = 'Redirecting to Google...';
  try{
    const r = await authFetch(`${BASE}/api/v1/google/auth`);
    if(!r.ok) throw new Error('Failed to start Google auth');
    const d = await r.json();
    if(d.auth_url){
      window.open(d.auth_url, 'google-auth', 'width=600,height=700');
      // Poll for connection
      const poll = setInterval(async () => {
        const cr = await authFetch(`${BASE}/api/v1/google/courses`);
        if(!cr.ok) return;
        const cd = await cr.json();
        if(cd.connected){
          clearInterval(poll);
          loadGoogleClassroom();
        }
      }, 2000);
      setTimeout(() => clearInterval(poll), 120000);
    }
  }catch(e){ resultEl.textContent = 'Failed: '+e.message; }
}

async function disconnectGoogle(){
  if(!(await appConfirm('Disconnect Google Classroom? Linked courses will be unlinked.', 'Disconnect Google Classroom', {okText:'Disconnect'}))) return;
  const resultEl = document.getElementById('google-result');
  resultEl.textContent = 'Disconnecting...';
  try{
    const r = await authFetch(`${BASE}/api/v1/google/disconnect`, {method:'POST'});
    if(!r.ok) throw new Error();
    resultEl.textContent = 'Disconnected.';
    loadGoogleClassroom();
  }catch(e){ resultEl.textContent = 'Failed to disconnect.'; }
}

async function toggleGoogleCourse(courseId, linked){
  const resultEl = document.getElementById('google-result');
  const examId = document.getElementById('google-exam-select').value;
  if(linked && !examId){
    resultEl.textContent = 'Select an exam first before linking a course.';
    return;
  }
  try{
    const ep = linked ? 'link-exam' : 'unlink-exam';
    const r = await authFetch(`${BASE}/api/v1/google/${ep}`, {
      method:'POST',
      body: JSON.stringify({course_id: courseId, exam_id: examId}),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    resultEl.textContent = linked ? 'Course linked to exam.' : 'Course unlinked.';
  }catch(e){ resultEl.textContent = 'Failed: '+e.message; }
}

async function syncGoogleRoster(){
  const courseId = document.querySelector('#google-course-list input:checked');
  const examId = document.getElementById('google-exam-select').value;
  if(!courseId){ showModal('Select a course first.'); return; }
  const resultEl = document.getElementById('google-result');
  resultEl.textContent = 'Syncing roster...';
  try{
    const r = await authFetch(`${BASE}/api/v1/google/sync-roster`, {
      method:'POST',
      body: JSON.stringify({course_id: courseId.dataset.cid, exam_id: examId}),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    resultEl.textContent = `Imported ${d.imported} students (${d.total} total in course).`;
  }catch(e){ resultEl.textContent = 'Failed: '+e.message; }
}

// ── TOOLS ───────────────────────────────────────────────────────
async function doBackfill(){
  const btn=document.getElementById('btn-backfill');
  btn.disabled=true; btn.textContent='Running...';
  const el=document.getElementById('backfill-result');
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/backfill-risk-scores${_examQuery('?')}`,{method:'POST'});
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    el.style.color='var(--emerald)';
    el.textContent=`Done! Backfilled ${d.backfilled} sessions.`;
    refreshResults();
  }catch(e){
    el.style.color='var(--red)';
    el.textContent='Failed: '+e.message;
  }
  btn.disabled=false; btn.textContent='Run Backfill';
}

async function doCleanup(){
  const el=document.getElementById('cleanup-result');
  try{
    const r=await authFetch(`${BASE}/api/v1/admin-cleanup`,{method:'POST'});
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    el.style.color='var(--emerald)';
    el.textContent=`Deleted ${d.deleted} old files.`;
  }catch(e){
    el.style.color='var(--red)';
    el.textContent='Failed: '+e.message;
  }
}

// ── Clear Live Sessions (destructive, double-confirm) ───────────
// State machine on a single button: idle → first-confirm → final-confirm.
// First click: asks the server for a preview + token ("Are you sure?").
// Second click: "Really wipe N sessions? Click again to confirm."
// Third click: sends the ack back with the token and deletes.
let _clearLiveState = 'idle';
let _clearLiveToken = '';
let _clearLiveCount = 0;
let _clearLiveTimer = null;

function _clearLiveReset(){
  _clearLiveState='idle';
  _clearLiveToken='';
  _clearLiveCount=0;
  if(_clearLiveTimer){ clearTimeout(_clearLiveTimer); _clearLiveTimer=null; }
  const btn=document.getElementById('clear-live-btn');
  if(btn){
    btn.textContent='Clear Live Sessions';
    btn.disabled=false;
    btn.style.color='var(--red)';
  }
  const prev=document.getElementById('clear-live-preview');
  if(prev) prev.textContent='';
}

function _clearIncludeCompleted(){
  return document.getElementById('clear-include-completed')?.checked || false;
}
function _clearIncludeActive(){
  return document.getElementById('clear-include-active')?.checked || false;
}

async function clearLiveSessionsStep(){
  const btn=document.getElementById('clear-live-btn');
  const prev=document.getElementById('clear-live-preview');
  const out=document.getElementById('clear-live-result');
  out.textContent='';
  const inclComp=_clearIncludeCompleted();
  const inclActive=_clearIncludeActive();
  // Scope the wipe to the currently-selected exam when one is picked
  // (multi-exam teachers). Fall back to "" which the backend reads as
  // "all exams for this teacher" — preserving single-exam behaviour.
  const examScope=currentExamId||'';

  if(_clearLiveState==='idle'){
    btn.disabled=true;
    btn.textContent='Checking...';
    try{
      const r=await authFetch(`${BASE}/api/v1/admin/clear-live-sessions`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          step:'request',
          include_completed:inclComp,
          include_active:inclActive,
          exam_id:examScope,
        })
      });
      if(!r.ok){
        const err=await r.json().catch(()=>({detail:'Request failed'}));
        throw new Error(err.detail||('HTTP '+r.status));
      }
      const d=await r.json();
      _clearLiveToken=d.token||'';
      const staleCount=d.stale_count||0;
      const compCount=d.completed_count||0;
      _clearLiveCount=(d.count!==undefined?d.count:staleCount);
      const activeCount=d.active_count||0;
      const win=d.active_window_s||120;
      _clearLiveState='first';
      btn.disabled=false;
      if(_clearLiveCount===0){
        out.style.color=activeCount>0?'var(--amber)':'var(--muted)';
        out.textContent=activeCount>0
          ? `Nothing to clear — ${activeCount} student(s) are actively taking the exam right now (heartbeat within ${win}s). Wait for them to finish.`
          : 'No sessions to clear.';
        _clearLiveReset();
        return;
      }
      btn.textContent=`Are you sure? Click to confirm (${_clearLiveCount})`;
      btn.style.color='var(--amber)';
      const rolls=(d.preview||[]).map(p=>esc(p.roll_number||p.session_key)).slice(0,10).join(', ');
      const activeRolls=(d.active_preview||[]).map(p=>esc(p.roll_number||p.session_key)).slice(0,6).join(', ');
      let html='';
      if(staleCount>0) html+=`Will wipe <b>${staleCount}</b> stale live session(s)${rolls?': '+rolls:''}${staleCount>10?' ...':''}`;
      if(compCount>0){
        const compRolls=(d.completed_preview||[]).map(p=>esc(p.roll_number||p.session_key)).slice(0,10).join(', ');
        html+=(html?'<br>':'')+`<span style="color:var(--amber)">+ <b>${compCount}</b> completed (submitted) session(s)${compRolls?' — '+compRolls:''}</span>`;
      }
      if(activeCount>0){
        html+=`<br><span style="color:var(--emerald)">Protected (still taking exam): <b>${activeCount}</b>${activeRolls?' — '+activeRolls:''}${activeCount>6?' ...':''}</span>`;
      }
      prev.innerHTML=html;
      _clearLiveTimer=setTimeout(()=>{
        out.style.color='var(--muted)';
        out.textContent='Confirmation timed out — click again to restart.';
        _clearLiveReset();
      }, 30000);
    }catch(e){
      out.style.color='var(--red)';
      out.textContent='Failed: '+e.message;
      _clearLiveReset();
    }
    return;
  }

  if(_clearLiveState==='first'){
    _clearLiveState='final';
    const bits=[];
    if(inclComp) bits.push('completed results');
    if(inclActive) bits.push('ACTIVE students (forced out mid-exam)');
    const extra=bits.length?' INCLUDING '+bits.join(' + '):'';
    btn.textContent=`This deletes answers, violations & screenshots${extra}. Click once more to wipe ${_clearLiveCount}.`;
    btn.style.color='var(--red)';
    return;
  }

  if(_clearLiveState==='final'){
    btn.disabled=true;
    btn.textContent='Wiping...';
    try{
      const reauth_token = await _get2FAReauthToken('clear live sessions');
      if(!reauth_token) throw new Error('Re-authentication required');
      const r=await authFetch(`${BASE}/api/v1/admin/clear-live-sessions`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          step:'confirm',
          token:_clearLiveToken,
          ack:'DELETE',
          include_completed:inclComp,
          include_active:inclActive,
          exam_id:examScope,
          reauth_token,
        })
      });
      if(!r.ok){
        const err=await r.json().catch(()=>({detail:'Failed'}));
        throw new Error(err.detail||('HTTP '+r.status));
      }
      const d=await r.json();
      out.style.color='var(--emerald)';
      let msg=`Cleared ${d.cleared} session(s)`;
      if(d.completed_cleared>0) msg+=` (${d.completed_cleared} completed)`;
      msg+=`. Deleted ${d.answers} answers, ${d.violations} violation events, ${d.screenshots} screenshots.`;
      if(d.skipped_active>0){
        msg+=` Protected ${d.skipped_active} active student(s) still taking the exam.`;
      }
      out.textContent=msg;
      if(typeof refreshLive==='function') refreshLive();
      if(typeof refreshResults==='function') refreshResults();
    }catch(e){
      out.style.color='var(--red)';
      out.textContent='Failed: '+e.message;
    }finally{
      _clearLiveReset();
    }
    return;
  }
}

async function loadFailed(){
  const el=document.getElementById('failed-result');
  try{
    const r=await authFetch(`${BASE}/api/v1/admin-failed-sessions${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    if(d.count===0){
      el.innerHTML='<span style="color:var(--emerald)">No failed sessions!</span>';
    } else {
      el.innerHTML=d.failed_sessions.map(s=>`
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span style="font-size:12px;color:var(--text)">${esc(s)}</span>
          <button class="btn btn-secondary btn-sm" data-action="forceSubmit" data-args='${_jsonArgsForAttr(s)}' style="padding:2px 8px;font-size:11px">Force Submit</button>
        </div>
      `).join('');
    }
  }catch(e){
    el.innerHTML='<span style="color:var(--red)">Failed to load.</span>';
  }
}

async function loadFailedCount(){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin-failed-sessions${_examQuery('?')}`);
    if(!r.ok) return;
    const d=await r.json();
    document.getElementById('tools-failed').textContent=d.count;
  }catch(e){}
}

async function forceSubmit(sid){
  if(!(await appConfirm(`Force-submit session ${sid}?`, 'Force submit session', {okText:'Force submit'}))) return;
  try{
    const reauth_token = await _get2FAReauthToken('force-submit this session');
    if(!reauth_token) return;
    const r=await authFetch(`${BASE}/api/v1/admin-submit/${encodeURIComponent(sid)}`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({reauth_token})
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    showModal(`Force-submitted! Score: ${d.score}/${d.total}, Risk: ${d.risk_score}/100`);
    loadFailed();
    refreshResults();
  }catch(e){showModal('Failed: '+e.message);}
}

// ── ACCESS CODE ────────────────────────────────────────────────
async function loadAccessCode(){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/access-code${_examQuery('?')}`);
    if(!r.ok) return;
    const d=await r.json();
    document.getElementById('access-code-input').value=d.access_code||'';
    const st=document.getElementById('access-code-status');
    st.style.color=d.enabled?'var(--emerald)':'var(--muted)';
    st.textContent=d.enabled?`Active: students need code "${d.access_code}" to start`:'Disabled: no code required';
  }catch(e){}
}

function generateAccessCode(){
  const chars='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no I/O/0/1 to avoid confusion
  let code='';
  for(let i=0;i<6;i++) code+=chars[Math.floor(Math.random()*chars.length)];
  document.getElementById('access-code-input').value=code;
}

async function saveAccessCode(){
  const code=document.getElementById('access-code-input').value.trim().toUpperCase();
  const st=document.getElementById('access-code-status');
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/access-code`,{
      method:'POST',body:JSON.stringify({access_code:code, exam_id:currentExamId})
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    st.style.color=d.enabled?'var(--emerald)':'var(--muted)';
    st.textContent=d.enabled?`Saved! Students need code "${d.access_code}" to start`:'Cleared — no code required';
  }catch(e){
    st.style.color='var(--red)';
    st.textContent='Failed to save: '+e.message;
  }
}

function clearAccessCode(){
  document.getElementById('access-code-input').value='';
  saveAccessCode();
}

// ── REGISTERED COUNT ────────────────────────────────────────────
async function loadRegisteredCount(){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/registered-count`);
    if(!r.ok) return;
    const d=await r.json();
    document.getElementById('tools-registered').textContent=d.count;
  }catch(e){}
}

// ── EXAM SCHEDULE ───────────────────────────────────────────────
async function loadSchedule(){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/exam-schedule${_examQuery('?')}`);
    if(!r.ok) return;
    const d=await r.json();
    if(d.starts_at) document.getElementById('schedule-start').value=utcToLocalInput(d.starts_at);
    if(d.ends_at) document.getElementById('schedule-end').value=utcToLocalInput(d.ends_at);
    updateScheduleStatus(d);
  }catch(e){}
}

function utcToLocalInput(iso){
  if(!iso) return '';
  // Convert UTC ISO → BROWSER LOCAL datetime-local input format
  // (audit L1 — was hardcoded IST). new Date(iso) parses the UTC
  // timestamp; getFullYear()/getMonth()/etc. then return the user's
  // local time components, which we format as `YYYY-MM-DDTHH:MM`.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localInputToUtc(val){
  if(!val) return null;
  // Treat the datetime-local input as the user's BROWSER local time
  // (audit L1 — was hardcoded IST). The Date constructor with
  // integer year/month/day/hour/minute uses local time naturally;
  // .toISOString() then returns the UTC equivalent. A teacher in
  // Dubai scheduling "10:00 AM" now correctly stores 06:00 UTC,
  // and a teacher in Delhi correctly stores 04:30 UTC.
  const parts = val.split('T');
  if(parts.length !== 2) return null;
  const [y, mo, d] = parts[0].split('-').map(Number);
  const [h, mi] = parts[1].split(':').map(Number);
  if (!y || !mo || !d || Number.isNaN(h) || Number.isNaN(mi)) return null;
  const local = new Date(y, mo - 1, d, h, mi, 0);
  return local.toISOString();
}

function updateScheduleStatus(d){
  const st=document.getElementById('schedule-status');
  if(!d.starts_at&&!d.ends_at){
    st.style.color='var(--muted)';
    st.textContent='No schedule set — exam is always accessible.';
    return;
  }
  // Use browser locale + timezone so non-India users see local times.
  // (Audit M3 — was hardcoded Asia/Kolkata.) Falls back to IST if Intl is unavailable.
  const tz = (()=>{ try { return Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Kolkata'; } catch(_){ return 'Asia/Kolkata'; } })();
  const locale = navigator.language || 'en-IN';
  const opts={timeZone:tz,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:true,timeZoneName:'short'};
  let msg='';
  if(d.starts_at) msg+='Opens: '+new Date(d.starts_at).toLocaleString(locale,opts);
  if(d.ends_at) msg+=(msg?' | ':'')+'Closes: '+new Date(d.ends_at).toLocaleString(locale,opts);
  const now=new Date();
  let color='var(--emerald)';
  if(d.starts_at&&now<new Date(d.starts_at)){color='var(--amber)';msg+=' (not started yet)';}
  else if(d.ends_at&&now>new Date(d.ends_at)){color='var(--red)';msg+=' (ended)';}
  else{msg+=' (active now)';}
  st.style.color=color;
  st.textContent=msg;
}

async function saveSchedule(){
  const st=document.getElementById('schedule-status');
  const starts=document.getElementById('schedule-start').value;
  const ends=document.getElementById('schedule-end').value;
  // Validate end > start if both set
  if(starts&&ends&&new Date(starts)>=new Date(ends)){
    st.style.color='var(--red)';
    st.textContent='End time must be after start time.';
    return;
  }
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/exam-schedule`,{
      method:'POST',
      body:JSON.stringify({starts_at:localInputToUtc(starts),ends_at:localInputToUtc(ends),exam_id:currentExamId})
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    st.style.color='var(--emerald)';
    st.textContent='Schedule saved!';
    setTimeout(()=>loadSchedule(),1000);
  }catch(e){
    st.style.color='var(--red)';
    st.textContent='Failed: '+e.message;
  }
}

function clearSchedule(){
  document.getElementById('schedule-start').value='';
  document.getElementById('schedule-end').value='';
  saveSchedule();
}

// ── SHUFFLE CONFIG ──────────────────────────────────────────────
async function loadShuffleConfig(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/shuffle-config${_examQuery('?')}`);
    if(!r.ok) return;
    const d = await r.json();
    const qEl = document.getElementById('shuffle-q-toggle');
    const oEl = document.getElementById('shuffle-o-toggle');
    if(qEl) qEl.checked = !!d.shuffle_questions;
    if(oEl) oEl.checked = !!d.shuffle_options;
    updateShuffleStatus(d);
    // Phone camera config piggybacks on the same endpoint
    const pcEl = document.getElementById('phone-cam-toggle');
    if(pcEl) pcEl.checked = !!d.phone_camera_enabled;
  }catch(e){ /* silent */ }
}

function updateShuffleStatus(d){
  const st = document.getElementById('shuffle-status');
  if(!st) return;
  const sq = !!d.shuffle_questions, so = !!d.shuffle_options;
  if(sq && so){
    st.style.color = 'var(--emerald)';
    st.textContent = 'Active — every student gets a unique question and option order.';
  } else if(sq){
    st.style.color = 'var(--emerald)';
    st.textContent = 'Question order is randomized per student. Options stay in the original order.';
  } else if(so){
    st.style.color = 'var(--emerald)';
    st.textContent = 'Option order is randomized per student. Questions stay in the original order.';
  } else {
    st.style.color = 'var(--muted)';
    st.textContent = 'Randomization is off — every student sees the exact same exam.';
  }
}

async function saveShuffleConfig(){
  const qEl = document.getElementById('shuffle-q-toggle');
  const oEl = document.getElementById('shuffle-o-toggle');
  const st = document.getElementById('shuffle-status');
  const body = {
    shuffle_questions: !!qEl.checked,
    shuffle_options:   !!oEl.checked,
    exam_id: currentExamId,
  };
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/shuffle-config`,{
      method:'POST',
      body: JSON.stringify(body),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    updateShuffleStatus(body);
  }catch(e){
    st.style.color = 'var(--red)';
    st.textContent = 'Failed to save: '+e.message;
  }
}

async function savePhoneCamConfig(){
  const el = document.getElementById('phone-cam-toggle');
  const st = document.getElementById('phone-cam-status');
  if(!el || !currentExamId) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/phone-camera-config`,{
      method:'POST',
      body: JSON.stringify({exam_id: currentExamId, enabled: el.checked}),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    st.style.color = 'var(--emerald)';
    st.textContent = el.checked ? 'Room camera required for this exam.' : 'Room camera disabled.';
  }catch(e){
    st.style.color = 'var(--red)';
    st.textContent = 'Failed to save: '+e.message;
  }
}

// ── QUESTIONS EDITOR ────────────────────────────────────────────
let qData = [];       // array of question objects (with correct answers)
let qPreviewMode = false;
let qDirty = false;   // unsaved changes flag

async function loadQuestions(){
  const el = document.getElementById('q-editor');
  el.innerHTML='<div class="loading-msg"><span class="spinner"></span> Loading questions...</div>';
  document.getElementById('q-save-msg').textContent='';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/questions${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    qData = d.questions || [];
    document.getElementById('q-title').value = d.exam_title || '';
    document.getElementById('q-duration').value = d.duration_minutes || 60;
    qDirty = false;
    renderQEditor();
    renderQStats();
  if(typeof renderQSidebar==='function') renderQSidebar();
  }catch(e){
    el.innerHTML='<p style="color:var(--red)">Failed to load questions.</p>';
  }
}

function renderQStats(){
  const el = document.getElementById('q-stats');
  const total = qData.length;
  const optCounts = qData.map(q=>Object.keys(q.options||{}).length);
  const avgOpts = total ? Math.round(optCounts.reduce((a,b)=>a+b,0)/total*10)/10 : 0;
  const noCorrect = qData.filter(q=>!q.correct).length;
  const withImg = qData.filter(q=>!!q.image_url).length;
  const dur = parseInt(document.getElementById('q-duration').value)||60;
  // Phase 2: emit .stat-tile shape so questions tab matches the rest
  // of the dashboard. Missing-Answer tile flips to error-fg when there
  // ARE missing answers so the issue surfaces at a glance.
  const missingCls = noCorrect
    ? 'class="stat-tile-value" style="color:var(--sev-error-fg)"'
    : 'class="stat-tile-value success"';
  el.innerHTML = `
    <div class="stat-tile"><div class="stat-tile-label">Total Questions</div><div class="stat-tile-value accent">${total}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Avg Options</div><div class="stat-tile-value">${avgOpts}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">With Image</div><div class="stat-tile-value">${withImg}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Duration</div><div class="stat-tile-value success">${dur} min</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Missing Answer</div><div ${missingCls}>${noCorrect}</div></div>
  `;
}

// ── Helpers for the new question schema ────────────────────────
// `correct` is stored as a canonical comma-joined string. Use these
// helpers so single/multi/true-false all use a consistent shape.
function qGetCorrectSet(q){
  return new Set(String(q.correct||'').split(',').map(s=>s.trim()).filter(Boolean));
}
function qSetCorrectSet(q, setOrArr){
  const arr=[...setOrArr].filter(Boolean).sort();
  q.correct=arr.join(',');
}
function qNormaliseType(q){
  let t=(q.question_type||'mcq_single').toString().trim().toLowerCase();
  if(!['mcq_single','mcq_multi','true_false','short_answer'].includes(t)) t='mcq_single';
  q.question_type=t;
  return t;
}
function qTypeLabel(t){
  if(t==='mcq_multi') return 'Multi-select';
  if(t==='true_false') return 'True / False';
  if(t==='short_answer') return 'Short answer (AI-graded)';
  return 'Single choice';
}
function qBuildImageUrl(u){
  if(!u) return '';
  // Server-issued relative URLs need the admin Bearer token. We use a
  // blob fetched via authFetch so we never leak the token into the DOM.
  return u;
}

// Fetch authenticated image bytes and convert to a blob URL for <img>.
const _qImgBlobCache = new Map();
async function qLoadImgSrc(url){
  if(!url) return '';
  if(_qImgBlobCache.has(url)) return _qImgBlobCache.get(url);
  try{
    const r = await authFetch(BASE + url);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const blob = await r.blob();
    const obj = URL.createObjectURL(blob);
    _qImgBlobCache.set(url, obj);
    return obj;
  }catch(e){
    console.warn('image load failed', url, e);
    return '';
  }
}

function markQDirty(){
  qDirty = true;
  document.getElementById('q-save-msg').innerHTML=
    '<span style="color:var(--amber)">Unsaved changes</span>';
}

function renderQEditor(){
  if(qPreviewMode){ renderQPreview(); return; }
  document.getElementById('q-preview').style.display='none';
  const el = document.getElementById('q-editor');
  el.style.display='block';
  if(!qData.length){
    el.innerHTML='<div class="loading-msg">No questions yet. Click "+ Add Question" to start.</div>';
    return;
  }
  el.innerHTML = qData.map((q,i)=>{
    const qtype = qNormaliseType(q);
    // Force fixed True/False options when the type is true_false so the
    // teacher can't accidentally leave stale A/B/C/D around.
    if(qtype==='true_false'){
      q.options = {'True':'True','False':'False'};
      const cs = qGetCorrectSet(q);
      if(cs.size!==1 || (!cs.has('True') && !cs.has('False'))){
        qSetCorrectSet(q, ['True']);
      }
    }
    const isShort = qtype==='short_answer';
    const optKeys = Object.keys(q.options||{});
    const correctSet = qGetCorrectSet(q);
    const isMulti = qtype==='mcq_multi';
    const optionsEditable = qtype!=='true_false';

    const correctStatusText = correctSet.size
      ? [...correctSet].sort().map(k=>`${k} = ${escAttr(q.options[k]||'?')}`).join('  |  ')
      : 'Not set!';
    const correctStatusColor = correctSet.size?'var(--emerald)':'var(--red)';

    return `<div class="q-card" id="qcard-${i}">
      <div class="q-card-hdr">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="q-num">Q${i+1}</span>
          <span class="q-type-badge">${qTypeLabel(qtype)}</span>
          <button class="q-move" data-action="moveQ" data-args='${_jsonArgsForAttr(i,-1)}' title="Move up" ${i===0?'disabled':''}>&#9650;</button>
          <button class="q-move" data-action="moveQ" data-args='${_jsonArgsForAttr(i,1)}' title="Move down" ${i===qData.length-1?'disabled':''}>&#9660;</button>
        </div>
        <button class="btn btn-secondary btn-sm" data-action="saveQuestionToBank" data-args='${_jsonArgsForAttr(i)}' style="padding:2px 8px;font-size:10px;margin-right:6px" title="Save to question bank">Save to Bank</button>
        <button class="q-del" data-action="deleteQ" data-args='${_jsonArgsForAttr(i)}'>Delete</button>
      </div>
      <!-- Lint slot — populated by lintQuestions() / clearLint().
           Empty by default so rows that haven't been linted are
           visually unchanged. -->
      <div class="q-lint-slot" id="qlint-${i}"></div>
      <div class="q-type-row">
        <label for="qtype-${i}">Question type</label>
        <select id="qtype-${i}" data-change-action="_setQTypeWrap" data-qidx='${i}'>
          <option value="mcq_single" ${qtype==='mcq_single'?'selected':''}>Single choice (MCQ)</option>
          <option value="mcq_multi"  ${qtype==='mcq_multi'?'selected':''}>Multi-select (2+ correct)</option>
          <option value="true_false" ${qtype==='true_false'?'selected':''}>True / False</option>
          <option value="short_answer" ${qtype==='short_answer'?'selected':''}>Short answer (AI-graded)</option>
        </select>
      </div>
      <div class="q-field">
        <label>Question Text</label>
        <textarea data-input-action="_setQQuestion" data-qidx='${i}'>${escAttr(q.question||'')}</textarea>
      </div>
      <div class="q-img-wrap">
        <div class="q-img-thumb" id="qimg-${i}">${q.image_url?'loading...':'No image'}</div>
        <div class="q-img-actions">
          <button class="q-img-btn" data-action="_clickQImageInput" data-args='${_jsonArgsForAttr(i)}'>
            ${q.image_url?'Replace image':'+ Upload image'}
          </button>
          ${q.image_url?`<button class="q-img-btn danger" data-action="clearQImage" data-args='${_jsonArgsForAttr(i)}'>Remove image</button>`:''}
          <input type="file" id="qimg-input-${i}" accept="image/png,image/jpeg,image/gif,image/webp"
                 style="display:none" data-change-action="_handleQImageUploadWrap" data-qidx='${i}'>
        </div>
      </div>
      ${isShort?`
      <div class="q-field">
        <label>Reference answer (model answer the AI grades against)</label>
        <textarea data-input-action="_setQRefAnswer" data-qidx='${i}' placeholder="e.g. Atomicity, Consistency, Isolation, Durability">${escAttr(q.reference_answer||'')}</textarea>
      </div>
      <div class="q-field">
        <label>Rubric (optional grading guidance for the AI)</label>
        <textarea data-input-action="_setQRubric" data-qidx='${i}' placeholder="e.g. Accept any of: atomicity / consistency / isolation / durability. Half marks for 2+ of 4. Lenient on spelling.">${escAttr(q.rubric||'')}</textarea>
      </div>
      <div class="q-correct-row">
        <label>Max score</label>
        <input type="number" min="0.5" step="0.5" value="${escAttr(String(q.max_score||1))}"
               data-input-action="_setQMaxScore" data-qidx='${i}'
               style="width:80px;padding:4px 6px">
        <span class="q-correct-status" style="color:var(--muted);font-size:11px;margin-left:8px">
          Students type a free-text answer. The AI suggests a score; you confirm it from the Results tab.
        </span>
      </div>
      `:`
      <div class="q-field">
        <label>Options ${isMulti?'(check all correct)':qtype==='true_false'?'(fixed)':'(pick one correct)'}</label>
        <div class="q-opts">
          ${optKeys.map(k=>{
            const esc=escJs(k);
            const hit=correctSet.has(k);
            const keyCls=hit?(isMulti?'correct-multi':'correct'):'';
            return `
            <div class="q-opt">
              <div class="q-opt-key ${keyCls}"
                   data-action="toggleCorrect" data-args='${_jsonArgsForAttr(i,esc)}'
                   title="${isMulti?'Click to toggle correct':'Click to set as correct'}"
                   style="cursor:pointer">${esc}</div>
              <input value="${escAttr(q.options[k])}"
                     ${optionsEditable?`data-input-action="_setQOption" data-qidx='${i}' data-okey='${esc}'`:'readonly'}
                     placeholder="Option ${esc}">
              ${(optionsEditable && optKeys.length>2)?`<button class="q-opt-remove" data-action="removeOpt" data-args='${_jsonArgsForAttr(i,esc)}' title="Remove option">&times;</button>`:''}
            </div>`;
          }).join('')}
        </div>
        ${optionsEditable?`<button class="q-add-opt" data-action="addOpt" data-args='${_jsonArgsForAttr(i)}'>+ Add Option</button>`:''}
      </div>
      <div class="q-correct-row">
        <label>Correct</label>
        <span class="q-correct-status" style="color:${correctStatusColor}">
          ${escAttr(correctStatusText)}
        </span>
      </div>
      `}
    </div>`;
  }).join('');
  renderQStats();
  if(typeof renderQSidebar==='function') renderQSidebar();
  // Resolve image thumbnails asynchronously (fetched with auth token).
  qData.forEach((q,i)=>{
    if(!q.image_url) return;
    qLoadImgSrc(q.image_url).then(src=>{
      const el=document.getElementById('qimg-'+i);
      if(!el) return;
      if(src){ const img=document.createElement('img'); img.src=src; img.alt=`Q${i+1} image`; el.textContent=''; el.appendChild(img); }
      else el.textContent='Load failed';
    });
  });
}

function setQType(idx, val){
  const q = qData[idx];
  if(!q) return;
  q.question_type = val;
  if(val==='short_answer'){
    // No options/correct for free-text. Seed grading defaults so the
    // teacher sees the new fields populated rather than empty placeholders.
    q.options = {};
    q.correct = '';
    if(typeof q.reference_answer !== 'string') q.reference_answer = '';
    if(typeof q.rubric !== 'string') q.rubric = '';
    if(typeof q.max_score !== 'number' || !q.max_score) q.max_score = 1;
  }else if(val==='true_false'){
    q.options = {'True':'True','False':'False'};
    qSetCorrectSet(q, ['True']);
  }else if(val==='mcq_single'){
    const cs = qGetCorrectSet(q);
    if(cs.size>1){
      // Keep only the first correct pick
      qSetCorrectSet(q, [[...cs][0]]);
    }
    // If options are still True/False (from a prior toggle), or empty
    // (coming from short_answer), restore A/B/C/D.
    const keys = Object.keys(q.options||{});
    const isTF = keys.length===2 && keys.includes('True') && keys.includes('False');
    if(isTF || keys.length<2){
      q.options = {A:'',B:'',C:'',D:''};
      qSetCorrectSet(q, ['A']);
    }
  }else if(val==='mcq_multi'){
    const keys = Object.keys(q.options||{});
    const isTF = keys.length===2 && keys.includes('True') && keys.includes('False');
    if(isTF || keys.length<2){
      q.options = {A:'',B:'',C:'',D:''};
      qSetCorrectSet(q, []);
    }
    // A multi question must have ≥2 correct — leave it to save-time validation.
  }
  markQDirty();
  renderQEditor();
}

function toggleCorrect(idx, key){
  const q = qData[idx];
  if(!q) return;
  const qtype = qNormaliseType(q);
  const cs = qGetCorrectSet(q);
  if(qtype==='mcq_multi'){
    if(cs.has(key)) cs.delete(key); else cs.add(key);
  }else{
    // single / true_false: exactly one correct
    cs.clear();
    cs.add(key);
  }
  qSetCorrectSet(q, cs);
  markQDirty();
  renderQEditor();
}

async function handleQImageUpload(idx, file){
  if(!file) return;
  if(file.size > 4*1024*1024){
    showModal('Image too large (max 4MB).');
    return;
  }
  const reader = new FileReader();
  reader.onload = async ()=>{
    const b64 = String(reader.result||'').split(',')[1] || '';
    if(!b64){ showModal('Failed to read image.'); return; }
    try{
      const r = await authFetch(`${BASE}/api/v1/admin/upload-question-image`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({image:b64, filename:file.name||''})
      });
      if(!r.ok){
        const err = await r.json().catch(()=>({detail:'Upload failed'}));
        throw new Error(err.detail||('HTTP '+r.status));
      }
      const d = await r.json();
      qData[idx].image_url = d.url;
      markQDirty();
      renderQEditor();
    }catch(e){
      showModal('Image upload failed: '+e.message);
    }
  };
  reader.onerror = ()=>showModal('Failed to read file.');
  reader.readAsDataURL(file);
}

function clearQImage(idx){
  if(!qData[idx]) return;
  qData[idx].image_url = '';
  markQDirty();
  renderQEditor();
}

function escAttr(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;').replace(/`/g,'&#96;').replace(/\//g,'&#47;');
}

function _escHtml(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
}

function escJs(s){
  return String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'\\"').replace(/`/g,'\\`').replace(/\n/g,'\\n').replace(/\r/g,'\\r').replace(/</g,'\\x3c');
}

let _appDialogResolve = null;
let _appDialogMode = 'alert';

function _appModalEls(){
  return {
    overlay: document.getElementById('app-modal-overlay'),
    title: document.getElementById('app-modal-title'),
    body: document.getElementById('app-modal-body'),
    ok: document.getElementById('app-modal-ok'),
    cancel: document.getElementById('app-modal-cancel'),
  };
}

function _openAppDialog({title='Procta', body='', mode='alert', defaultValue='', multiline=false, inputType='text', okText='OK', cancelText='Cancel'} = {}){
  const els = _appModalEls();
  if(!els.overlay || !els.title || !els.body || !els.ok || !els.cancel){
    return Promise.resolve(mode === 'confirm' ? false : null);
  }
  if(_appDialogResolve) _appDialogResolve(mode === 'confirm' ? false : null);
  _appDialogMode = mode;
  els.title.textContent = title;
  els.body.innerHTML = '';
  const msg = document.createElement('div');
  msg.textContent = body || '';
  els.body.appendChild(msg);
  if(mode === 'prompt'){
    const input = multiline ? document.createElement('textarea') : document.createElement('input');
    input.id = 'app-modal-prompt';
    if(!multiline) input.type = inputType || 'text';
    input.value = defaultValue || '';
    input.style.cssText = 'width:100%;margin-top:12px;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:10px;color:var(--text);padding:10px 12px;font-size:13px;outline:none;box-sizing:border-box';
    if(multiline){
      input.rows = 4;
      input.style.resize = 'vertical';
    }
    els.body.appendChild(input);
    setTimeout(()=>input.focus(), 0);
  }
  els.ok.textContent = okText || 'OK';
  els.cancel.textContent = cancelText || 'Cancel';
  els.cancel.style.display = mode === 'alert' ? 'none' : '';
  els.overlay.style.display = 'flex';
  return new Promise(resolve => { _appDialogResolve = resolve; });
}

function _resolveAppDialog(value){
  const els = _appModalEls();
  if(els.overlay) els.overlay.style.display = 'none';
  const resolve = _appDialogResolve;
  _appDialogResolve = null;
  if(resolve) resolve(value);
}

function confirmAppModal(){
  if(_appDialogMode === 'prompt'){
    const input = document.getElementById('app-modal-prompt');
    _resolveAppDialog(input ? input.value : '');
    return;
  }
  _resolveAppDialog(true);
}

function cancelAppModal(){
  _resolveAppDialog(_appDialogMode === 'confirm' ? false : null);
}

function closeAppModal(){
  cancelAppModal();
}

function showModal(title, message){
  const finalTitle = message === undefined ? 'Procta' : title;
  const finalMessage = message === undefined ? title : message;
  return _openAppDialog({title: finalTitle, body: finalMessage, mode:'alert'});
}

function appConfirm(message, title='Please confirm', opts={}){
  return _openAppDialog({title, body: message, mode:'confirm', okText: opts.okText || 'Confirm', cancelText: opts.cancelText || 'Cancel'});
}

function appPrompt(message, defaultValue='', opts={}){
  return _openAppDialog({title: opts.title || 'Procta', body: message, mode:'prompt', defaultValue, multiline: !!opts.multiline, inputType: opts.inputType || 'text', okText: opts.okText || 'OK'});
}

function setCorrect(idx,key){
  // Legacy single-correct setter kept for backwards compat with any
  // callers still out there. Delegates to toggleCorrect, which handles
  // all three question types.
  toggleCorrect(idx, key);
}

function addOpt(idx){
  const opts=qData[idx].options||{};
  const keys=Object.keys(opts);
  // Next letter: A,B,C,D,E,F...
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  let nextKey='A';
  for(const ch of alphabet){
    if(!opts.hasOwnProperty(ch)){nextKey=ch;break;}
  }
  opts[nextKey]='';
  qData[idx].options=opts;
  markQDirty();
  renderQEditor();
}

function removeOpt(idx,key){
  const opts=qData[idx].options;
  if(Object.keys(opts).length<=2){showModal('Minimum 2 options required.');return;}
  delete opts[key];
  // If we removed a correct answer, strip it from the canonical set
  const cs = qGetCorrectSet(qData[idx]);
  if(cs.has(key)){ cs.delete(key); qSetCorrectSet(qData[idx], cs); }
  markQDirty();
  renderQEditor();
}

async function deleteQ(idx){
  if(!(await appConfirm(`Delete question ${idx+1}?`, 'Delete question', {okText:'Delete'}))) return;
  qData.splice(idx,1);
  // Re-number IDs
  qData.forEach((q,i)=>q.id=i+1);
  markQDirty();
  renderQEditor();
}

function moveQ(idx,dir){
  const newIdx=idx+dir;
  if(newIdx<0||newIdx>=qData.length) return;
  [qData[idx],qData[newIdx]]=[qData[newIdx],qData[idx]];
  qData.forEach((q,i)=>q.id=i+1);
  markQDirty();
  renderQEditor();
}

function addQuestion(){
  const nextId=qData.length+1;
  qData.push({
    id:nextId,
    question:'',
    options:{A:'',B:'',C:'',D:''},
    correct:'A',
    question_type:'mcq_single',
    image_url:''
  });
  markQDirty();
  renderQEditor();
  // Scroll to new question
  setTimeout(()=>{
    const card=document.getElementById(`qcard-${qData.length-1}`);
    if(card) card.scrollIntoView({behavior:'smooth',block:'center'});
  },100);
}

function togglePreview(){
  qPreviewMode=!qPreviewMode;
  if(qPreviewMode) renderQPreview();
  else renderQEditor();
}

function renderQPreview(){
  document.getElementById('q-editor').style.display='none';
  const wrap=document.getElementById('q-preview');
  wrap.style.display='block';
  const title=document.getElementById('q-title').value||'Exam';
  document.getElementById('preview-title').textContent=title+' — Preview';
  const body=document.getElementById('preview-body');
  if(!qData.length){
    body.innerHTML='<p style="color:var(--muted)">No questions to preview.</p>';
    return;
  }
  body.innerHTML=qData.map((q,i)=>{
    const qtype=qNormaliseType(q);
    const optKeys=Object.keys(q.options||{});
    const correctSet=qGetCorrectSet(q);
    if(qtype==='short_answer'){
      return `<div class="preview-q">
        <div class="pq-num">Question ${i+1} of ${qData.length} · ${escAttr(qTypeLabel(qtype))}</div>
        ${q.image_url?`<img class="pq-image" id="pqimg-${i}" alt="Q${i+1}">`:''}
        <div class="pq-text">${escAttr(q.question||'(empty)')}</div>
        <div style="border:1px dashed var(--border);border-radius:8px;padding:10px 12px;margin-top:8px;color:var(--muted);font-size:12px">
          Student types a free-text answer here. Max score: ${escAttr(String(q.max_score||1))}
        </div>
        <div style="margin-top:8px;font-size:12px"><strong>Reference:</strong> ${escAttr(q.reference_answer||'(none)')}</div>
        ${q.rubric?`<div style="margin-top:4px;font-size:12px;color:var(--muted)"><strong>Rubric:</strong> ${escAttr(q.rubric)}</div>`:''}
      </div>`;
    }
    return `<div class="preview-q">
      <div class="pq-num">Question ${i+1} of ${qData.length} · ${escAttr(qTypeLabel(qtype))}</div>
      ${q.image_url?`<img class="pq-image" id="pqimg-${i}" alt="Q${i+1}">`:''}
      <div class="pq-text">${escAttr(q.question||'(empty)')}</div>
      <div class="pq-opts">
        ${optKeys.map(k=>{
          const hit=correctSet.has(k);
          return `
          <div class="pq-opt ${hit?'pq-correct':''}">
            <div class="pq-key">${escAttr(k)}</div>
            <span>${escAttr(q.options[k]||'(empty)')}</span>
            ${hit?'<span style="margin-left:auto;font-size:11px;color:var(--emerald);font-weight:600">CORRECT</span>':''}
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }).join('');
  qData.forEach((q,i)=>{
    if(!q.image_url) return;
    qLoadImgSrc(q.image_url).then(src=>{
      const el=document.getElementById('pqimg-'+i);
      if(el && src) el.src=src;
    });
  });
}

// ── AI Question Lint (pre-publish review) ────────────────────────
// Runs every question on this exam through Groq. Surfaces issues
// inline on each .q-card via the #qlint-N slot. The teacher can
// then decide: edit, delete, or accept the warning. We never
// auto-mutate the question — the LLM might be wrong, especially
// on niche / domain-specific content.
//
// Severity color map matches the rest of the dashboard:
//   high   = error-fg (red)   — students will get this wrong unfairly
//   medium = warn-fg (amber)  — confusing but probably gradable
//   low    = muted             — stylistic
async function lintQuestions(){
  if(!Array.isArray(qData) || !qData.length){
    showModal('No questions to lint. Add some first.');
    return;
  }
  const btn = document.getElementById('btn-lint');
  const msg = document.getElementById('q-save-msg');
  btn.disabled = true;
  btn.textContent = 'Linting…';
  msg.style.color = 'var(--muted)';
  msg.textContent = `Reviewing ${qData.length} question${qData.length===1?'':'s'} (typically 2-5 seconds)…`;

  // Build the payload. Use index-in-array as `idx` so the round-
  // trip results map back cleanly. Trim long question text — the
  // server caps at 1500 chars anyway, no point sending more.
  const payload = {
    questions: qData.map((q, i) => ({
      idx: i,
      question: (q.question || '').slice(0, 1500),
      options: q.options || {},
      correct: String(q.correct || ''),
    })),
  };

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/lint-questions`, {
      method: 'POST', body: JSON.stringify(payload),
    });
    const data = await r.json();
    if(!r.ok){
      msg.style.color = 'var(--red)';
      msg.textContent = data.detail || `Lint failed (${r.status})`;
      return;
    }
    _renderLintResults(data.results || []);
    const total = data.total_issues || 0;
    if(total === 0){
      msg.style.color = 'var(--emerald)';
      msg.textContent = `✓ ${qData.length} question${qData.length===1?'':'s'} reviewed — no issues found.`;
    } else {
      msg.style.color = 'var(--amber)';
      msg.textContent = `Found ${total} issue${total===1?'':'s'} across ${data.results.filter(r => r.issues.length).length} question${data.results.filter(r => r.issues.length).length===1?'':'s'}. See inline highlights.`;
    }
  } catch(e){
    msg.style.color = 'var(--red)';
    msg.textContent = 'Network error — try again.';
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ Lint';
  }
}

// Render lint results onto the existing q-cards. Each issue gets
// its own row inside #qlint-N, color-coded by severity. Calling
// with an empty array clears all slots — used when the teacher
// edits a question and wants the stale lint warnings gone.
function _renderLintResults(results){
  // First, clear every existing slot so re-running lint doesn't
  // stack old warnings on top of new ones.
  document.querySelectorAll('.q-lint-slot').forEach(slot => slot.innerHTML = '');

  results.forEach(r => {
    const slot = document.getElementById('qlint-' + r.idx);
    if(!slot) return;
    if(r.lint_failed){
      slot.innerHTML = `<div style="margin:8px 0;padding:8px 12px;background:var(--surface-2);
        border-left:3px solid var(--text-muted);border-radius:4px;
        font-size:11px;color:var(--text-muted);font-family:var(--font-mono)">
        Lint unavailable for this question (LLM blip — try again).
      </div>`;
      return;
    }
    const issues = r.issues || [];
    if(!issues.length){
      // Add a small green check so teachers see "this one's fine"
      // — without it, an unflagged question after a lint pass looks
      // identical to one that was never reviewed.
      slot.innerHTML = `<div style="margin:8px 0;padding:6px 10px;
        font-size:11px;color:var(--emerald);font-family:var(--font-mono);
        display:flex;align-items:center;gap:6px">
        <span style="font-size:13px">✓</span> Reviewed — no issues
      </div>`;
      return;
    }
    slot.innerHTML = issues.map(iss => {
      const sev = iss.severity || 'medium';
      const colors = {
        high:   {fg: 'var(--sev-error-fg)',   bg: 'var(--sev-error-bg)',   border: 'var(--sev-error-fg)'},
        medium: {fg: 'var(--sev-warn-fg)',    bg: 'var(--sev-warn-bg)',    border: 'var(--sev-warn-fg)'},
        low:    {fg: 'var(--text-muted)',     bg: 'var(--surface-2)',      border: 'var(--text-muted)'},
      }[sev] || {fg: 'var(--text-muted)', bg: 'var(--surface-2)', border: 'var(--text-muted)'};
      return `<div style="margin:8px 0;padding:8px 12px;background:${colors.bg};
        border-left:3px solid ${colors.border};border-radius:4px;font-size:12px;color:var(--text)">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
          <span style="font-family:var(--font-mono);font-size:9px;font-weight:600;
                       letter-spacing:0.06em;color:${colors.fg};
                       background:${colors.bg};padding:1px 6px;border-radius:2px;
                       border:1px solid ${colors.fg}">
            ${esc(iss.type || 'ISSUE')} · ${esc(sev.toUpperCase())}
          </span>
        </div>
        <div style="line-height:1.5">${esc(iss.note || '')}</div>
      </div>`;
    }).join('');
  });
}

async function saveQuestions(){
  // Validate before saving
  const errors=[];
  qData.forEach((q,i)=>{
    const qtype=qNormaliseType(q);
    if(!q.question||!q.question.trim()) errors.push(`Q${i+1}: empty question text`);
    if(qtype==='short_answer'){
      if(!q.reference_answer||!String(q.reference_answer).trim()){
        errors.push(`Q${i+1}: short-answer needs a reference answer (the AI grades against it)`);
      }
      const ms = parseFloat(q.max_score);
      if(!ms || ms<=0) errors.push(`Q${i+1}: max score must be greater than 0`);
      return; // skip the MCQ option/correct validation below
    }
    const optKeys=Object.keys(q.options||{});
    if(optKeys.length<2) errors.push(`Q${i+1}: needs at least 2 options`);
    const emptyOpts=optKeys.filter(k=>!q.options[k]||!q.options[k].trim());
    if(emptyOpts.length) errors.push(`Q${i+1}: empty option(s): ${emptyOpts.join(', ')}`);
    const correctSet=qGetCorrectSet(q);
    if(correctSet.size===0){
      errors.push(`Q${i+1}: no correct answer set`);
    }else{
      for(const k of correctSet){
        if(!q.options.hasOwnProperty(k)){
          errors.push(`Q${i+1}: correct answer '${k}' not in options`);
        }
      }
    }
    if(qtype==='mcq_single' && correctSet.size!==1){
      errors.push(`Q${i+1}: single-choice needs exactly 1 correct answer`);
    }
    if(qtype==='mcq_multi' && correctSet.size<2){
      errors.push(`Q${i+1}: multi-select needs at least 2 correct answers`);
    }
    if(qtype==='true_false'){
      const ok = correctSet.size===1 && (correctSet.has('True')||correctSet.has('False'));
      if(!ok) errors.push(`Q${i+1}: True/False must have 'True' or 'False' as the correct answer`);
    }
  });
  if(!qData.length) errors.push('No questions to save');

  const msgEl=document.getElementById('q-save-msg');
  if(errors.length){
    msgEl.innerHTML=`<span style="color:var(--red)">Validation errors:<br>${errors.map(e=>'&bull; '+escAttr(e)).join('<br>')}</span>`;
    // Highlight first errored card
    const firstIdx=parseInt((errors[0].match(/Q(\d+)/)||[])[1]||'1')-1;
    const card=document.getElementById(`qcard-${firstIdx}`);
    if(card){card.classList.add('q-error');card.scrollIntoView({behavior:'smooth',block:'center'});
      setTimeout(()=>card.classList.remove('q-error'),3000);}
    return;
  }

  // Re-number IDs sequentially
  qData.forEach((q,i)=>q.id=i+1);

  const payload={
    exam_title: document.getElementById('q-title').value.trim() || 'Exam',
    duration_minutes: parseInt(document.getElementById('q-duration').value) || 60,
    questions: qData,
    exam_id: currentExamId
  };

  msgEl.innerHTML='<span class="spinner"></span> Saving...';
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/questions`,{
      method:'POST',body:JSON.stringify(payload)
    });
    if(!r.ok){
      const err=await r.json();
      throw new Error(err.detail||'Save failed');
    }
    const d=await r.json();
    qDirty=false;
    msgEl.innerHTML=`<span style="color:var(--emerald)">Saved ${d.count} questions successfully.</span>`;
    renderQStats();
  if(typeof renderQSidebar==='function') renderQSidebar();
  }catch(e){
    msgEl.innerHTML=`<span style="color:var(--red)">Save failed: ${escAttr(e.message)}</span>`;
  }
}

// ── FORENSICS TIMELINE ──────────────────────────────────────────
let tlData = null;  // current timeline data
let tlFilter = 'all';

const TL_ICONS = {
  face_missing:'&#128100;', multiple_faces:'&#128101;', wrong_person:'&#128680;',
  calibration_abort:'&#9888;&#65039;',
  gaze_away:'&#128064;', head_turned:'&#8617;&#65039;', eyes_closed:'&#128529;',
  cheat_object_detected:'&#128241;', voice_detected:'&#127908;',
  window_focus_lost:'&#129695;', tab_hidden:'&#128209;', shortcut_blocked:'&#9000;&#65039;',
  time_exceeded:'&#9200;', vm_detected:'&#128187;', remote_desktop_detected:'&#128421;&#65039;',
  screen_share_detected:'&#128250;', multiple_monitors:'&#128421;&#65039;',
  exam_started:'&#9654;&#65039;', exam_submitted:'&#9989;', enrollment_started:'&#128247;',
  enrollment_complete:'&#9989;', face_enrolled:'&#128274;', session_ended:'&#127937;',
  answer_selected:'&#128221;', heartbeat:'&#128147;', submit_failed:'&#10060;',
  proctor_camera_failed:'&#128247;',
  phone_consulting:'&#128242;', collaboration:'&#128101;&#8205;&#128172;',
  answer_memo:'&#129504;', note_reading:'&#128214;',
  sustained_offtask:'&#9203;', nervous_evasion:'&#128064;&#65039;',
};

function openTimeline(){
  if(!currentSessionId) return;
  closeModal();
  const m=document.getElementById('timeline-modal');
  m.classList.add('open');
  document.getElementById('tl-title').textContent='Loading...';
  document.getElementById('tl-meta').innerHTML='';
  document.getElementById('tl-events').innerHTML='<div class="tl-empty"><span class="spinner"></span> Loading timeline...</div>';
  document.getElementById('tl-scrubber-track').innerHTML='';
  document.getElementById('tl-scrubber-labels').innerHTML='';
  loadTimeline(currentSessionId);
}

async function loadTimeline(sid){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/timeline/${encodeURIComponent(sid)}`);
    if(!r.ok){
      // Surface the actual server error so we can debug "session not found"
      // vs auth failures vs 500s instead of a generic "Failed to load".
      let msg=`HTTP ${r.status}`;
      try{
        const body=await r.json();
        if(body && body.detail) msg=`${r.status}: ${body.detail}`;
      }catch(_){}
      throw new Error(msg);
    }
    tlData=await r.json();
    tlFilter='all';
    // Reset filter buttons
    document.querySelectorAll('.tl-filter-btn').forEach(b=>{
      b.classList.toggle('active',b.dataset.sev==='all');
    });
    renderTimelineSummary(tlData.summary||null);
    renderTimeline();
  }catch(e){
    document.getElementById('tl-events').innerHTML=`<div class="tl-empty" style="color:var(--red)">Failed to load timeline: ${_escHtml(e.message)}</div>`;
  }
}

function renderTimeline(){
  if(!tlData) return;
  const d=tlData;

  // Title & meta
  document.getElementById('tl-title').textContent=`${d.full_name||'Unknown'} — ${d.roll_number}`;
  document.getElementById('tl-meta').innerHTML=`
    <span>Status: <strong>${d.status}</strong></span>
    <span>Started: <strong>${d.started_at||'—'}</strong></span>
    <span>Submitted: <strong>${d.submitted_at||'—'}</strong></span>
    <span>Score: <strong>${d.score!=null?d.score+'/'+d.total:'—'}</strong></span>
    <span>Risk: <strong>${d.risk_score!=null?d.risk_score+'/100':'—'}</strong></span>
    <span>Events: <strong>${d.total_events}</strong></span>
  `;

  // Filter events
  const events=d.timeline.filter(e=>{
    if(tlFilter==='all') return true;
    if(tlFilter==='violations') return e.is_violation;
    return e.severity===tlFilter;
  });

  // Scrubber — parse timestamps to build the bar
  const allTs=d.timeline.map(e=>parseRawTs(e.raw_ts)).filter(t=>t>0);
  const minTs=Math.min(...allTs), maxTs=Math.max(...allTs);
  const range=maxTs-minTs||1;

  const track=document.getElementById('tl-scrubber-track');
  track.innerHTML='';
  events.forEach((e,i)=>{
    const ts=parseRawTs(e.raw_ts);
    if(ts<=0) return;
    const pct=((ts-minTs)/range)*100;
    const dot=document.createElement('div');
    dot.className=`tl-dot sev-${e.severity}${e.screenshot?' has-screenshot':''}`;
    dot.style.left=pct+'%';
    dot.title=`${e.type.replace(/_/g,' ')} (${e.severity})`;
    dot.onclick=(ev)=>{ev.stopPropagation();scrollToEvent(i);};
    track.appendChild(dot);
  });

  // Scrubber labels
  const labels=document.getElementById('tl-scrubber-labels');
  if(allTs.length>=2){
    const startTime=new Date(minTs*1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:true});
    const endTime=new Date(maxTs*1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:true});
    const durMins=Math.round(range/60);
    labels.innerHTML=`<span>${startTime}</span><span>${durMins} min</span><span>${endTime}</span>`;
  } else {
    labels.innerHTML='';
  }

  // Event list
  const el=document.getElementById('tl-events');
  if(!events.length){
    el.innerHTML='<div class="tl-empty">No events match the current filter.</div>';
    return;
  }
  // Render events and then lazy-load thumbnails
  el.innerHTML=events.map((e,i)=>{
    const icon=TL_ICONS[e.type]||'&#128204;';
    const icCls=`ic-${e.severity}`;
    const timeStr=extractTime(e.timestamp);
    const thumbHtml=e.screenshot
      ?`<img class="tl-thumb" data-src="${escAttr(e.screenshot)}" data-action="_showLightbox" data-args='${_jsonArgsForAttr(e.screenshot,e.type,timeStr)}' data-error-action="_hideSelf">`
      :'';
    return `<div class="tl-event sev-${e.severity}${e.is_violation?' is-violation':''}" id="tl-evt-${i}">
      <div class="tl-time">${timeStr}</div>
      <div class="tl-icon ${icCls}">${icon}</div>
      <div class="tl-body">
        <div class="tl-type">${e.type.replace(/_/g,' ')}<span style="margin-left:8px;font-size:11px;font-weight:400;color:${e.severity==='high'?'var(--red)':e.severity==='medium'?'var(--amber)':'var(--muted)'}">${e.severity.toUpperCase()}</span></div>
        ${e.details?`<div class="tl-detail">${esc(e.details)}</div>`:''}
      </div>
      ${thumbHtml}
    </div>`;
  }).join('');

  // Lazy-load thumbnails with auth headers
  el.querySelectorAll('.tl-thumb[data-src]').forEach(img=>{
    authFetch(img.dataset.src).then(r=>{
      if(!r.ok) throw new Error();
      return r.blob();
    }).then(b=>{
      img.src=URL.createObjectURL(b);
    }).catch(()=>{img.style.display='none';});
  });
}

function parseRawTs(raw){
  if(!raw) return 0;
  try{return new Date(raw.replace(' ','T').replace('Z','+00:00')).getTime()/1000;}catch(e){return 0;}
}

function extractTime(formatted){
  // formatted is like "05 Apr 2026, 02:30:22 PM IST" — extract time part
  if(!formatted) return '--:--';
  const m=formatted.match(/(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)/i);
  return m ? m[1] : '--:--';
}

function filterTimeline(){
  const checked=document.querySelector('input[name="tl-sev"]:checked');
  tlFilter=checked?checked.value:'all';
  document.querySelectorAll('.tl-filter-btn').forEach(b=>{
    b.classList.toggle('active',b.dataset.sev===tlFilter);
  });
  renderTimeline();
}

function renderTimelineSummary(summary){
  const container=document.getElementById('tl-summary');
  const highlights = Array.isArray(summary && summary.highlights) ? summary.highlights : [];
  if(!summary || !highlights.length){
    container.style.display='none';
    return;
  }
  container.style.display='';
  const sevColors={
    clean:'var(--emerald)',
    minor:'var(--amber)',
    concerning:'var(--red)',
    critical:'var(--red)',
  };
  const sevBg={
    clean:'rgba(16,185,129,0.15)',
    minor:'rgba(245,158,11,0.15)',
    concerning:'rgba(220,38,38,0.15)',
    critical:'rgba(220,38,38,0.25)',
  };
  const sev=summary.severity||'clean';
  const sevEl=document.getElementById('tl-summary-severity');
  sevEl.textContent=sev.charAt(0).toUpperCase()+sev.slice(1);
  sevEl.style.background=sevBg[sev]||sevBg.clean;
  sevEl.style.color=sevColors[sev]||sevColors.clean;
  document.getElementById('tl-summary-text').textContent=summary.narrative||'';
  document.getElementById('tl-summary-highlights').innerHTML=
    highlights.map(h=>`<div style="padding:4px 0 4px 20px;font-size:12px;position:relative">
      <span style="position:absolute;left:0;color:${sevColors[sev]||sevColors.clean}">●</span>
      ${_escHtml(h)}
    </div>`).join('');
}

function scrollToEvent(idx){
  const el=document.getElementById(`tl-evt-${idx}`);
  if(!el) return;
  // Remove previous highlight
  document.querySelectorAll('.tl-event.highlighted').forEach(e=>e.classList.remove('highlighted'));
  el.classList.add('highlighted');
  el.scrollIntoView({behavior:'smooth',block:'center'});
  setTimeout(()=>el.classList.remove('highlighted'),2500);
}

function showLightbox(src,caption){
  const img=document.getElementById('tl-lightbox-img');
  // Load image with admin auth via fetch+blob
  authFetch(src).then(r=>r.blob()).then(b=>{
    img.src=URL.createObjectURL(b);
    document.getElementById('tl-lightbox-caption').textContent=caption;
    document.getElementById('tl-lightbox').classList.add('open');
  }).catch(()=>{});
}

function closeLightbox(){
  const lb=document.getElementById('tl-lightbox');
  lb.classList.remove('open');
  const img=document.getElementById('tl-lightbox-img');
  if(img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
  img.src='';
}

function closeTimeline(){
  document.getElementById('timeline-modal').classList.remove('open');
  tlData=null;
}

// ── KEYBOARD SHORTCUTS ──────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(document.getElementById('tl-lightbox').classList.contains('open')) closeLightbox();
    else if(document.getElementById('timeline-modal').classList.contains('open')) closeTimeline();
    else if(document.getElementById('broadcast-modal').classList.contains('open')) closeBroadcastModal();
    else if(!document.getElementById('triage-modal').classList.contains('hidden')) closeTriage();
    else if(!document.getElementById('grade-modal').classList.contains('hidden')) closeGradeReview();
    else if(!document.getElementById('liveview-modal').classList.contains('hidden')) closeLiveView();
    else if(document.getElementById('detail-modal').classList.contains('open')) closeModal();
    else if(!document.getElementById('onboard-modal').classList.contains('hidden')) onboardSkip();
  }
});

// ── IN-EXAM CHAT (teacher side) ─────────────────────────────────
let chatWs = null;
let chatReconnectTimer = null;
let chatReconnectDelay = 1000;
let chatSessions = {};          // session_id -> {roll, name, online, messages, unread}
let chatActiveSid = null;
let chatIntentionalClose = false;
const CHAT_TAB_ORIG_TITLE = document.title;
let chatTitleFlashTimer = null;

// Tiny built-in beep so we don't need to ship an audio file.
const CHAT_BEEP_DATA = (()=>{
  // 16-bit PCM WAV, 440Hz, 120ms, mono, 8kHz.  Generated inline.
  const sr=8000, dur=0.12, freq=440;
  const n=Math.floor(sr*dur);
  const buf=new ArrayBuffer(44+n*2);
  const v=new DataView(buf);
  const wstr=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i));};
  wstr(0,'RIFF'); v.setUint32(4,36+n*2,true); wstr(8,'WAVE');
  wstr(12,'fmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
  v.setUint16(22,1,true); v.setUint32(24,sr,true); v.setUint32(28,sr*2,true);
  v.setUint16(32,2,true); v.setUint16(34,16,true);
  wstr(36,'data'); v.setUint32(40,n*2,true);
  for(let i=0;i<n;i++){
    const t=i/sr;
    const env=Math.min(1,t*20)*Math.min(1,(dur-t)*20);
    const s=Math.sin(2*Math.PI*freq*t)*env*0.35;
    v.setInt16(44+i*2, Math.max(-1,Math.min(1,s))*0x7fff, true);
  }
  let bin='';
  const b=new Uint8Array(buf);
  for(let i=0;i<b.length;i++) bin+=String.fromCharCode(b[i]);
  return 'data:audio/wav;base64,'+btoa(bin);
})();
const chatBeep = new Audio(CHAT_BEEP_DATA);
chatBeep.volume = 0.5;

function chatWsUrl(){
  const proto = (location.protocol==='https:')?'wss:':'ws:';
  const host = (BASE.replace(/^https?:\/\//,'').replace(/\/$/,'')) || location.host;
  return `${proto}//${host}/ws/chat/teacher`;
}

function chatConnect(){
  if(chatWs && (chatWs.readyState===0||chatWs.readyState===1)) return;
  chatIntentionalClose = false;
  try{
    chatWs = authToken ? new WebSocket(chatWsUrl(), [authToken]) : new WebSocket(chatWsUrl());
  }catch(e){
    console.warn('chat ws ctor failed',e);
    chatScheduleReconnect();
    return;
  }
  chatWs.onopen = ()=>{
    chatReconnectDelay = 1000;
    // Legacy Bearer fallback. Modern dashboard sessions authenticate
    // the WebSocket handshake with the HttpOnly admin_access cookie.
    if(authToken) chatWs.send(JSON.stringify({type:'auth', token: authToken}));
    document.getElementById('chat-roster-sub').textContent = 'Connected';
  };
  chatWs.onmessage = (ev)=>{
    let data; try{ data = JSON.parse(ev.data); }catch(_){ return; }
    chatHandleIncoming(data);
  };
  chatWs.onclose = (ev)=>{
    document.getElementById('chat-roster-sub').textContent = 'Disconnected';
    if(chatIntentionalClose) return;
    if(ev.code===4401){
      // Token expired — refresh and reconnect
      _refreshTokens().then(()=>chatConnect()).catch(()=>doLogout());
      return;
    }
    chatScheduleReconnect();
  };
  chatWs.onerror = ()=>{ /* onclose will follow */ };
}

function chatDisconnect(){
  chatIntentionalClose = true;
  if(chatReconnectTimer){ clearTimeout(chatReconnectTimer); chatReconnectTimer=null; }
  if(chatWs){ try{ chatWs.close(); }catch(_){ } chatWs=null; }
  chatSessions = {};
  chatActiveSid = null;
  chatRenderRoster();
  chatRenderThread();
  chatClearTabBadge();
  document.getElementById('chat-roster-sub').textContent = 'Not connected';
}

function chatScheduleReconnect(){
  if(chatReconnectTimer) return;
  const d = Math.min(chatReconnectDelay, 15000);
  chatReconnectTimer = setTimeout(()=>{
    chatReconnectTimer = null;
    chatReconnectDelay = Math.min(chatReconnectDelay*2, 15000);
    chatConnect();
  }, d);
}

function chatEnsureSession(sid, meta){
  if(!chatSessions[sid]){
    chatSessions[sid] = {
      roll: (meta&&meta.roll)||'',
      name: (meta&&meta.name)||sid,
      online: (meta&&meta.online)||false,
      messages: [],
      unread: 0,
    };
  }else if(meta){
    if(meta.roll) chatSessions[sid].roll = meta.roll;
    if(meta.name) chatSessions[sid].name = meta.name;
    if(typeof meta.online==='boolean') chatSessions[sid].online = meta.online;
  }
  return chatSessions[sid];
}

function chatHandleIncoming(data){
  const t = data.type;
  if(t==='roster'){
    chatSessions = {};
    (data.sessions||[]).forEach(s=>{
      const sess = chatEnsureSession(s.session_id, s);
      sess.messages = (s.history||[]).slice();
    });
    chatRenderRoster();
    if(chatActiveSid && chatSessions[chatActiveSid]) chatRenderThread();
    return;
  }
  if(t==='presence'){
    const sess = chatEnsureSession(data.session_id, data);
    sess.online = !!data.online;
    if(!data.online && !sess.messages.length){
      // Student dropped before any message — remove entirely
      delete chatSessions[data.session_id];
      if(chatActiveSid===data.session_id){ chatActiveSid=null; chatRenderThread(); }
    }
    chatRenderRoster();
    return;
  }
  if(t==='msg' || t==='broadcast'){
    const sid = data.session_id;
    if(!sid) return;
    const sess = chatEnsureSession(sid, {
      roll: data.roll, name: data.name,
    });
    sess.messages.push(data);
    const isBroadcast = (t==='broadcast');
    const fromStudent = (data.sender==='student');
    if(fromStudent){
      const chatTabActive =
        document.querySelector('.tab.active')?.dataset.tab==='chat';
      if(!chatTabActive || chatActiveSid!==sid){
        sess.unread = (sess.unread||0)+1;
        chatBumpTabBadge();
        chatNotify();
      }
    }
    chatRenderRoster();
    if(chatActiveSid===sid){
      chatRenderThread();
      chatScrollToBottom();
    }
    return;
  }
}

function chatRenderRoster(){
  const body = document.getElementById('chat-roster-body');
  const entries = Object.entries(chatSessions);
  // Online first, then recently-active
  entries.sort((a,b)=>{
    if(a[1].online!==b[1].online) return a[1].online?-1:1;
    const ta = a[1].messages.length ? a[1].messages[a[1].messages.length-1].ts : '';
    const tb = b[1].messages.length ? b[1].messages[b[1].messages.length-1].ts : '';
    return tb.localeCompare(ta);
  });
  if(!entries.length){
    body.innerHTML = '<div class="chat-empty">No students online yet.</div>';
    return;
  }
  body.innerHTML = entries.map(([sid,s])=>{
    const active = (sid===chatActiveSid) ? ' active' : '';
    const dotCls = s.online ? '' : ' offline';
    const unread = (s.unread||0) > 0 ? `<span class="unread">${s.unread}</span>` : '';
    const safeName = chatEscape(s.name || sid);
    const safeRoll = chatEscape(s.roll || '');
    return `<div class="chat-row${active}" data-action="chatSelect" data-args='${_jsonArgsForAttr(sid)}'>
      <span class="dot${dotCls}"></span>
      <div class="meta">
        <div class="name">${safeName}</div>
        <div class="roll">${safeRoll}${s.online?'':' · offline'}</div>
      </div>
      ${unread}
    </div>`;
  }).join('');
}

function chatSelect(sid){
  chatActiveSid = sid;
  const sess = chatSessions[sid];
  if(sess){ sess.unread = 0; }
  chatRecomputeTabBadge();
  chatRenderRoster();
  chatRenderThread();
  chatScrollToBottom();
  const input = document.getElementById('chat-input');
  input.disabled = false;
  document.getElementById('chat-send').disabled = false;
  input.focus();
}

function chatRenderThread(){
  const head = document.getElementById('chat-thread-head');
  const body = document.getElementById('chat-thread-body');
  if(!chatActiveSid || !chatSessions[chatActiveSid]){
    head.innerHTML = '<div class="chat-thread-title">Select a student</div>'
      +'<div class="chat-thread-sub">Messages are ephemeral — nothing is stored after the exam ends.</div>';
    body.innerHTML = '<div class="chat-empty-lg">Pick a student on the left to start chatting.</div>';
    document.getElementById('chat-input').disabled = true;
    document.getElementById('chat-send').disabled = true;
    return;
  }
  const sess = chatSessions[chatActiveSid];
  const onlineTxt = sess.online ? '<span style="color:var(--emerald)">● online</span>' : '<span style="color:var(--muted)">○ offline</span>';
  head.innerHTML = `<div class="chat-thread-title">${chatEscape(sess.name||chatActiveSid)} <span style="color:var(--muted);font-weight:400;font-size:12px">· ${chatEscape(sess.roll||'')}</span></div>
    <div class="chat-thread-sub">${onlineTxt} · Session ${chatEscape(chatActiveSid)}</div>`;
  if(!sess.messages.length){
    body.innerHTML = '<div class="chat-empty-lg">No messages yet. Say hi.</div>';
    return;
  }
  body.innerHTML = sess.messages.map(m=>{
    const isB = m.type==='broadcast';
    const isT = m.sender==='teacher';
    const cls = isB ? 'from-broadcast' : (isT ? 'from-teacher' : 'from-student');
    const ts = chatFmtTime(m.ts);
    const label = isB ? 'Broadcast · ' : '';
    return `<div class="chat-msg ${cls}">${chatEscape(m.text)}<span class="ts">${label}${ts}</span></div>`;
  }).join('');
}

function chatScrollToBottom(){
  const body = document.getElementById('chat-thread-body');
  setTimeout(()=>{ body.scrollTop = body.scrollHeight; }, 0);
}

function sendTeacherMsg(ev){
  if(ev) ev.preventDefault();
  const input = document.getElementById('chat-input');
  const text = (input.value||'').trim();
  if(!text || !chatActiveSid || !chatWs || chatWs.readyState!==1) return false;
  try{
    chatWs.send(JSON.stringify({type:'msg', session_id:chatActiveSid, text}));
    input.value='';
  }catch(e){ console.warn('chat send failed',e); }
  return false;
}

document.addEventListener('DOMContentLoaded',()=>{
  const input = document.getElementById('chat-input');
  if(input){
    input.addEventListener('keydown',e=>{
      if(e.key==='Enter' && !e.shiftKey){
        e.preventDefault();
        sendTeacherMsg();
      }
    });
  }
});
// If DOMContentLoaded already fired, wire it up anyway
setTimeout(()=>{
  const input = document.getElementById('chat-input');
  if(input && !input.__chatBound){
    input.__chatBound = true;
    input.addEventListener('keydown',e=>{
      if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendTeacherMsg(); }
    });
  }
},0);

// ── Broadcast modal ─────────────────────────────────────────────
function openBroadcastModal(){
  document.getElementById('broadcast-text').value='';
  document.getElementById('broadcast-status').textContent='';
  document.getElementById('broadcast-modal').classList.add('open');
}
function closeBroadcastModal(){
  document.getElementById('broadcast-modal').classList.remove('open');
}
function sendBroadcast(){
  const ta = document.getElementById('broadcast-text');
  const text = (ta.value||'').trim();
  const status = document.getElementById('broadcast-status');
  if(!text){ status.style.color='var(--red)'; status.textContent='Message is empty'; return; }
  if(!chatWs || chatWs.readyState!==1){
    status.style.color='var(--red)'; status.textContent='Chat not connected';
    return;
  }
  try{
    chatWs.send(JSON.stringify({type:'broadcast', text}));
    status.style.color='var(--emerald)';
    status.textContent='Broadcast sent.';
    ta.value='';
    setTimeout(closeBroadcastModal, 700);
  }catch(e){
    status.style.color='var(--red)'; status.textContent='Send failed';
  }
}

// ── Badge / notification helpers ────────────────────────────────
function chatRecomputeTabBadge(){
  let n=0;
  for(const sid in chatSessions) n += (chatSessions[sid].unread||0);
  const el = document.getElementById('chat-tab-badge');
  if(n>0){ el.textContent = n>99?'99+':n; el.style.display=''; }
  else   { el.textContent = '0'; el.style.display='none'; }
}
function chatBumpTabBadge(){ chatRecomputeTabBadge(); }
function chatClearTabBadge(){
  for(const sid in chatSessions) chatSessions[sid].unread = 0;
  chatRecomputeTabBadge();
  if(chatTitleFlashTimer){ clearInterval(chatTitleFlashTimer); chatTitleFlashTimer=null; }
  document.title = CHAT_TAB_ORIG_TITLE;
}
function chatClearActiveUnread(){
  if(chatActiveSid && chatSessions[chatActiveSid]){
    chatSessions[chatActiveSid].unread = 0;
    chatRecomputeTabBadge();
    chatRenderRoster();
  }
}
function chatNotify(){
  try{ chatBeep.currentTime = 0; chatBeep.play().catch(()=>{}); }catch(_){}
  // Title flash if the tab/window isn't focused
  if(document.hidden || document.visibilityState==='hidden'){
    if(chatTitleFlashTimer) return;
    let on=false;
    chatTitleFlashTimer = setInterval(()=>{
      on=!on;
      document.title = on ? '● new message — Procta' : CHAT_TAB_ORIG_TITLE;
    }, 900);
  }
}
window.addEventListener('focus', ()=>{
  if(chatTitleFlashTimer){ clearInterval(chatTitleFlashTimer); chatTitleFlashTimer=null; }
  document.title = CHAT_TAB_ORIG_TITLE;
});

// ── Safe escape helpers ─────────────────────────────────────────
function chatFmtTime(ts){
  if(!ts) return '';
  try{ return new Date(ts).toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',hour12:true}); }
  catch(_){ return ''; }
}

// ── QUESTION BANK ──────────────────────────────────────────────
let _bankData = [];
let _bankSelected = new Set();

// Toggle the right-side AI/Bank panel via the .collapsed class (Phase 2.8
// 3-column shell). Falls back gracefully on legacy markup that may use
// inline display:none — both styles end up consistent after the toggle.
function toggleBank(){
  const panel = document.getElementById('q-bank-panel');
  if(!panel) return;
  const isCollapsed = panel.classList.contains('collapsed');
  if(isCollapsed){
    panel.classList.remove('collapsed');
    panel.style.display = '';   // belt + suspenders for legacy CSS rules
    loadBank();
  } else {
    panel.classList.add('collapsed');
    panel.style.display = 'none';
  }
}

// AI panel tab switcher — flips the [data-aipane] visible block
// inside the right column. Three panes: generate / bank / import.
function setAITab(name){
  const panel = document.getElementById('q-bank-panel');
  if(!panel) return;
  // If the panel is closed, opening it via tab click should reveal it.
  if(panel.classList.contains('collapsed')) toggleBank();
  panel.querySelectorAll('.q-aipanel-tab').forEach(t => {
    const on = t.dataset.aitab === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  panel.querySelectorAll('[data-aipane]').forEach(p => {
    p.style.display = (p.dataset.aipane === name) ? '' : 'none';
  });
  if(name === 'bank') loadBank();
}

// Sidebar question-list filter state — module-level so the sidebar
// can re-render itself on search input + chip click without
// disturbing the editor (#q-editor) which renders all questions.
let _qListFilter = 'all';   // 'all' | 'mcq_single' | 'mcq_multi' | 'true_false' | 'img'

function setQTypeFilter(name){
  _qListFilter = name;
  document.querySelectorAll('.q-sidebar-filter .filter-chip').forEach(c => {
    c.classList.toggle('active', c.dataset.qtypef === name);
  });
  renderQSidebar();
}

// Render the left sidebar question list. Filters by:
//   • _qListFilter chip (question type or "has image")
//   • #q-list-search text match against question body
// Clicking an item scrolls the matching #qcard-N into view in the
// center column. Highlights the active item via .active class.
function renderQSidebar(){
  const wrap = document.getElementById('q-list-wrap');
  const countEl = document.getElementById('q-list-count');
  if(!wrap) return;
  if(countEl) countEl.textContent = qData.length;
  if(!qData.length){
    wrap.innerHTML = '<div class="q-list-empty">No questions yet — click "Add" in the toolbar.</div>';
    return;
  }
  const searchEl = document.getElementById('q-list-search');
  const term = searchEl ? (searchEl.value||'').trim().toLowerCase() : '';
  const filtered = qData.map((q,i) => ({...q, _idx:i})).filter(q => {
    if(_qListFilter === 'img'){ if(!q.image_url) return false; }
    else if(_qListFilter !== 'all'){
      if((q.question_type||'mcq_single') !== _qListFilter) return false;
    }
    if(term && !(q.question||'').toLowerCase().includes(term)) return false;
    return true;
  });
  if(!filtered.length){
    wrap.innerHTML = '<div class="q-list-empty">No matching questions.</div>';
    return;
  }
  wrap.innerHTML = filtered.map(q => {
    const preview = (q.question||'(empty)').slice(0,80);
    return `<div class="q-list-item" data-action="qFocusCard" data-args='${_jsonArgsForAttr(q._idx)}' data-qidx="${q._idx}">
      <span class="q-list-num">${q._idx+1}</span>
      <span class="q-list-preview">${esc(preview)}</span>
    </div>`;
  }).join('');
}

// Scroll the matching #qcard-i into view + apply .active highlight in
// the sidebar. Used by sidebar item clicks.
function qFocusCard(idx){
  const card = document.getElementById('qcard-'+idx);
  if(card) card.scrollIntoView({behavior:'smooth', block:'start'});
  document.querySelectorAll('.q-list-item').forEach(el => {
    el.classList.toggle('active', String(el.dataset.qidx) === String(idx));
  });
}

// Expand / collapse all q-cards. The legacy q-card has no collapse
// state today — every card is fully open. We implement collapse by
// toggling display on every child of .q-card except .q-card-hdr,
// which is the sticky always-visible row. Idempotent across re-renders
// because the JS that emits q-cards re-runs after every loadQuestions.
function qExpandAll(){
  document.querySelectorAll('#q-editor .q-card').forEach(card => {
    Array.from(card.children).forEach(ch => {
      if(!ch.classList.contains('q-card-hdr')) ch.style.display = '';
    });
    card.dataset.collapsed = '0';
  });
}
function qCollapseAll(){
  document.querySelectorAll('#q-editor .q-card').forEach(card => {
    Array.from(card.children).forEach(ch => {
      if(!ch.classList.contains('q-card-hdr')) ch.style.display = 'none';
    });
    card.dataset.collapsed = '1';
  });
}

async function loadBank(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank`);
    if(!r.ok) return;
    _bankData = await r.json();
    _bankSelected.clear();
    renderBank(_bankData);
  }catch(e){ console.error('loadBank:', e); }
}

function renderBank(data){
  const list = document.getElementById('bank-list');
  const empty = document.getElementById('bank-empty');
  if(!data.length){ list.innerHTML=''; empty.style.display=''; return; }
  empty.style.display='none';
  // Select-all header — checks/unchecks every visible row in one click.
  // Computed state: indeterminate when SOME but not ALL are selected.
  const allSelected = data.length > 0 && data.every(q => _bankSelected.has(q.id));
  const someSelected = data.some(q => _bankSelected.has(q.id));
  const selectAllHeader = `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;
                background:var(--surface-2);border-bottom:1px solid var(--border);
                position:sticky;top:0;z-index:1">
      <input type="checkbox" id="bank-select-all" data-change-action="_bankSelectAllWrap"
             ${allSelected ? 'checked' : ''}
             style="margin:0;flex-shrink:0">
      <label for="bank-select-all" style="font-size:11px;color:var(--text-mid);cursor:pointer;flex:1">
        ${allSelected ? 'All selected' : someSelected ? 'Some selected' : 'Select all'}
      </label>
      <span id="bank-selected-count" style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">
        ${_bankSelected.size} / ${data.length}
      </span>
    </div>`;
  list.innerHTML = selectAllHeader + data.map((q,i)=>{
    const opts = q.options||{};
    const optStr = Object.entries(opts).map(([k,v])=>`<span style="margin-right:8px"><b>${_escHtml(k)}.</b> ${_escHtml(String(v).substring(0,40))}</span>`).join('');
    const tags = (q.tags||[]).map(t=>`<span style="background:rgba(61,217,168,.1);color:var(--accent-light);border-radius:10px;padding:1px 8px;font-size:10px;margin-right:4px">${_escHtml(t)}</span>`).join('');
    const checked = _bankSelected.has(q.id) ? 'checked' : '';
    return `<div style="display:flex;gap:10px;align-items:flex-start;padding:10px;border-bottom:1px solid var(--border);${i%2===0?'background:rgba(255,255,255,.01)':''}">
      <input type="checkbox" ${checked} data-change-action="_bankToggleWrap" data-qid='${escAttr(q.id)}' style="margin-top:4px;flex-shrink:0">
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;color:var(--text);margin-bottom:4px;max-height:120px;overflow-y:auto">${_escHtml(q.question)}</div>
        <div style="font-size:11px;color:var(--muted)">${optStr}</div>
        <div style="margin-top:4px;display:flex;align-items:center;gap:6px">
          <span style="font-size:11px;color:var(--emerald);font-weight:600">Correct: ${_escHtml(q.correct)}</span>
          <span style="font-size:10px;color:var(--muted)">${_escHtml(q.question_type)}</span>
          ${tags}
        </div>
      </div>
      <div style="display:flex;gap:4px;flex-shrink:0">
        <button class="btn btn-secondary btn-sm" data-action="saveBankToExamSingle" data-args='${_jsonArgsForAttr(q.id)}' style="padding:2px 8px;font-size:10px" title="Add to current exam">+Exam</button>
        <button class="btn btn-secondary btn-sm" data-action="editBankQ" data-args='${_jsonArgsForAttr(q.id)}' style="padding:2px 8px;font-size:10px" title="Edit question">Edit</button>
        <button class="btn btn-secondary btn-sm" data-action="deleteBankQ" data-args='${_jsonArgsForAttr(q.id)}' style="padding:2px 8px;font-size:10px;color:var(--red)" title="Delete">&times;</button>
      </div>
    </div>`;
  }).join('');
}

function _bankToggle(id, checked){
  if(checked) _bankSelected.add(id);
  else _bankSelected.delete(id);
  // Refresh select-all header so the indeterminate / count text stays accurate
  // without re-rendering every row (cheaper + preserves scroll position).
  const countEl = document.getElementById('bank-selected-count');
  if(countEl) countEl.textContent = `${_bankSelected.size} / ${(_bankData||[]).length}`;
  const allEl = document.getElementById('bank-select-all');
  if(allEl){
    const data = _bankData || [];
    allEl.checked = data.length > 0 && data.every(q => _bankSelected.has(q.id));
    allEl.indeterminate = !allEl.checked && _bankSelected.size > 0;
  }
}

// Select-all checkbox handler.
function _bankSelectAll(check){
  const data = _bankData || [];
  if(check) data.forEach(q => _bankSelected.add(q.id));
  else _bankSelected.clear();
  renderBank(data);
}

function filterBank(){
  const term = (document.getElementById('bank-search').value||'').toLowerCase();
  if(!term){ renderBank(_bankData); return; }
  const filtered = _bankData.filter(q=>
    q.question.toLowerCase().includes(term) ||
    (q.tags||[]).some(t=>t.toLowerCase().includes(term))
  );
  renderBank(filtered);
}

async function deleteBankQ(qid){
  await authFetch(`${BASE}/api/v1/admin/question-bank/${qid}`,{method:'DELETE'});
  _bankData = _bankData.filter(q=>q.id!==qid);
  _bankSelected.delete(qid);
  renderBank(_bankData);
}

async function bankToExam(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const ids = Array.from(_bankSelected);
  if(!ids.length){ showModal('Select questions using the checkboxes first.'); return; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/to-exam`,
      {method:'POST',body:JSON.stringify({question_ids:ids,exam_id:eid})});
    const d = await r.json();
    if(r.ok){
      showModal(`Added ${d.added} questions to exam.`);
      _bankSelected.clear();
      loadQuestions();
      loadBank();
    }else{ showModal(d.detail||'Error'); }
  }catch(e){ showModal('Failed to add questions'); }
}

async function saveBankToExamSingle(qid){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const r = await authFetch(`${BASE}/api/v1/admin/question-bank/to-exam`,
    {method:'POST',body:JSON.stringify({question_ids:[qid],exam_id:eid})});
  if(r.ok){ loadQuestions(); }
  else{ const d=await r.json(); showModal(d.detail||'Error'); }
}

function showBankImport(){
  document.getElementById('bank-import-area').style.display='';
}

// ── AI Question Generation ──────────────────────────────────────
let _genPreview = [];

function showBankGenerate(){
  document.getElementById('bank-generate-area').style.display='';
  document.getElementById('gen-topic').focus();
}

async function doGenerateQuestions(){
  const topic = document.getElementById('gen-topic').value.trim();
  const count = parseInt(document.getElementById('gen-count').value || '10', 10);
  const difficulty = document.getElementById('gen-difficulty').value;
  const grade = document.getElementById('gen-grade').value.trim();
  const source = document.getElementById('gen-source').value.trim();
  const status = document.getElementById('gen-status');
  const btn = document.getElementById('gen-btn');
  const preview = document.getElementById('gen-preview');

  if(!topic){ status.style.color='var(--red)'; status.textContent='Topic required.'; return; }

  // Disable the button + show loading state. Generation can take 1-3s
  // even on Groq for 25 questions, and a teacher who clicks twice
  // would queue two LLM calls (we rate-limit server-side, but the UX
  // is still better if we never let them double-click).
  btn.disabled = true;
  btn.textContent = 'Generating…';
  status.style.color = 'var(--muted)';
  status.textContent = 'Calling AI (typically 1-3 seconds)…';
  preview.innerHTML = '';

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/generate`, {
      method:'POST',
      body: JSON.stringify({
        topic, count, difficulty,
        grade_level: grade || undefined,
        source_text: source || undefined,
      })
    });
    const data = await r.json();
    if(!r.ok){
      status.style.color='var(--red)';
      status.textContent = data.detail || 'Generation failed.';
      return;
    }
    _genPreview = data.questions || [];
    if(!_genPreview.length){
      status.style.color='var(--red)';
      status.textContent = 'No usable questions returned. Try a more specific topic.';
      return;
    }
    status.style.color='var(--emerald)';
    status.textContent = `Generated ${_genPreview.length}. Review below, then click "Add to Bank".`;
    _renderGenPreview();
  } catch(e){
    status.style.color='var(--red)';
    status.textContent = 'Network error.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

function _renderGenPreview(){
  const preview = document.getElementById('gen-preview');
  if(!_genPreview.length){ preview.innerHTML=''; return; }
  // Render each question as an editable card. Teachers will want to
  // tweak wording before saving — we expose that inline via
  // contentEditable spans so they don't have to re-generate just
  // because option B has an awkward phrase. Edits are read back
  // from the DOM at save time.
  const rows = _genPreview.map((q, i) => `
    <div data-gen-idx="${i}" style="padding:10px 12px;margin-top:6px;background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:8px">
      <div style="display:flex;align-items:flex-start;gap:8px">
        <span style="color:var(--muted);font-size:11px;font-family:monospace;flex-shrink:0;margin-top:2px">${i+1}.</span>
        <div style="flex:1;min-width:0">
          <div contenteditable="true" data-field="question" style="font-size:13px;color:var(--text);outline:none;border-bottom:1px dashed transparent;padding:2px 0">${_escHtml(q.question)}</div>
          <div style="margin-top:6px;display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:12px">
            ${['A','B','C','D'].map(L => `
              <span style="color:${q.correct.includes(L)?'var(--emerald)':'var(--muted)'};font-weight:${q.correct.includes(L)?'600':'400'}">${L}.</span>
              <span contenteditable="true" data-field="option_${L}" style="color:var(--text);outline:none">${_escHtml(q['option_'+L]||'')}</span>
            `).join('')}
          </div>
          <div style="margin-top:6px;display:flex;align-items:center;gap:6px;font-size:11px">
            <span style="color:var(--muted)">Correct:</span>
            <input type="text" data-field="correct" value="${escAttr(q.correct)}" maxlength="7"
              style="background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:4px;padding:1px 6px;color:var(--accent-light);font-family:monospace;width:60px;font-size:11px">
            <span style="color:var(--muted);margin-left:8px">Tags:</span>
            <input type="text" data-field="tags" value="${escAttr((q.tags||[]).join(', '))}"
              style="background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:4px;padding:1px 6px;color:var(--text);flex:1;font-size:11px">
            <button class="btn btn-secondary btn-sm" data-action="_dropGenQ" data-args='${_jsonArgsForAttr(i)}' style="padding:1px 6px;font-size:10px;color:var(--red)" title="Discard">&times;</button>
          </div>
        </div>
      </div>
    </div>`).join('');
  preview.innerHTML = `
    ${rows}
    <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn btn-primary btn-sm" data-action="commitGenPreview" data-args='${_jsonArgsForAttr('exam')}' title="Save to bank AND add to the currently selected exam">+ ${_genPreview.length} → Exam</button>
      <button class="btn btn-secondary btn-sm" data-action="commitGenPreview" data-args='${_jsonArgsForAttr('bank')}' title="Save to question bank only (use Bank tab to add to an exam later)">+ ${_genPreview.length} → Bank</button>
      <button class="btn btn-secondary btn-sm" data-action="_discardGenPreview">Discard</button>
    </div>`;
}

function _dropGenQ(idx){
  _genPreview.splice(idx, 1);
  _renderGenPreview();
}

async function commitGenPreview(destination = 'bank'){
  // destination = 'bank'  → save to bank only (legacy default)
  // destination = 'exam'  → save to bank AND auto-import into the
  //                         currently selected exam in one click.
  //                         Two-step under the hood (the bank write
  //                         is the source of truth; bank-to-exam
  //                         copies onto the exam) but feels like one.
  if(destination === 'exam'){
    if(!currentExamId){
      showModal('Select an exam first (top of the page) before adding directly.');
      return;
    }
  }
  // Read back any inline edits before sending — teachers expect
  // their tweaks to persist, and re-rendering would lose them.
  const cards = document.querySelectorAll('#gen-preview [data-gen-idx]');
  const out = [];
  cards.forEach(card => {
    const idx = parseInt(card.dataset.genIdx, 10);
    const orig = _genPreview[idx];
    if(!orig) return;
    const get = sel => {
      const el = card.querySelector(`[data-field="${sel}"]`);
      return el ? (el.value !== undefined ? el.value : el.textContent).trim() : '';
    };
    const tagsStr = get('tags');
    out.push({
      question: get('question'),
      question_type: orig.question_type || 'mcq_single',
      option_A: get('option_A'),
      option_B: get('option_B'),
      option_C: get('option_C'),
      option_D: get('option_D'),
      correct: get('correct').toUpperCase(),
      tags: tagsStr.split(',').map(s=>s.trim()).filter(Boolean),
      image_url: '',
    });
  });
  if(!out.length){ showModal('Nothing to save.'); return; }

  const status = document.getElementById('gen-status');
  status.style.color='var(--muted)';
  status.textContent = 'Saving to bank…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/import`, {
      method:'POST',
      body: JSON.stringify({questions: out})
    });
    const d = await r.json();
    if(!r.ok){ status.style.color='var(--red)'; status.textContent = d.detail||'Save failed.'; return; }
    const savedCount = d.imported || out.length;
    const newIds = (d.inserted_ids || (Array.isArray(d.data) ? d.data.map(x => x.id) : []));
    _genPreview = [];
    document.getElementById('gen-preview').innerHTML = '';
    document.getElementById('gen-topic').value = '';
    document.getElementById('gen-source').value = '';

    // If the teacher chose "→ Exam", chain the bank-to-exam copy.
    // We use the IDs the import endpoint returns so we don't have
    // to re-fetch the bank list and pattern-match. Falls back to
    // copying ALL freshly-saved bank rows if the endpoint didn't
    // surface IDs (older deploys).
    if(destination === 'exam'){
      status.style.color = 'var(--muted)';
      status.textContent = `Saved ${savedCount} to bank · adding to exam…`;
      try{
        // Reload bank to pick up the new rows + their generated UUIDs
        await loadBank();
        // Take the most-recently-created N rows that match this batch
        const idsToAdd = newIds.length ? newIds
          : (_bankData || []).slice(0, savedCount).map(b => b.id);
        if(!idsToAdd.length){
          status.style.color = 'var(--amber)';
          status.textContent = 'Saved to bank but couldn’t identify them for auto-add. Use Bank → Add Selected.';
          if(typeof setAITab === 'function') setAITab('bank');
          return;
        }
        const r2 = await authFetch(`${BASE}/api/v1/admin/question-bank/to-exam`, {
          method: 'POST',
          body: JSON.stringify({question_ids: idsToAdd, exam_id: currentExamId}),
        });
        const d2 = await r2.json();
        if(!r2.ok){
          status.style.color = 'var(--red)';
          status.textContent = `Saved to bank, but adding to exam failed: ${d2.detail || 'server error'}`;
          if(typeof setAITab === 'function') setAITab('bank');
          return;
        }
        status.style.color = 'var(--emerald)';
        status.textContent = `Added ${d2.added || idsToAdd.length} questions to the exam.`;
        // Refresh the exam editor so the new questions appear there too.
        if(typeof loadQuestions === 'function') loadQuestions();
      }catch(e){
        status.style.color = 'var(--red)';
        status.textContent = 'Saved to bank, but auto-add to exam errored. Use Bank → Add Selected.';
        if(typeof setAITab === 'function') setAITab('bank');
      }
      return;
    }

    // destination === 'bank' (default) — show the bank tab so the
    // teacher SEES their newly-saved questions appear.
    status.style.color = 'var(--emerald)';
    status.textContent = `Saved ${savedCount} to bank.`;
    if(typeof setAITab === 'function') setAITab('bank');
    else loadBank();
    // (Previously hid #bank-generate-area here, but in the Phase 2.8
    // 3-pane layout that left the AI panel body empty / black until
    // the user manually clicked another tab. setAITab('bank') above
    // already moved focus to the bank list with the new questions
    // visible — so we leave the generate pane in the DOM, just
    // hidden by the data-aipane swap, ready for the next generation.)
  } catch(e){
    status.style.color='var(--red)'; status.textContent = 'Network error.';
  }
}

function loadBankFile(input){
  const file = input.files[0];
  if(!file) return;
  const reader = new FileReader();
  reader.onload = function(e){
    const text = e.target.result;
    // Try to parse CSV
    if(file.name.endsWith('.csv')){
      document.getElementById('bank-import-text').value = JSON.stringify(_parseBankCSV(text), null, 2);
    }else{
      document.getElementById('bank-import-text').value = text;
    }
  };
  reader.readAsText(file);
}

// Quote-aware CSV parser — naive split-on-comma corrupts any question
// that contains a comma (which is most non-trivial questions). Walks
// the file character-by-character, tracking whether we're inside a
// quoted field. Handles RFC 4180 doubled quotes ("" → ") and CRLF
// line endings. Tags column gets split on `|` so a CSV row can carry
// multiple tags without colliding with the field delimiter.
function _parseBankCSV(text){
  // Strip BOM if Excel exported with one — otherwise the first header
  // would be `\ufeffquestion` and not match the server schema.
  if(text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  const rows = [];
  let row = [];
  let cell = '';
  let inQuotes = false;
  for(let i=0; i<text.length; i++){
    const c = text[i];
    if(inQuotes){
      if(c === '"'){
        if(text[i+1] === '"'){ cell += '"'; i++; }  // escaped quote
        else { inQuotes = false; }
      } else { cell += c; }
    } else {
      if(c === '"'){ inQuotes = true; }
      else if(c === ','){ row.push(cell); cell=''; }
      else if(c === '\r'){ /* swallow — handled by \n */ }
      else if(c === '\n'){ row.push(cell); rows.push(row); row=[]; cell=''; }
      else { cell += c; }
    }
  }
  // Flush trailing cell/row if file didn't end with a newline
  if(cell.length || row.length){ row.push(cell); rows.push(row); }
  if(rows.length < 2) return [];
  const headers = rows[0].map(h=>h.trim().toLowerCase());
  return rows.slice(1)
    .filter(r => r.some(v => v && v.trim()))  // skip blank rows
    .map(r => {
      const obj = {};
      headers.forEach((h, idx)=>{
        let v = (r[idx] || '').trim();
        // Tags column: split on `|` so "easy|algebra|grade-10" → ["easy","algebra","grade-10"]
        if(h === 'tags'){
          obj.tags = v ? v.split('|').map(t=>t.trim()).filter(Boolean) : [];
        } else {
          obj[h] = v;
        }
      });
      return obj;
    });
}

// Hands the teacher a working starter CSV — saves them from
// reverse-engineering the column layout from the modal hint text.
// Includes one realistic example row so they can pattern-match
// rather than guess at how to encode options.
function downloadBankCSVTemplate(){
  const csv = [
    'question,question_type,option_A,option_B,option_C,option_D,correct,image_url,tags',
    '"What is 2 + 2?",mcq_single,"3","4","5","6",B,,easy|math',
    '"Pick the prime numbers.",mcq_multi,"2","3","4","6","A,B",,medium|math|primes',
  ].join('\n');
  const blob = new Blob([csv], {type:'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'question_bank_template.csv'; a.click();
  URL.revokeObjectURL(url);
}

async function doBankImport(){
  const text = document.getElementById('bank-import-text').value.trim();
  const status = document.getElementById('bank-import-status');
  if(!text){ status.style.color='var(--red)'; status.textContent='Paste data first'; return; }
  let questions;
  try{ questions = JSON.parse(text); }
  catch(e){ status.style.color='var(--red)'; status.textContent='Invalid JSON'; return; }
  if(!Array.isArray(questions)) questions = [questions];
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/import`,
      {method:'POST',body:JSON.stringify({questions})});
    const d = await r.json();
    if(r.ok){
      status.style.color='var(--emerald)'; status.textContent=`Imported ${d.imported} questions`;
      document.getElementById('bank-import-text').value='';
      loadBank();
    }else{ status.style.color='var(--red)'; status.textContent=d.detail||'Error'; }
  }catch(e){ status.style.color='var(--red)'; status.textContent='Import failed'; }
}

async function exportBank(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/export`);
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'question_bank.json'; a.click();
    URL.revokeObjectURL(url);
  }catch(e){ showModal('Export failed'); }
}

// "Save to Bank" helper — called from question editor cards. Prompts
// for tags so the bank stays organised; tags are how teachers find
// questions later. Comma-separated input is normalised to an array of
// trimmed non-empty strings before sending.
async function saveQuestionToBank(idx){
  if(!qData[idx]) return;
  const q = qData[idx];
  if(!q.question || !String(q.question).trim()){ showModal('Question text is empty.'); return; }
  // Pre-fill tags by asking the LLM, then let the teacher edit before
  // confirming. If the LLM endpoint isn't configured (503), we silently
  // fall back to an empty default — auto-tag is a nice-to-have, not
  // a blocker on saving to the bank.
  let suggested = '';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/suggest-tags`, {
      method:'POST',
      body: JSON.stringify({
        question: q.question,
        options: q.options || {},
        correct: q.correct || '',
      })
    });
    if(r.ok){
      const d = await r.json();
      suggested = (d.tags || []).join(', ');
    }
  }catch(_){/* offline or no LLM — fall through to manual entry */}

  const tagsRaw = await appPrompt(
    suggested
      ? 'Tags (AI-suggested — edit if needed, or clear for none):'
      : 'Add tags (comma-separated). Example: easy, algebra, grade-10',
    suggested,
    {title:'Question tags', okText:'Save'}
  );
  if(tagsRaw === null) return; // cancelled
  const tags = tagsRaw.split(',').map(t=>t.trim()).filter(Boolean);
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank`,
      {method:'POST',body:JSON.stringify({
        question: q.question,
        question_type: q.question_type || 'mcq_single',
        options: q.options || {},
        correct: q.correct || '',
        image_url: q.image_url || '',
        tags,
      })});
    if(r.ok){
      const msg = document.getElementById('q-save-msg');
      if(msg){ msg.style.color='var(--emerald)'; msg.textContent='Saved to bank.'; setTimeout(()=>msg.textContent='',2500); }
      else showModal('Saved to bank!');
    } else { const d=await r.json(); showModal(d.detail||'Error'); }
  }catch(e){ showModal('Failed to save to bank'); }
}

// Edit a question already in the bank. The PUT endpoint accepts a
// partial fields dict, so we only ship what changed. We use simple
// Uses the shared app prompt helper so dashboard edits never fall back
// to blocking native browser dialogs.
async function editBankQ(qid){
  const q = _bankData.find(x=>x.id===qid);
  if(!q) return;
  const newQ = await appPrompt('Edit question text:', q.question, {title:'Edit bank question', okText:'Next', multiline:true});
  if(newQ === null) return;
  const newCorrect = await appPrompt('Correct answer (e.g. A or A,B for multi):', q.correct || '', {title:'Edit correct answer', okText:'Next'});
  if(newCorrect === null) return;
  const tagsRaw = await appPrompt('Tags (comma-separated):', (q.tags||[]).join(', '), {title:'Edit tags', okText:'Save'});
  if(tagsRaw === null) return;
  const fields = {
    question: newQ.trim(),
    correct: newCorrect.trim(),
    tags: tagsRaw.split(',').map(t=>t.trim()).filter(Boolean),
  };
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/question-bank/${qid}`,
      {method:'PUT',body:JSON.stringify(fields)});
    if(r.ok) loadBank();
    else { const d=await r.json(); showModal(d.detail||'Error'); }
  }catch(e){ showModal('Edit failed'); }
}

// ── STUDENT GROUPS ─────────────────────────────────────────────
let _groupsData = [];
let _activeGroupId = null;

async function loadGroups(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/groups`);
    if(!r.ok) return;
    _groupsData = await r.json();
    renderGroups();
    populateGroupSelect();
    loadExamGroups();
  }catch(e){ console.error('loadGroups:', e); }
}

function renderGroups(){
  const list = document.getElementById('groups-list');
  const empty = document.getElementById('groups-empty');
  if(!_groupsData.length){ list.innerHTML=''; empty.style.display=''; return; }
  empty.style.display='none';
  list.innerHTML = _groupsData.map(g=>`
    <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(255,255,255,.02);border:1px solid var(--border);border-radius:6px;margin-bottom:6px;cursor:pointer"
         data-action="openGroupDetail" data-args='${_jsonArgsForAttr(g.id,g.group_name)}'>
      <span style="flex:1;font-size:13px;color:var(--text)">${_escHtml(g.group_name)}</span>
      <span style="font-size:11px;color:var(--muted)">${g.member_count||0} members</span>
      <button class="btn btn-secondary btn-sm" data-action="deleteGroup" data-args='${_jsonArgsForAttr(g.id)}' style="padding:2px 8px;font-size:10px;color:var(--red)">Delete</button>
    </div>`).join('');
}

async function createGroup(){
  const inp = document.getElementById('new-group-name');
  const name = inp.value.trim();
  if(!name) return;
  const st = document.getElementById('group-status');
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/groups`,{method:'POST',body:JSON.stringify({group_name:name})});
    if(!r.ok){ const d=await r.json(); st.style.color='var(--red)'; st.textContent=d.detail||'Error'; return; }
    inp.value='';
    st.style.color='var(--emerald)'; st.textContent='Group created';
    setTimeout(()=>st.textContent='',3000);
    loadGroups();
  }catch(e){ st.style.color='var(--red)'; st.textContent='Failed'; }
}

async function deleteGroup(gid){
  if(!(await appConfirm('Delete this group? Members and exam assignments will also be removed.', 'Delete group', {okText:'Delete'}))) return;
  await authFetch(`${BASE}/api/v1/admin/groups/${gid}`,{method:'DELETE'});
  if(_activeGroupId===gid) closeGroupDetail();
  loadGroups();
}

async function openGroupDetail(gid, name){
  _activeGroupId = gid;
  document.getElementById('group-detail').style.display='';
  document.getElementById('group-detail-name').textContent = name;
  const r = await authFetch(`${BASE}/api/v1/admin/groups/${gid}/members`);
  const members = r.ok ? await r.json() : [];
  const list = document.getElementById('group-members-list');
  const empty = document.getElementById('group-members-empty');
  if(!members.length){ list.innerHTML=''; empty.style.display=''; return; }
  empty.style.display='none';
  list.innerHTML = members.map(m=>`
    <span style="display:inline-flex;align-items:center;gap:4px;background:rgba(61,217,168,.1);border:1px solid rgba(61,217,168,.2);border-radius:14px;padding:3px 10px;margin:3px 3px;font-size:12px;color:var(--accent-light)">
      ${_escHtml(m.roll_number)}
      <span data-action="removeGroupMember" data-args='${_jsonArgsForAttr(gid,m.roll_number)}' style="cursor:pointer;opacity:0.6;font-size:14px">&times;</span>
    </span>`).join('');
}

function closeGroupDetail(){
  _activeGroupId = null;
  document.getElementById('group-detail').style.display='none';
}

async function addGroupMembers(){
  if(!_activeGroupId) return;
  const inp = document.getElementById('add-member-rolls');
  const rolls = inp.value.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean);
  if(!rolls.length) return;
  await authFetch(`${BASE}/api/v1/admin/groups/${_activeGroupId}/members`,
    {method:'POST',body:JSON.stringify({roll_numbers:rolls})});
  inp.value='';
  openGroupDetail(_activeGroupId, document.getElementById('group-detail-name').textContent);
  loadGroups();
}

// Renamed from `removeMember` 2026-05-23 (audit M7). The org-side
// members table at line ~2463 was calling `removeMember(memberId)`
// which collided with this group-side function — the org call landed
// on /api/v1/admin/groups/{teacher_id}/members with
// roll_numbers:[undefined], silently failing or worse. Split into
// removeGroupMember + removeOrgMember (the latter defined below).
async function removeGroupMember(gid, roll){
  await authFetch(`${BASE}/api/v1/admin/groups/${gid}/members`,
    {method:'DELETE',body:JSON.stringify({roll_numbers:[roll]})});
  openGroupDetail(gid, document.getElementById('group-detail-name').textContent);
  loadGroups();
}

async function removeOrgMember(memberId){
  if(!memberId) return;
  if(!(await appConfirm('Remove this teacher from the organization? They will lose access immediately.', 'Remove member', {okText:'Remove'}))) return;
  const r = await authFetch(`${BASE}/api/v1/org/members/${encodeURIComponent(memberId)}`,
    {method:'DELETE'});
  if(!r.ok){
    const d = await r.json().catch(()=>({}));
    showModal(d.detail || 'Could not remove member');
    return;
  }
  loadMembers();   // existing loader for the org members table (line ~2451)
}

function populateGroupSelect(){
  const sel = document.getElementById('assign-group-select');
  sel.innerHTML = '<option value="">Select a group to restrict access...</option>' +
    _groupsData.map(g=>`<option value="${escAttr(g.id)}">${_escHtml(g.group_name)} (${g.member_count||0})</option>`).join('');
}

async function loadExamGroups(){
  const eid = currentExamId;
  if(!eid){ document.getElementById('exam-groups-list').innerHTML=''; document.getElementById('exam-groups-none').style.display=''; return; }
  const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/groups`);
  const groups = r.ok ? await r.json() : [];
  const list = document.getElementById('exam-groups-list');
  const none = document.getElementById('exam-groups-none');
  if(!groups.length){ list.innerHTML=''; none.style.display=''; return; }
  none.style.display='none';
  list.innerHTML = groups.map(g=>`
    <span style="display:inline-flex;align-items:center;gap:4px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.2);border-radius:14px;padding:3px 10px;margin:3px 3px;font-size:12px;color:var(--emerald)">
      ${_escHtml(g.group_name)}
      <span data-action="unassignGroup" data-args='${_jsonArgsForAttr(g.id)}' style="cursor:pointer;opacity:0.6;font-size:14px">&times;</span>
    </span>`).join('');
}

async function assignGroupToExam(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const gid = document.getElementById('assign-group-select').value;
  if(!gid) return;
  await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/groups`,
    {method:'POST',body:JSON.stringify({group_ids:[gid]})});
  loadExamGroups();
}

async function unassignGroup(gid){
  const eid = currentExamId;
  if(!eid) return;
  await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/groups/${gid}`,{method:'DELETE'});
  loadExamGroups();
}

// Load groups when tools tab opens or exam switches
const _origSwitchTab = switchTab;
switchTab = function(tab){
  _origSwitchTab(tab);
  if(tab==='tools'){ loadGroups(); loadInvites(); _populateInviteGroupSelect(); try{ if(typeof loadGoogleClassroom==='function') loadGoogleClassroom(); }catch(_){}}
};

// ── EMAIL INVITES ──────────────────────────────────────────────
let _invitesData = [];

function _populateInviteGroupSelect(){
  const sel = document.getElementById('invite-from-group');
  if(!sel) return;
  sel.innerHTML = '<option value="">— or pull from a group —</option>' +
    (_groupsData||[]).map(g=>`<option value="${escAttr(g.id)}">${_escHtml(g.group_name)} (${g.member_count||0})</option>`).join('');
}

function importInviteCsv(evt){
  const f = evt.target.files && evt.target.files[0];
  if(!f) return;
  const reader = new FileReader();
  reader.onload = e => {
    const text = String(e.target.result||'');
    const lines = text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
    // Drop header if it looks like one
    if(lines.length && /email/i.test(lines[0]) && /name/i.test(lines[0])) lines.shift();
    // Normalize "name,email,roll" — accept any ordering with header sniffing is overkill here; keep simple
    const out = lines.map(l => {
      const parts = l.split(',').map(s=>s.trim());
      // If first looks like email, reorder
      if(parts.length>=3 && /@/.test(parts[0])) return `${parts[1]||''}, ${parts[0]}, ${parts[2]||''}`;
      return parts.slice(0,3).join(', ');
    });
    document.getElementById('invite-recipients').value = out.join('\n');
    evt.target.value = '';
  };
  reader.readAsText(f);
}

async function pullGroupIntoInvites(){
  const gid = document.getElementById('invite-from-group').value;
  if(!gid){ return; }
  const st = document.getElementById('invite-result');
  st.style.color='var(--muted)'; st.textContent='Loading group members…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/groups/${gid}/members`);
    if(!r.ok){ st.style.color='var(--red)'; st.textContent='Failed to load members'; return; }
    const members = await r.json();
    if(!members.length){ st.style.color='var(--amber)'; st.textContent='Group has no members'; return; }
    const missing = [];
    const rows = members.map(m => {
      const roll = String(m.roll_number||'').toUpperCase();
      if(!m.email){ missing.push(roll); return `, , ${roll}`; }
      return `${m.full_name||''}, ${m.email}, ${roll}`;
    });
    const ta = document.getElementById('invite-recipients');
    const existing = ta.value.trim();
    ta.value = (existing ? existing + '\n' : '') + rows.join('\n');
    if(missing.length){
      st.style.color='var(--amber)';
      st.textContent = `Loaded ${rows.length}. Missing email for: ${missing.slice(0,5).join(', ')}${missing.length>5?'…':''} — fill in manually before sending.`;
    }else{
      st.style.color='var(--emerald)';
      st.textContent = `Loaded ${rows.length} members.`;
      setTimeout(()=>st.textContent='',3000);
    }
  }catch(e){ st.style.color='var(--red)'; st.textContent='Failed to pull group'; }
}

function _parseInviteRows(){
  const text = document.getElementById('invite-recipients').value || '';
  const lines = text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
  const out = []; const bad = [];
  for(const l of lines){
    const parts = l.split(',').map(s=>s.trim());
    const [full_name, email, roll_number] = [parts[0]||'', parts[1]||'', (parts[2]||'').toUpperCase()];
    if(!email || !/@/.test(email) || !full_name || !roll_number){ bad.push(l); continue; }
    out.push({full_name, email, roll_number});
  }
  return {rows: out, bad};
}

async function sendInvites(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const {rows, bad} = _parseInviteRows();
  if(!rows.length){ showModal('Add at least one recipient as: name, email, roll'); return; }
  const st = document.getElementById('invite-result');
  if(bad.length){
    if(!(await appConfirm(`${bad.length} row(s) are malformed and will be skipped. Continue with ${rows.length} valid row(s)?`, 'Send invites', {okText:'Continue'}))) return;
  }else{
    if(!(await appConfirm(`Send invite emails to ${rows.length} student(s) for this exam?`, 'Send invites', {okText:'Send'}))) return;
  }
  const btn = document.getElementById('btn-invite-send');
  btn.disabled = true;
  st.style.color='var(--muted)'; st.textContent=`Sending ${rows.length} invite(s)…`;
  const payload = {
    exam_id: eid,
    recipients: rows,
    custom_message: document.getElementById('invite-message').value.trim(),
    per_invite_code: document.getElementById('invite-per-code').checked,
  };
  const exp = document.getElementById('invite-expires').value;
  if(exp) payload.expires_at = new Date(exp).toISOString();
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/send`, {method:'POST', body:JSON.stringify(payload)});
    const d = await r.json();
    if(!r.ok){ st.style.color='var(--red)'; st.innerHTML = _escHtml(d.detail || 'Send failed'); return; }
    const sent = d.sent||0, failed = d.failed||0, skipped = d.skipped||0;
    const failures = Array.isArray(d.failures) ? d.failures : [];
    const summary = `Sent ${sent}${failed?`, ${failed} failed`:''}${skipped?`, ${skipped} skipped (duplicate)`:''}.`;

    if(failed === 0 && skipped === 0){
      // clean success — clear inputs, all good
      st.style.color='var(--emerald)';
      st.textContent = summary;
      document.getElementById('invite-recipients').value = '';
      document.getElementById('invite-message').value = '';
    }else{
      // partial or total failure — preserve inputs so the teacher can fix & retry.
      st.style.color = failed ? 'var(--red)' : 'var(--amber)';
      const failList = failures.length
        ? `<div style="margin-top:8px;padding:10px 12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);border-radius:8px">
             <div style="font-size:11px;color:var(--red);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Failed to send</div>
             ${failures.slice(0,20).map(f =>
                 `<div style="font-size:12px;color:var(--text);font-family:monospace">${_escHtml(f.email||'')} <span style="color:var(--muted)">— ${_escHtml((f.reason||'').slice(0,160))}</span></div>`
               ).join('')}
             ${failures.length>20?`<div style="font-size:11px;color:var(--muted);margin-top:4px">…and ${failures.length-20} more</div>`:''}
             <div style="margin-top:8px;font-size:11px;color:var(--muted)">Fix the rows above in the recipients box and click Send again — successful sends are not duplicated.</div>
           </div>`
        : '';
      st.innerHTML = `<div>${_escHtml(summary)}</div>${failList}`;
      // If any succeeded, trim the recipients textarea to just the failed ones so
      // teacher can immediately retry without re-typing the good rows.
      if(sent > 0 && failures.length){
        const failedEmails = new Set(failures.map(f=>String(f.email||'').toLowerCase()));
        const ta = document.getElementById('invite-recipients');
        const keep = (ta.value||'').split(/\r?\n/).filter(line => {
          const m = line.match(/,\s*([^,]+?)\s*,/);
          const email = m ? m[1].toLowerCase() : '';
          return failedEmails.has(email);
        });
        ta.value = keep.join('\n');
      }
    }
    await loadInvites();
    // Scroll the sent-invites table into view so badges are visible immediately.
    const list = document.getElementById('invite-list');
    if(list) list.scrollIntoView({behavior:'smooth', block:'nearest'});
  }catch(e){ st.style.color='var(--red)'; st.textContent='Network error'; }
  finally{ btn.disabled = false; }
}

async function loadInvites(){
  const eid = currentExamId;
  const list = document.getElementById('invite-list');
  const empty = document.getElementById('invite-empty');
  const countEl = document.getElementById('invite-count');
  if(!list) return;
  if(!eid){ list.innerHTML=''; empty.style.display=''; empty.textContent='Select an exam to see invites.'; countEl.textContent=''; return; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites?exam_id=${encodeURIComponent(eid)}`);
    if(!r.ok){ list.innerHTML=''; empty.style.display=''; empty.textContent='Failed to load invites.'; return; }
    // API contract is {invites:[...]} — be tolerant of either shape so a future
    // contract change (return a bare array) doesn't silently empty the UI.
    const data = await r.json();
    _invitesData = Array.isArray(data) ? data : (data.invites || []);
    _renderInvites();
  }catch(e){ list.innerHTML=''; empty.style.display=''; empty.textContent='Network error.'; }
}

function _inviteBadge(status){
  const palette = {
    queued:   ['rgba(148,163,184,.15)','var(--muted)','#94a3b8'],
    sent:     ['rgba(59,130,246,.15)','#60a5fa','#60a5fa'],
    opened:   ['rgba(139,92,246,.15)','#a78bfa','#a78bfa'],
    clicked:  ['rgba(56,189,248,.15)','#38bdf8','#38bdf8'],
    accepted: ['rgba(16,185,129,.15)','var(--emerald)','#10b981'],
    bounced:  ['rgba(239,68,68,.15)','var(--red)','#ef4444'],
    failed:   ['rgba(239,68,68,.15)','var(--red)','#ef4444'],
    revoked:  ['rgba(100,116,139,.15)','var(--muted)','#64748b'],
  };
  const [bg, fg, brd] = palette[status] || palette.queued;
  return `<span style="display:inline-block;background:${bg};color:${fg};border:1px solid ${brd};border-radius:10px;padding:2px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.04em">${status}</span>`;
}

function _renderInvites(){
  const list = document.getElementById('invite-list');
  const empty = document.getElementById('invite-empty');
  const countEl = document.getElementById('invite-count');
  if(!_invitesData.length){ list.innerHTML=''; empty.style.display=''; empty.textContent='No invites sent yet.'; countEl.textContent=''; return; }
  empty.style.display='none';
  const counts = _invitesData.reduce((a,i)=>{ a[i.status]=(a[i.status]||0)+1; return a; }, {});
  countEl.textContent = Object.entries(counts).map(([k,v])=>`${v} ${k}`).join(' · ');
  const _fmtTime = (iso) => iso ? new Date(iso).toLocaleString() : '—';
  // Clicks (server-side redirect hits) and exam-session starts are
  // both reliable engagement signals — opens are not. Surfacing all
  // three lets a teacher see at a glance who actually showed up vs
  // who just had their pixel pre-fetched by Apple Mail.
  const rows = _invitesData.map(i => {
    const safeLink = _escHtml(i.invite_url||'');
    const clickCell = i.clicked_at
      ? `<span style="color:#38bdf8">${_fmtTime(i.clicked_at)}${(i.click_count>1)?` <span style="color:var(--muted)">×${i.click_count}</span>`:''}</span>`
      : '<span style="color:var(--muted)">—</span>';
    const startCell = i.started_at
      ? `<span style="color:var(--emerald)">${_fmtTime(i.started_at)}</span>`
      : '<span style="color:var(--muted)">—</span>';
    const actions = [
      i.invite_url ? `<button class="btn btn-secondary btn-sm" data-action="_copyInviteLink" data-args='${_jsonArgsForAttr(i.invite_url)}' style="padding:2px 8px;font-size:10px">Copy</button>` : '',
      (i.status!=='accepted' && i.status!=='revoked') ? `<button class="btn btn-secondary btn-sm" data-action="resendInvite" data-args='${_jsonArgsForAttr(i.id)}' style="padding:2px 8px;font-size:10px">Resend</button>` : '',
      (i.status!=='accepted' && i.status!=='revoked') ? `<button class="btn btn-secondary btn-sm" data-action="revokeInvite" data-args='${_jsonArgsForAttr(i.id)}' style="padding:2px 8px;font-size:10px;color:var(--red)">Revoke</button>` : '',
    ].filter(Boolean).join(' ');
    return `<tr>
      <td style="padding:6px 8px;font-size:12px">${_escHtml(i.full_name||'')}</td>
      <td style="padding:6px 8px;font-size:12px;color:var(--muted)">${_escHtml(i.email||'')}</td>
      <td style="padding:6px 8px;font-size:11px;font-family:monospace">${_escHtml(i.roll_number||'')}</td>
      <td style="padding:6px 8px">${_inviteBadge(i.status)}</td>
      <td style="padding:6px 8px;font-size:11px;color:var(--muted)">${_fmtTime(i.sent_at)}</td>
      <td style="padding:6px 8px;font-size:11px">${clickCell}</td>
      <td style="padding:6px 8px;font-size:11px">${startCell}</td>
      <td style="padding:6px 8px;white-space:nowrap">${actions}</td>
    </tr>`;
  }).join('');
  const th = (label, hint) => `<th title="${hint||''}" style="padding:6px 8px;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:600">${label}</th>`;
  list.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">
    <thead><tr style="border-bottom:1px solid var(--border);text-align:left">
      ${th('Name')}
      ${th('Email')}
      ${th('Roll')}
      ${th('Status')}
      ${th('Sent')}
      ${th('Clicked', 'Server-side redirect hit — reliable signal, immune to Outlook/Apple Mail pixel blocking')}
      ${th('Started', 'Student actually opened the exam in Procta — ground truth for engagement')}
      ${th('Actions')}
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function _copyInviteLink(url){
  navigator.clipboard.writeText(url).then(()=>{
    const st = document.getElementById('invite-result');
    st.style.color='var(--emerald)'; st.textContent='Link copied.';
    setTimeout(()=>st.textContent='',2000);
  });
}

async function resendInvite(id){
  if(!(await appConfirm('Resend this invite? A fresh token will be generated and the old link will stop working.', 'Resend invite', {okText:'Resend'}))) return;
  const st = document.getElementById('invite-result');
  st.style.color='var(--muted)'; st.textContent='Resending…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/${id}/resend`, {method:'POST'});
    if(!r.ok){ const d=await r.json(); st.style.color='var(--red)'; st.textContent=d.detail||'Failed'; return; }
    st.style.color='var(--emerald)'; st.textContent='Resent.';
    setTimeout(()=>st.textContent='',2500);
    loadInvites();
  }catch(e){ st.style.color='var(--red)'; st.textContent='Network error'; }
}

async function revokeInvite(id){
  if(!(await appConfirm('Revoke this invite? The student will no longer be able to join using this link.', 'Revoke invite', {okText:'Revoke'}))) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/${id}`, {method:'DELETE'});
    if(!r.ok){ const d=await r.json(); showModal(d.detail||'Failed'); return; }
    loadInvites();
  }catch(e){ showModal('Network error'); }
}

async function resendBouncedInvites(){
  if(!(await appConfirm('Resend all invites that bounced or failed for this exam?', 'Resend failed invites', {okText:'Resend'}))) return;
  const eid = currentExamId;
  const st = document.getElementById('invite-result');
  st.style.color='var(--muted)'; st.textContent='Resending bounced invites…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/resend-bounced`,
      {method:'POST', body: JSON.stringify(eid ? {exam_id: eid} : {})});
    const d = await r.json();
    if(!r.ok){ st.style.color='var(--red)'; st.textContent=d.detail||'Failed'; return; }
    st.style.color='var(--emerald)'; st.textContent=`Requeued ${d.requeued||0}${d.failed?`, ${d.failed} still failed`:''}.`;
    loadInvites();
  }catch(e){ st.style.color='var(--red)'; st.textContent='Network error'; }
}

// ── ANALYTICS ──────────────────────────────────────────────────
let _analyticsCache = {};
let _analyticsLoading = false;

async function loadAnalytics(){
  const eid = currentExamId;
  const empty = document.getElementById('analytics-empty');
  const loading = document.getElementById('analytics-loading');
  const content = document.getElementById('analytics-content');
  if(!eid){ empty.style.display=''; loading.style.display='none'; content.style.display='none'; return; }
  const cacheKey = `${eid}:${currentTeacherFilter || 'all'}`;

  // Use cached if fresh (<60s)
  const cached = _analyticsCache[cacheKey];
  if(cached && Date.now()-cached._ts < 60000){ _renderAnalytics(cached); return; }

  empty.style.display='none'; loading.style.display=''; content.style.display='none';
  if(_analyticsLoading) return;
  _analyticsLoading = true;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/analytics${_examQuery('?')}`);
    if(!r.ok) throw new Error('Failed');
    const data = await r.json();
    data._ts = Date.now();
    _analyticsCache[cacheKey] = data;
    _renderAnalytics(data);
  }catch(e){
    console.error('Analytics load error:', e);
    empty.style.display=''; empty.querySelector('div:last-child').textContent='Failed to load analytics';
    loading.style.display='none'; content.style.display='none';
  }finally{ _analyticsLoading = false; }
}

function _renderAnalytics(data){
  document.getElementById('analytics-empty').style.display='none';
  document.getElementById('analytics-loading').style.display='none';
  document.getElementById('analytics-content').style.display='';

  const ov = data.exam_overview || {};
  document.getElementById('an-count').textContent = ov.count || 0;
  document.getElementById('an-avg-score').textContent = ov.count ? `${ov.avg_score}/${ov.avg_total}` : '--';
  document.getElementById('an-avg-pct').textContent = ov.count ? `${ov.avg_percentage}%` : '--';
  const pr = ov.pass_rate || 0;
  const prEl = document.getElementById('an-pass-rate');
  prEl.textContent = ov.count ? `${pr}%` : '--';
  prEl.className = 'value ' + (pr >= 60 ? 'green' : pr >= 40 ? 'yellow' : 'red');
  document.getElementById('an-med-time').textContent = ov.median_time_secs
    ? _fmtDuration(ov.median_time_secs) : '--';
  document.getElementById('an-viols').textContent =
    (data.violation_summary && data.violation_summary.total) || 0;

  // Score histogram
  const dist = data.score_distribution || [];
  const maxCount = Math.max(1, ...dist.map(d=>d.count));
  const histo = document.getElementById('an-histogram');
  histo.innerHTML = dist.map((d,i)=>{
    const pct = Math.max(2, d.count/maxCount*100);
    const color = i<3 ? 'var(--red)' : i<5 ? 'var(--amber)' : i<8 ? 'var(--accent)' : 'var(--emerald)';
    return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px">
      <span style="font-size:10px;color:var(--text);font-weight:600">${d.count}</span>
      <div style="width:100%;height:${pct}%;min-height:2px;background:${color};border-radius:4px 4px 0 0;transition:height 0.4s"></div>
      <span style="font-size:9px;color:var(--muted);white-space:nowrap">${d.range.replace(/%/g,'')}</span>
    </div>`;
  }).join('');

  // Risk distribution
  const risk = data.risk_distribution || {low:0,medium:0,high:0};
  const riskTotal = Math.max(1, risk.low+risk.medium+risk.high);
  document.getElementById('an-risk-bars').innerHTML = [
    {label:'Low (0-30)',count:risk.low,color:'var(--emerald)'},
    {label:'Medium (31-60)',count:risk.medium,color:'var(--amber)'},
    {label:'High (61-100)',count:risk.high,color:'var(--red)'},
  ].map(r=>{
    const w = Math.max(4, r.count/riskTotal*100);
    return `<div style="flex:1;text-align:center">
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">${r.label}</div>
      <div style="height:32px;background:rgba(255,255,255,0.03);border-radius:6px;overflow:hidden;position:relative">
        <div style="height:100%;width:${w}%;background:${r.color};border-radius:6px;transition:width 0.4s"></div>
      </div>
      <div style="font-size:18px;font-weight:700;color:${r.color};margin-top:6px">${r.count}</div>
    </div>`;
  }).join('');

  // Violation breakdown
  const viol = data.violation_summary || {};
  const byType = viol.by_type || {};
  const vtKeys = Object.keys(byType);
  const violGrid = document.getElementById('an-viol-grid');
  const violNone = document.getElementById('an-viol-none');
  if(vtKeys.length===0){ violGrid.innerHTML=''; violNone.style.display=''; }
  else{
    violNone.style.display='none';
    const maxV = Math.max(1, ...Object.values(byType));
    violGrid.innerHTML = vtKeys.sort((a,b)=>byType[b]-byType[a]).map(k=>{
      const c = byType[k];
      const w = Math.max(8, c/maxV*100);
      return `<div style="background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:8px;padding:12px">
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:6px">${_escHtml(k.replace(/_/g,' '))}</div>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="flex:1;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${w}%;background:var(--red);border-radius:3px"></div>
          </div>
          <span style="font-size:14px;font-weight:700;color:var(--red)">${c}</span>
        </div>
      </div>`;
    }).join('');
  }

  // Question analysis table
  const qa = data.question_analysis || [];
  const qtable = document.getElementById('an-qtable');
  const qnone = document.getElementById('an-qnone');
  if(qa.length===0){ qtable.innerHTML=''; qnone.style.display=''; }
  else{
    qnone.style.display='none';
    qtable.innerHTML = qa.map((q,i)=>{
      const diff = q.difficulty_pct;
      const diffColor = diff>=70 ? 'var(--emerald)' : diff>=40 ? 'var(--amber)' : 'var(--red)';
      const diffLabel = diff>=70 ? 'Easy' : diff>=40 ? 'Medium' : 'Hard';
      const disc = q.discrimination;
      const discColor = disc>=0.3 ? 'var(--emerald)' : disc>=0.1 ? 'var(--amber)' : 'var(--red)';
      const discLabel = disc>=0.3 ? 'Good' : disc>=0.1 ? 'Fair' : 'Poor';
      return `<tr>
        <td style="color:var(--muted)">${i+1}</td>
        <td style="font-size:12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_escHtml(q.question)}</td>
        <td><span style="color:${diffColor};font-weight:600">${diff}%</span>
          <span style="font-size:10px;color:var(--muted);margin-left:4px">${diffLabel}</span></td>
        <td><span style="color:${discColor};font-weight:600">${disc}</span>
          <span style="font-size:10px;color:var(--muted);margin-left:4px">${discLabel}</span></td>
        <td>${q.attempted}</td>
        <td>${q.correct}</td>
      </tr>`;
    }).join('');
  }
}

function _fmtDuration(secs){
  const m = Math.floor(secs/60), s = secs%60;
  return m>0 ? `${m}m ${s}s` : `${s}s`;
}
// ── STUDENT HISTORY TAB ──────────────────────────────────────────
let historyStudents = [];
let historySortKey = 'roll_number';
let historySortAsc = true;
let historySearchQuery = '';
let historyDetailData = null;

function _initTabKeyboard(){
  // Existing tab keyboard navigation — already defined above
}

async function refreshStudentList(){
  try{
    const r = await authFetch(`${BASE}/api/v1/student-search?q=${encodeURIComponent(historySearchQuery)}${_teacherQuery('&')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    historyStudents = (data.students || []).sort(_historyCompare);
    renderHistoryList();
  }catch(e){
    document.getElementById('history-body').innerHTML = '<tr><td colspan="7" class="empty-state">Failed to load: '+_escHtml(e.message)+'</td></tr>';
  }
}

function filterHistorySearch(){
  historySearchQuery = document.getElementById('history-search').value.trim();
  refreshStudentList();
}

function renderHistoryList(){
  const body = document.getElementById('history-body');
  if(!historyStudents.length){
    body.innerHTML = '<tr><td colspan="7" class="empty-state">No students found</td></tr>';
    return;
  }
  body.innerHTML = historyStudents.map(s=>{
    const riskBadge = s.last_exam_risk != null ? _riskBadge(s.last_exam_risk) : '—';
    return `<tr>
      <td style="font-family:var(--font-mono);font-size:13px">${_escHtml(s.roll_number)}</td>
      <td>${_escHtml(s.full_name)}</td>
      <td>${s.total_exams}</td>
      <td>${s.avg_percentage != null ? s.avg_percentage+'%' : '—'}</td>
      <td>${riskBadge}</td>
      <td style="font-size:13px;color:var(--muted)">${_escHtml(s.last_exam_date || '—')}</td>
      <td><button class="btn btn-primary btn-sm" data-action="viewStudentHistory" data-args='${_jsonArgsForAttr(s.roll_number)}'>View History</button></td>
    </tr>`;
  }).join('');
}

function sortHistory(key){
  if(historySortKey===key) historySortAsc=!historySortAsc;
  else{ historySortKey=key; historySortAsc=true; }
  historyStudents.sort(_historyCompare);
  renderHistoryList();
}

function _historyCompare(a,b){
  let va=a[historySortKey], vb=b[historySortKey];
  if(historySortKey==='last_exam_risk'){ va=va??-1; vb=vb??-1; }
  if(historySortKey==='avg_percentage'){ va=va??-1; vb=vb??-1; }
  if(va==null) va=''; if(vb==null) vb='';
  if(typeof va==='number' && typeof vb==='number') return historySortAsc ? va-vb : vb-va;
  const cmp = String(va).localeCompare(String(vb));
  return historySortAsc ? cmp : -cmp;
}

function _riskBadge(score){
  if(score==null) return '—';
  const safeScore = Number(score);
  if(!Number.isFinite(safeScore)) return '—';
  let color, label;
  if(safeScore<=15){ color='var(--emerald)'; label='Low'; }
  else if(safeScore<=40){ color='var(--amber)'; label='Moderate'; }
  else if(safeScore<=70){ color='var(--red)'; label='High'; }
  else{ color='var(--red)'; label='Critical'; }
  return `<span style="color:${color};font-weight:600">${safeScore} (${label})</span>`;
}

async function viewStudentHistory(roll){
  try{
    const r = await authFetch(`${BASE}/api/v1/student-history/${encodeURIComponent(roll)}${_teacherQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    historyDetailData = await r.json();
    renderHistoryDetail();
    document.getElementById('history-detail').style.display='';
    // Hide the student list table while showing detail
    document.querySelector('#panel-history .table-wrap').style.display='none';
    document.querySelector('#panel-history .table-toolbar').style.display='none';
  }catch(e){
    showModal('Failed to load history: '+e.message);
  }
}

function renderHistoryDetail(){
  const d = historyDetailData;
  if(!d) return;

  // Student header stats
  const s = d.student;
  const ag = d.aggregates;
  document.getElementById('history-detail-stats').innerHTML = `
    <div class="stat-tile"><div class="stat-tile-label">Student</div><div class="stat-tile-value" style="font-size:14px">${_escHtml(s.full_name)}</div><div class="stat-tile-sub">${_escHtml(s.roll_number)}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Email</div><div class="stat-tile-value" style="font-size:12px">${_escHtml(s.email||'—')}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Exams Taken</div><div class="stat-tile-value">${ag.total_exams}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Avg Score</div><div class="stat-tile-value">${ag.avg_percentage!=null?ag.avg_percentage+'%':'—'}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Highest</div><div class="stat-tile-value green">${ag.highest_percentage!=null?ag.highest_percentage+'%':'—'}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Lowest</div><div class="stat-tile-value red">${ag.lowest_percentage!=null?ag.lowest_percentage+'%':'—'}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Avg Risk</div><div class="stat-tile-value">${ag.avg_risk_score!=null?ag.avg_risk_score:'—'}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Total Violations</div><div class="stat-tile-value">${ag.total_violations}</div></div>
  `;

  const body = document.getElementById('history-detail-body');
  if(!d.history.length){
    body.innerHTML = '<tr><td colspan="9" class="empty-state">No completed exams</td></tr>';
    return;
  }
  body.innerHTML = d.history.map(h=>{
    const behavList = h.behavioral_patterns.length ? h.behavioral_patterns.map(p=>'<span class="badge" style="background:rgba(220,38,38,0.15);color:var(--red);font-size:10px">'+_escHtml(p.replace(/_/g,' '))+'</span>').join(' ') : '<span style="color:var(--muted)">None</span>';
    const riskStr = h.risk_score != null ? _riskBadge(h.risk_score) : '—';
    const highlights = Array.isArray(h.summary && h.summary.highlights) ? h.summary.highlights : [];
    return `<tr>
      <td>
        <div style="font-weight:600">${_escHtml(h.exam_title||'Exam')}</div>
        <div style="font-size:11px;color:var(--muted);font-family:var(--font-mono)">${_escHtml(h.session_id.slice(0,24))}…</div>
      </td>
      <td>${h.score}/${h.total}</td>
      <td style="font-weight:600;color:${h.percentage>=40?'var(--emerald)':'var(--red)'}">${h.percentage}%</td>
      <td>${h.violation_count}</td>
      <td>${behavList}</td>
      <td>${riskStr}</td>
      <td style="font-size:13px">${_fmtDuration(h.time_taken_secs)}</td>
      <td style="font-size:13px;color:var(--muted)">${_escHtml(h.submitted_at)}</td>
      <td>
        ${highlights.length?`<button class="btn btn-secondary btn-sm" data-action="toggleHistorySummary" data-args='${_jsonArgsForAttr(h.session_id)}' id="summary-btn-${_escHtml(h.session_id)}" title="View AI-generated activity summary">Summary</button>`:''}
        <button class="btn btn-secondary btn-sm" data-action="viewSessionTimeline" data-args='${_jsonArgsForAttr(h.session_id)}'>Timeline</button>
      </td>
    </tr>`;
  }).join('');
}

function closeHistoryDetail(){
  historyDetailData = null;
  document.getElementById('history-detail').style.display='none';
  document.querySelector('#panel-history .table-wrap').style.display='';
  document.querySelector('#panel-history .table-toolbar').style.display='';
}

function viewSession(sid){
  if(!sid) return;
  window.open('/dashboard?session='+encodeURIComponent(sid), '_blank');
}

function viewSessionTimeline(sessionId){
  // Navigate to the forensics timeline view (already exists in dashboard)
  window.location.hash = '#timeline-'+encodeURIComponent(sessionId);
  viewSession(sessionId);
}

function toggleHistorySummary(sessionId){
  let row = document.getElementById('summary-row-'+sessionId);
  if(row){
    row.remove();
    return;
  }
  const h = (historyDetailData.history||[]).find(x=>x.session_id===sessionId);
  if(!h||!h.summary) return;
  const s = h.summary;
  const highlights = Array.isArray(s.highlights) ? s.highlights : [];
  const sevColors={clean:'var(--emerald)',minor:'var(--amber)',concerning:'var(--red)',critical:'var(--red)'};
  const sevBg={clean:'rgba(16,185,129,0.15)',minor:'rgba(245,158,11,0.15)',concerning:'rgba(220,38,38,0.15)',critical:'rgba(220,38,38,0.25)'};
  const sev = s.severity||'clean';
  const tr = document.createElement('tr');
  tr.id = 'summary-row-'+sessionId;
  tr.innerHTML = `<td colspan="9" style="padding:12px 16px;background:rgba(255,255,255,0.02);border-top:1px solid var(--border)">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span style="font-weight:600;font-size:13px">Activity Summary</span>
      <span class="badge" style="background:${sevBg[sev]};color:${sevColors[sev]}">${sev.charAt(0).toUpperCase()+sev.slice(1)}</span>
    </div>
    <div style="font-size:12px;line-height:1.7;color:var(--text-secondary);white-space:pre-line;margin-bottom:8px">${_escHtml(s.narrative)}</div>
    ${highlights.length?'<div style="font-size:12px">'+highlights.map(h=>'<div style="padding:2px 0 2px 16px;position:relative"><span style="position:absolute;left:0;color:'+sevColors[sev]+'">●</span>'+_escHtml(h)+'</div>').join('')+'</div>':''}
  </td>`;
  // Insert after the current row
  const btn = document.getElementById('summary-btn-'+sessionId);
  if(btn){
    const row = btn.closest('tr');
    if(row && row.parentNode) row.parentNode.insertBefore(tr, row.nextSibling);
  }
}

// ── EXAM TEMPLATES ─────────────────────────────────────────────
let templatesData = [];

async function loadTemplates(){
  try{
    const r = await authFetch(`${BASE}/api/v1/templates`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    templatesData = data.templates || [];
    renderTemplates();
    populateTemplateSourceExams();
  }catch(e){
    document.getElementById('templates-empty').textContent = 'Failed to load templates';
  }
}

function populateTemplateSourceExams(){
  const sel = document.getElementById('template-source-exam');
  if(!sel || !examsList) return;
  sel.innerHTML = '<option value="">Select exam to save as template…</option>';
  examsList.forEach(ex=>{
    sel.innerHTML += `<option value="${escAttr(ex.id)}">${_escHtml(ex.exam_title||ex.id)}</option>`;
  });
}

function renderTemplates(){
  const list = document.getElementById('templates-list');
  const empty = document.getElementById('templates-empty');
  if(!templatesData.length){
    list.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  list.innerHTML = templatesData.map(t=>`
    <div style="display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px">
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:13px">${_escHtml(t.template_name)}</div>
        <div style="font-size:11px;color:var(--muted)">${_escHtml(t.exam_title)} · ${t.duration_minutes} min · ${t.questions_count} questions · Created ${_escHtml(t.created_at)}</div>
      </div>
      <button class="btn btn-primary btn-sm" data-action="createExamFromTemplate" data-args='${_jsonArgsForAttr(t.id)}' style="white-space:nowrap;font-size:12px;padding:6px 12px">Create Exam</button>
      <button class="btn btn-secondary btn-sm" data-action="deleteTemplate" data-args='${_jsonArgsForAttr(t.id)}' style="white-space:nowrap;font-size:12px;padding:6px 12px;color:var(--red)">Delete</button>
    </div>
  `).join('');
}

async function saveTemplate(){
  const examId = document.getElementById('template-source-exam').value;
  const name = document.getElementById('template-name-input').value.trim();
  const resultEl = document.getElementById('template-result');
  if(!examId){ resultEl.textContent = 'Select an exam first'; resultEl.style.color = 'var(--red)'; return; }
  if(!name){ resultEl.textContent = 'Enter a template name'; resultEl.style.color = 'var(--red)'; return; }
  resultEl.textContent = 'Saving template…';
  resultEl.style.color = 'var(--text)';
  try{
    const r = await authFetch(`${BASE}/api/v1/templates`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({exam_id: examId, template_name: name, include_questions: true}),
    });
    if(!r.ok) throw new Error((await r.json()).detail || 'Failed');
    const d = await r.json();
    resultEl.textContent = `Template saved! ${d.questions_count} questions included.`;
    resultEl.style.color = 'var(--emerald)';
    document.getElementById('template-name-input').value = '';
    loadTemplates();
  }catch(e){
    resultEl.textContent = 'Error: '+e.message;
    resultEl.style.color = 'var(--red)';
  }
}

async function createExamFromTemplate(templateId){
  const resultEl = document.getElementById('template-result');
  resultEl.textContent = 'Creating exam…';
  resultEl.style.color = 'var(--text)';
  try{
    const r = await authFetch(`${BASE}/api/v1/templates/${encodeURIComponent(templateId)}/create-exam`, {method:'POST'});
    if(!r.ok) throw new Error((await r.json()).detail || 'Failed');
    const d = await r.json();
    resultEl.textContent = `Exam created: "${d.exam_title}" (${d.questions_copied} questions copied). Switching to it…`;
    resultEl.style.color = 'var(--emerald)';
    loadTemplates();
    // Switch to the new exam
    setTimeout(()=>{
      if(typeof onExamSwitch==='function') onExamSwitch(d.exam_id);
    }, 500);
  }catch(e){
    resultEl.textContent = 'Error: '+e.message;
    resultEl.style.color = 'var(--red)';
  }
}

async function deleteTemplate(templateId){
  if(!(await appConfirm('Delete this template? Exams already created from it will not be affected.', 'Delete template', {okText:'Delete'}))) return;
  const resultEl = document.getElementById('template-result');
  resultEl.textContent = 'Deleting…';
  try{
    const r = await authFetch(`${BASE}/api/v1/templates/${encodeURIComponent(templateId)}`, {method:'DELETE'});
    if(!r.ok) throw new Error((await r.json()).detail || 'Failed');
    resultEl.textContent = 'Template deleted.';
    resultEl.style.color = 'var(--emerald)';
    loadTemplates();
  }catch(e){
    resultEl.textContent = 'Error: '+e.message;
    resultEl.style.color = 'var(--red)';
  }
}

// ── REAL-TIME RISK ALERTS ──────────────────────────────────────
let _alertCount = 0;
let _alertMuted = false;

function _getAlertContainer(){
  let c = document.getElementById('alert-toast-container');
  if(!c){
    c = document.createElement('div');
    c.id = 'alert-toast-container';
    c.style.cssText = 'position:fixed;top:80px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:380px;pointer-events:none';
    document.body.appendChild(c);
  }
  return c;
}

function handleRealtimeAlert(a){
  _alertCount++;
  const badge = document.getElementById('live-alert-badge');
  if(badge){
    badge.textContent = _alertCount;
    badge.style.display = _alertMuted ? 'none' : '';
  }
  if(_alertMuted) return;

  try{
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.value = 0.1;
    osc.start(); osc.stop(ctx.currentTime + 0.15);
  }catch(_){}

  const sevColors = {critical:'var(--red)',high:'var(--amber)',medium:'var(--accent)',low:'var(--muted)'};
  const sevBg = {critical:'rgba(220,38,38,0.15)',high:'rgba(245,158,11,0.15)',medium:'rgba(16,185,129,0.15)',low:'rgba(107,114,128,0.15)'};
  const sev = (a.severity||'medium').toLowerCase();
  const color = sevColors[sev]||sevColors.medium;
  const bg = sevBg[sev]||sevBg.medium;
  const container = _getAlertContainer();
  const toast = document.createElement('div');
  const studentLabel = _escHtml(a.full_name||a.roll_number||'Student');
  const violationType = _escHtml((a.violation_type||'').replace(/_/g,' '));
  const details = a.details ? String(a.details) : '';
  const detailsPreview = details.length > 120 ? details.slice(0, 120) + '…' : details;
  const safeSessionId = _escGrp(a.session_id || '');
  toast.style.cssText = 'pointer-events:auto;background:rgba(20,20,30,0.95);backdrop-filter:blur(12px);border:1px solid var(--border);border-left:3px solid '+color+';border-radius:8px;padding:12px 14px';
  toast.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="font-size:14px">⚠️</span>
      <span style="font-weight:600;font-size:13px;color:${color}">${studentLabel}</span>
      <span class="badge" style="background:${bg};color:${color};font-size:10px">${sev}</span>
      <span style="margin-left:auto;font-size:10px;color:var(--muted);cursor:pointer" data-action="_closeToastParent">✕</span>
    </div>
    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">${violationType}</div>
    ${details?'<div style="font-size:11px;color:var(--muted);margin-bottom:6px">'+_escHtml(detailsPreview)+'</div>':''}
    <div style="display:flex;gap:6px">
      <button class="btn btn-secondary btn-sm" style="font-size:11px;padding:4px 10px" data-action="viewSession" data-args='${_jsonArgsForAttr(safeSessionId)}'>View Timeline</button>
    </div>
  `;
  container.appendChild(toast);
  setTimeout(()=>{ if(toast.parentElement) toast.remove(); }, 8000);
}

function toggleAlertMute(){
  _alertMuted = !_alertMuted;
  const btn = document.getElementById('alert-mute-btn');
  const badge = document.getElementById('live-alert-badge');
  if(btn) btn.textContent = _alertMuted ? '🔔 Muted' : '🔔';
  if(badge) badge.style.display = _alertMuted ? 'none' : (_alertCount > 0 ? '' : 'none');
}

function clearAlertBadge(){
  _alertCount = 0;
  const badge = document.getElementById('live-alert-badge');
  if(badge) badge.style.display = 'none';
}

// ── Helper: encode args as JSON for data-args HTML attr ───────────
function _jsonArgsForAttr(){
  return JSON.stringify(Array.from(arguments)).replace(/'/g,'&#x27;');
}

// ── Wrappers for compound/manipulation onclick handlers ──────────
function toggleBankShowImport(){ toggleBank(); showBankImport(); }
function switchTabLiveClearBadge(){ switchTab('live'); clearAlertBadge(); }
// These wrappers used to take `el` as first parameter, but that
// signature would break the 170+ existing data-action handlers from
// the first-pass static-HTML conversion (which expect their JSON
// args directly \u2014 `upgradePlan(planId)` not `upgradePlan(el, planId)`).
// Fix: drop the unused `el` param. Where a wrapper genuinely needs
// the element (only _closeToastParent in this set), it reads `this`
// instead \u2014 the delegated listener below binds the clicked element
// to `this`, matching native inline-onclick semantics.
function _clickQImageInput(i){ document.getElementById('qimg-input-'+i).click(); }
function _showLightbox(screenshot, type, timeStr){ showLightbox(screenshot, type+' \u2014 '+timeStr); }
function _discardGenPreview(){ _genPreview=[]; _renderGenPreview(); document.getElementById('gen-status').textContent=''; }
function _closeToastParent(){ this.closest('div').parentElement.remove(); }
function _focusLoginPwd(){ document.getElementById('login-pwd')?.focus(); }

const _BLOCKED_DELEGATED_ACTIONS = new Set(['close', 'open', 'name', 'blur', 'focus', 'status', 'print', 'alert', 'confirm', 'prompt', 'eval', 'Function', 'fetch']);
function _resolveDelegatedAction(name){
  if(!/^[A-Za-z_$][\w$]*$/.test(name || '') || _BLOCKED_DELEGATED_ACTIONS.has(name)) return null;
  const fn = window[name];
  return typeof fn === 'function' ? fn : null;
}

// ── Delegated listeners replacing inline onclick/onsubmit ────────
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || !el.dataset.action) return;
  if (el.dataset.guardSelf !== undefined && e.target !== el) return;
  if (e.target.closest('a') === el) e.preventDefault();
  const fn = _resolveDelegatedAction(el.dataset.action);
  if (typeof fn !== 'function') return;
  const argsRaw = el.dataset.args || '[]';
  let args = [];
  try { args = JSON.parse(argsRaw); } catch (err) { console.warn('[delegated] invalid data-args', err); }
  // fn.call(el, ...args): bind clicked element to `this` (so wrappers
  // like _closeToastParent can use this.closest(...)) without
  // polluting the positional arg list. Matches native inline-onclick
  // semantics where `this` = the element with the onclick attribute.
  fn.call(el, ...args);
});

document.addEventListener('submit', (e) => {
  const el = e.target.closest('[data-submit]');
  if (!el || !el.dataset.submit) return;
  e.preventDefault();
  const fn = _resolveDelegatedAction(el.dataset.submit);
  if (typeof fn === 'function') fn();
});

// ── Wrappers for onchange handlers ────────────────────────────────
// Called via delegated change listener with this=el.
function _onExamSwitchWrap(){ onExamSwitch(this.value); }
function _loadBankFileWrap(){ loadBankFile(this); }
function _importInviteCsvWrap(){ importInviteCsv({target: this}); }
function _toggleGoogleCourseWrap(){ toggleGoogleCourse(this.dataset.courseId, this.checked); }
function _setQTypeWrap(){ setQType(parseInt(this.dataset.qidx), this.value); }
function _handleQImageUploadWrap(){ handleQImageUpload(parseInt(this.dataset.qidx), this.files[0]); }
function _bankSelectAllWrap(){ _bankSelectAll(this.checked); }
function _bankToggleWrap(){ _bankToggle(this.dataset.qid, this.checked); }

// ── Wrappers for oninput handlers (compound DOM updates) ─────────
function _setQQuestion(){ var i=parseInt(this.dataset.qidx); if(isNaN(i))return; qData[i].question=this.value; markQDirty(); }
function _setQRefAnswer(){ var i=parseInt(this.dataset.qidx); if(isNaN(i))return; qData[i].reference_answer=this.value; markQDirty(); }
function _setQRubric(){ var i=parseInt(this.dataset.qidx); if(isNaN(i))return; qData[i].rubric=this.value; markQDirty(); }
function _setQMaxScore(){ var i=parseInt(this.dataset.qidx); if(isNaN(i))return; qData[i].max_score=parseFloat(this.value)||1; markQDirty(); }
function _setQOption(){ var i=parseInt(this.dataset.qidx); if(isNaN(i)||!this.dataset.okey)return; qData[i].options[this.dataset.okey]=this.value; markQDirty(); }

// ── Wrapper for onerror (this.style.display) ─────────────────────
function _hideSelf(){ this.style.display='none'; }

// ── Delegated change listener ─────────────────────────────────────
document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-change-action]');
  if (!el || !el.dataset.changeAction) return;
  const fn = _resolveDelegatedAction(el.dataset.changeAction);
  if (typeof fn !== 'function') return;
  let args = [];
  try { args = JSON.parse(el.dataset.changeArgs || '[]'); } catch (err) { console.warn('[delegated] invalid data-change-args', err); }
  fn.call(el, ...args);
});

// ── Delegated input listener ─────────────────────────────────────
document.addEventListener('input', (e) => {
  const el = e.target.closest('[data-input-action]');
  if (!el || !el.dataset.inputAction) return;
  const fn = _resolveDelegatedAction(el.dataset.inputAction);
  if (typeof fn !== 'function') return;
  let args = [];
  try { args = JSON.parse(el.dataset.inputArgs || '[]'); } catch (err) { console.warn('[delegated] invalid data-input-args', err); }
  fn.call(el, ...args);
});

// ── Delegated keydown listener ───────────────────────────────────
document.addEventListener('keydown', (e) => {
  const el = e.target.closest('[data-keydown-action]');
  if (!el || !el.dataset.keydownAction) return;
  const wantKey = el.dataset.keydownKey || '';
  if (wantKey && e.key !== wantKey) return;
  const fn = _resolveDelegatedAction(el.dataset.keydownAction);
  if (typeof fn !== 'function') return;
  let args = [];
  try { args = JSON.parse(el.dataset.keydownArgs || '[]'); } catch (err) { console.warn('[delegated] invalid data-keydown-args', err); }
  fn.call(el, ...args);
});

// ── MutationObserver: attach error handlers for data-error-action ─
// onerror doesn't bubble, so we observe the DOM for new <img> elements
// with data-error-action and imperatively attach the handler.
(function(){
  function _bindError(el){
    if(el.__errorBound) return;
    el.__errorBound = true;
    el.addEventListener('error', function(){
      var fn = _resolveDelegatedAction(this.dataset.errorAction);
      if(typeof fn === 'function') fn.call(this);
    });
  }
  // Scan existing
  [].forEach.call(document.querySelectorAll('[data-error-action]'), _bindError);
  // Watch for new elements
  var obs = new MutationObserver(function(ms){
    ms.forEach(function(m){
      [].forEach.call(m.addedNodes, function(n){
        if(n.nodeType===1 && n.dataset && n.dataset.errorAction) _bindError(n);
        if(n.nodeType===1 && n.querySelectorAll){
          [].forEach.call(n.querySelectorAll('[data-error-action]'), _bindError);
        }
      });
    });
  });
  if(document.body) obs.observe(document.body, {childList:true, subtree:true});
})();
