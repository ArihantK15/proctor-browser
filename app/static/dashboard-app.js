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
// Set once doLogout() has run so any in-flight calls that come back 401
// AFTER logout don't try to refresh and recurse forever. Cleared by a
// fresh login (_saveTokens with a real token). Without this guard, the
// dashboard hit an infinite loop on session expiry: poll → 401 →
// refresh-fail → doLogout → logout-call → 401 → refresh → fail →
// doLogout → ... until the rate limiter kicked in at 429.
// Declared at top-level so _saveTokens (defined further down) can
// reference it safely — let-in-TDZ would throw on a typeof check.
let _loggedOut = false;
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
// Restore the previously-selected exam across page refreshes. Without
// this, F5 silently swaps you to examsList[0], and live sessions / results
// / schedule all blank out because they filter by exam_id.
let currentExamId = localStorage.getItem('procta_current_exam') || null;
let currentGroupFilter = '';
let currentBatchFilter = '';
let examsList = [];
let _examsLoaded = false;
let _showArchived = false; // true once loadExams() has run (so the exam bar only shows after)
// Tabs that are NOT scoped to a single exam — the exam selector / +New / Duplicate
// / Delete bar is meaningless clutter on these, so it's hidden (see _syncExamBar).
const _NON_EXAM_TABS = new Set([
  'history', 'org', 'security', 'members', 'billing', 'org-settings',
  'all-orgs', 'issues', 'debug',
]);
function _syncExamBar(tab){
  const bar = document.getElementById('exam-bar');
  if(!bar) return;
  tab = tab || document.querySelector('.tab.active')?.dataset.tab || 'live';
  // Manager-only org admins never author exams, so the exam-management bar
  // (selector + New/Duplicate/Archive/Delete) stays hidden for them even on
  // the oversight tabs they CAN see (live/results/analytics). See applyOrgRole.
  if(currentOrgRole === 'admin'){ bar.style.display = 'none'; return; }
  bar.style.display = (_examsLoaded && !_NON_EXAM_TABS.has(tab)) ? 'flex' : 'none';
}
let _refreshGen = 0; // incremented on exam switch to discard stale responses
let _liveViewSid = null;
let _liveViewLastFrameAt = 0;
let _liveViewFrameTimer = null;
let _liveViewKeepaliveTimer = null;
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
  } else {
    // Fresh login / successful refresh — re-arm the auth flow. Without
    // this, after a previous expiry triggered _loggedOut=true, the next
    // login would have all its authFetch calls bail out at the
    // suppression check and the dashboard would look frozen.
    _loggedOut = false;
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
    // Tolerate a non-JSON body (e.g. a 502/503 HTML error page from the proxy):
    // parsing before the !r.ok check used to throw, masking the real status with
    // a generic parse error.
    const data = await r.json().catch(()=>({}));
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
      throw new Error(_detailText(data, 'Login failed'));
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
      throw new Error(_detailText(d, 'Failed'));
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
  document.body.classList.remove('auth-active');
  document.getElementById('auth-overlay').classList.add('hidden');
  currentTeacherProfile = teacher || null;
  currentIsSolo = !!(teacher && teacher.is_solo);
  // Billing visibility now keys off honest ownership, not role: a solo teacher
  // (org_role='teacher') owns their subscription; an invited teacher doesn't.
  currentIsBillingOwner = !!(teacher && teacher.is_billing_owner);
  _onAuthDone();
  if(teacher && teacher.full_name){
    document.getElementById('teacher-name').textContent = teacher.full_name;
  }
  if(teacher && teacher.id){
    currentTeacherId = teacher.id;
    _shareLinkTeacherId = teacher.id;  // cache for exam-switch refresh
    _populateShareLinks(teacher.id);
  }
  await loadExams();
  refreshAll();
  _startRosterAutoRefresh();
  // Try SSE for real-time updates; fall back to polling if unavailable
  _connectSSE();
  chatConnect();
}

let _sseSource = null;
// The exam_id the live SSE stream is currently scoped to. The filter is baked
// into the EventSource URL at connect time, so it goes stale when the user
// switches exams — we track it here to detect+heal that (see _connectSSE and
// the init/refresh handlers). The poll path reads currentExamId live, so it
// never drifts; this keeps the SSE path in lockstep.
let _sseExamId = null;
let _sseFallbackTimer = null;
let _liveRefreshTimer = null;

/* Toggle the "real-time degraded" banner + Live pulse. 'degraded' means
 * the SSE pub/sub path isn't live (no Redis, or the stream dropped to a
 * polling tick) — monitoring still works, just not instantly. */
function _setRealtimeStatus(state){
  const degraded = state === 'degraded';
  const banner = document.getElementById('realtime-banner');
  if(banner) banner.style.display = degraded ? 'flex' : 'none';
  const status = document.querySelector('#panel-live .panel-status');
  if(status){
    status.classList.toggle('degraded', degraded);
    const label = status.querySelector('.status-label');
    if(label) label.textContent = degraded ? 'Degraded' : 'Streaming';
  }
}

/* Coalesce a burst of SSE 'update' events into one refresh. Trailing-edge
 * throttle: the first update schedules a fetch ~400ms out and further
 * updates within that window are folded in. Light by design — heartbeat
 * storms are already throttled server-side. */
function _debouncedLiveRefresh(){
  if(_liveRefreshTimer) return;
  _liveRefreshTimer = setTimeout(()=>{
    _liveRefreshTimer = null;
    refreshLive();
    refreshIdReviews();
  }, 400);
}

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

    _sseExamId = currentExamId;   // stamp the scope this connection is opened for
    const examParam = currentExamId ? `&exam_id=${encodeURIComponent(currentExamId)}` : '';
    _sseSource = new EventSource(`${BASE}/api/v1/sse/sessions?token=${encodeURIComponent(connect_token)}${examParam}`);

    _sseSource.addEventListener('init', (e)=>{
      try{
        // Stale-scope guard: if the exam changed since this stream opened, this
        // payload is for the wrong exam — drop it and reconnect for the current
        // one (covers any exam-change path that didn't reconnect explicitly).
        if(_sseExamId !== currentExamId){ _connectSSE(); return; }
        const d=JSON.parse(e.data);
        liveData=d.all_sessions||[];
        renderLiveStats(d.sessions||[],liveData);
        renderLive();
        // Server tells us whether the live pub/sub path is actually up.
        // 'degraded' => no reachable Redis, stream is a 5s refresh tick.
        _setRealtimeStatus(d.realtime === 'degraded' ? 'degraded' : 'live');
      }catch(err){ console.error('[SSE] init parse error',err); }
    });

    _sseSource.addEventListener('update', (e)=>{
      // Incremental update — refresh live data from server. Debounced so a
      // burst of updates coalesces into a single fetch (heartbeat storms are
      // already throttled server-side, so this is a light safety net).
      _debouncedLiveRefresh();
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
        // Stale-scope guard (see 'init'): a degraded-mode refresh tick must not
        // overwrite liveData with the previously-selected exam's sessions.
        if(_sseExamId !== currentExamId){ _connectSSE(); return; }
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
      // Stream dropped — we're now on a polling tick, so monitoring is degraded.
      _setRealtimeStatus('degraded');
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
    _setRealtimeStatus('degraded');
    _sseFallbackTimer = setInterval(()=>{ refreshLive(); refreshIdReviews(); }, 5000);
    // Retry SSE after 30s
    setTimeout(()=>{
      if(_sseFallbackTimer){
        clearInterval(_sseFallbackTimer);
        _sseFallbackTimer=null;
        _connectSSE();
      }
    }, 30000);
  }
}

// ── EXAM SELECTOR ──────────────────────────────────────────────
async function loadExams(){
  try{
    const query = _teacherQuery('?');
    const includeArchived = _showArchived ? 'include_archived=1' : '';
    const sep = query ? '&' : '?';
    const r = await authFetch(`${BASE}/api/v1/admin/exams${query}${includeArchived ? sep + includeArchived : ''}`);
    if(!r.ok){
      _examsLoaded = true;
      _syncExamBar();
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
      // textContent is inherently XSS-safe (no HTML parsing), so the title
      // must NOT be passed through _escHtml — that would double-encode and
      // display literal "&amp;"/"&lt;" for titles containing & < > " '.
      opt.textContent = `${ex.exam_title || 'Untitled'} (${ex.question_count}Q, ${ex.session_count} sessions)` + (ex.archived_at ? ' [Archived]' : '');
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
    _examsLoaded = true;
    _syncExamBar();
    document.getElementById('exam-count').textContent = `${examsList.length} exam${examsList.length!==1?'s':''}`;
    _updateArchiveButtons();
    document.getElementById('delete-exam-btn').style.display = examsList.length > 1 ? '' : 'none';
    document.getElementById('duplicate-exam-btn').style.display = currentExamId ? '' : 'none';
  }catch(e){ console.error('loadExams', e); }
}

function onExamSwitch(examId){
  currentExamId = examId;
  try{ localStorage.setItem('procta_current_exam', examId || ''); }catch(_){}
  _updateArchiveButtons();
  document.getElementById('delete-exam-btn').style.display = examsList.length > 1 ? '' : 'none';
  document.getElementById('duplicate-exam-btn').style.display = currentExamId ? '' : 'none';
  // Refresh the Share-link so /register?t=...&e=<new-exam> stays in
  // sync with the selected exam. Otherwise a student clicking the
  // link gets enrolled for the wrong exam.
  if (_shareLinkTeacherId) _populateShareLinks(_shareLinkTeacherId);
  // Reset data and reload everything for the new exam
  liveData = []; resultsData = []; qData = [];
  // Re-scope the live SSE stream to the new exam. The stream's exam_id is fixed
  // at connect time, so without this it keeps pushing the PREVIOUS exam's
  // sessions (the bug where live filtering ignored the selected exam). The poll
  // path below already reads currentExamId live; this keeps the stream in sync.
  if(typeof _connectSSE === 'function') _connectSSE();
  _reloadQuestionsIfActive();
  refreshAll();
  // refreshAll() only covers a fixed set (live/results/tools/invites). The
  // currently active tab may be outside that set (analytics, history, org,
  // members, billing, security, …) — re-run its loader so the visible panel
  // reflects the newly selected exam instead of staying stale until the user
  // manually toggles tabs.
  const _activeTabBtn = document.querySelector('.tab.active');
  const _activeTab = _activeTabBtn && _activeTabBtn.dataset ? _activeTabBtn.dataset.tab : null;
  if(_activeTab) _dispatchTabLoad(_activeTab);
  reloadExtensions();
}

function _examQuery(sep){
  const params = [];
  if(currentExamId) params.push(`exam_id=${encodeURIComponent(currentExamId)}`);
  if(currentTeacherFilter) params.push(`teacher_id=${encodeURIComponent(currentTeacherFilter)}`);
  if(currentGroupFilter) params.push(`group_id=${encodeURIComponent(currentGroupFilter)}`);
  if(currentBatchFilter) params.push(`batch=${encodeURIComponent(currentBatchFilter)}`);
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
  const dur = parseInt(document.getElementById('new-exam-duration').value, 10) || 60;
  const phoneCam = document.getElementById('new-exam-phone-cam').checked;
  if(!title){ document.getElementById('create-exam-err').textContent='Title is required'; return; }
  const btn = document.getElementById('create-exam-btn');
  btn.disabled = true; btn.textContent = 'Creating...';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams`,{
      method:'POST', body:JSON.stringify({exam_title:title, duration_minutes:dur, phone_camera:phoneCam})
    });
    if(!r.ok){ const d=await r.json(); throw new Error(_detailText(d, 'Failed')); }
    const d = await r.json();
    hideCreateExamModal();
    currentExamId = d.exam_id;
    await loadExams();
    liveData = []; resultsData = []; qData = [];
    // A brand-new exam has no questions — resync the editor so it clears
    // the previous exam's title + question cards instead of showing stale
    // data (the dropdown switched but the editor wouldn't reload on its own).
    _reloadQuestionsIfActive();
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
    let reauth_token;
    try { reauth_token = await _getReauthToken('delete this exam'); }
    catch(e){ alert(e.message || 'Re-authentication failed'); return; }
    if(!reauth_token) return;
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${currentExamId}`, {
      method:'DELETE',
      headers:{'X-Reauth-Token': reauth_token}
    });
    if(!r.ok){ const d=await r.json(); throw new Error(_detailText(d, 'Failed')); }
    currentExamId = null;
    await loadExams();
    liveData = []; resultsData = []; qData = [];
    _reloadQuestionsIfActive();
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
    if (!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
    // Switch to the new exam immediately so the teacher lands in
    // editing mode on their fresh copy.
    currentExamId = d.exam_id;
    try { localStorage.setItem('procta_current_exam', d.exam_id || ''); } catch(_) {}
    await loadExams();
    const sel = document.getElementById('exam-select');
    if (sel) sel.value = d.exam_id;
    liveData = []; resultsData = []; qData = [];
    _reloadQuestionsIfActive();
    refreshAll();
    showModal(`Created "${d.exam_title}" with ${d.questions_copied} question(s).`);
  } catch (e) {
    showModal('Duplicate failed: ' + e.message);
  }
}

function _updateArchiveButtons(){
  const ex = examsList.find(e => e.exam_id === currentExamId);
  const isArchived = !!(ex && ex.archived_at);
  document.getElementById('archive-exam-btn').style.display = (!isArchived && currentExamId) ? '' : 'none';
  document.getElementById('unarchive-exam-btn').style.display = (isArchived && currentExamId) ? '' : 'none';
}

function toggleShowArchived(){
  _showArchived = document.getElementById('show-archived-input').checked;
  // Re-load with the new filter; preserve the selected exam if it's still visible.
  loadExams();
}

async function archiveCurrentExam(){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  const ex = examsList.find(e => e.exam_id === currentExamId);
  const name = ex ? ex.exam_title : 'this exam';
  if(!(await appConfirm(`Archive "${name}"? It will be hidden from the exam list and students won't be able to start it. Live sessions are unaffected.`, 'Archive exam', {okText:'Archive'}))) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(currentExamId)}/archive`, {method:'POST'});
    if(!r.ok){ const d=await r.json(); throw new Error(_detailText(d, 'Failed')); }
    await loadExams();
  }catch(e){ showModal('Archive failed: '+e.message); }
}

async function unarchiveCurrentExam(){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  const ex = examsList.find(e => e.exam_id === currentExamId);
  const name = ex ? ex.exam_title : 'this exam';
  if(!(await appConfirm(`Unarchive "${name}"? It will reappear in the exam list and students can start it again.`, 'Unarchive exam', {okText:'Unarchive'}))) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(currentExamId)}/unarchive`, {method:'POST'});
    if(!r.ok){ const d=await r.json(); throw new Error(_detailText(d, 'Failed')); }
    await loadExams();
  }catch(e){ showModal('Unarchive failed: '+e.message); }
}

async function _tryAutoLogin(){
  try{
    // Cookie auth is primary. Bearer is only kept for one-shot OAuth/LTI
    // fragments before the backend sets HttpOnly cookies.
    let r = await fetchWithTimeout(`${BASE}/api/v1/auth/me`, {
      credentials:'include',
      headers: authToken ? {'Authorization':'Bearer '+authToken} : {},
    });
    if(r.ok){
      const me = await r.json().catch(()=>null);
      if(me){ await _ensureCsrfToken(true); _onAuthed(me); return; }
    }

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
  // Idempotent. authFetch's 401-catch can call this for several
  // racing in-flight requests at once; without the guard we'd fire
  // a logout POST per failure (the duplicated "logout 401" lines in
  // the user-reported console were each a separate redundant call).
  if (_loggedOut) return;
  // Set the suppression flag FIRST so any in-flight 401 responses that
  // race in while we're tearing down can't trigger _refreshTokens()
  // (which would recurse back into doLogout via authFetch's catch).
  _loggedOut = true;
  // Cancel periodic pollers BEFORE the logout call so they can't
  // schedule another auth round-trip mid-teardown.
  if(autoRefreshTimer){ clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  if(_sseSource){ try{_sseSource.close();}catch(_){} _sseSource=null; }
  if(_sseFallbackTimer){ clearInterval(_sseFallbackTimer); _sseFallbackTimer=null; }
  // Plain fetch — NOT authFetch. Using authFetch here was the source of
  // the dashboard's "POST /auth/logout 401 → /auth/refresh 401 → /auth/logout
  // 401 → … → 429" infinite loop reported on session expiry. Logout
  // must be fire-and-forget; we don't care if the server says 401 (we're
  // already logged out from its perspective).
  try{
    const headers = {};
    if(authToken) headers.Authorization = 'Bearer '+authToken;
    const csrf = _getCsrfToken();
    if(csrf) headers['X-CSRF-Token'] = csrf;
    await fetchWithTimeout(`${BASE}/api/v1/auth/logout`, {
      method:'POST', credentials:'include', headers,
    });
  }catch(_){}
  _saveTokens('','');
  // Wipe in-memory state so a second teacher logging in on the same
  // browser never sees the previous teacher's data even momentarily.
  try{
    if(typeof liveData    !== 'undefined') liveData    = [];
    if(typeof resultsData !== 'undefined') resultsData = [];
    // Student-history view was leaking across accounts: its cached array +
    // table body weren't cleared on logout, so the next teacher saw the
    // previous teacher's students until they switched tabs / reloaded.
    if(typeof historyStudents    !== 'undefined') historyStudents    = [];
    if(typeof historyDetailData  !== 'undefined') historyDetailData  = null;
    if(typeof currentSessionId !== 'undefined') currentSessionId = null;
    currentTeacherProfile = null;
    currentExamId = null; examsList = [];
    try{ localStorage.removeItem('procta_current_exam'); }catch(_){}
    document.querySelectorAll('#live-body, #results-body, #history-body').forEach(el=>el.innerHTML='');
    const _rvBody = document.getElementById('review-body'); if(_rvBody) _rvBody.innerHTML='';
  }catch(_){}
  chatDisconnect();
  document.body.classList.add('auth-active');
  document.getElementById('auth-overlay').classList.remove('hidden');
  document.getElementById('teacher-name').textContent = '';
  _examsLoaded = false;
  document.getElementById('exam-bar').style.display = 'none';
  // Clear the login form so the previous user's email/password aren't left
  // sitting in the fields after logout.
  ['login-email','login-pwd','login-2fa-code'].forEach(id=>{
    const el = document.getElementById(id); if(el) el.value = '';
  });
  const otpRow = document.getElementById('login-2fa-row'); if(otpRow) otpRow.style.display = 'none';
  toggleAuthForm('login');
}

// The share-link is teacher_id + (when an exam is selected) exam_id.
// Without the exam_id, students who register via the link get added
// to the teacher's roster but with NO exam association — the lobby
// then picks "the teacher's first exam_config" which may be the
// wrong one. User reported this on demo prep: registration succeeded
// but the wrong exam (or none) showed up in the student dashboard.
// Re-call this on every currentExamId change (exam-bar selector) so
// the displayed link always matches the currently-selected exam.
function _populateShareLinks(teacherId){
  const base = location.origin;
  let url = `${base}/register?t=${teacherId}`;
  if (typeof currentExamId !== 'undefined' && currentExamId) {
    url += `&e=${encodeURIComponent(currentExamId)}`;
  }
  const el = document.getElementById('share-register-link');
  if (el) el.value = url;
  const dl = document.getElementById('share-download-link');
  if (dl) dl.value = `${base}/download`;
}

// Capture the current teacher_id once it's known so we can refresh
// the share-link whenever the selected exam changes — without forcing
// callers to thread teacher_id through every selector change handler.
let _shareLinkTeacherId = '';

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
// _loggedOut is declared higher in the file (near refreshToken) so it's
// safe to read from _saveTokens without TDZ surprises during the
// top-level token-restore path.
async function _refreshTokens(){
  if(_loggedOut) throw new Error('logged out — refresh suppressed');
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
    _loggedOut = false;  // re-armed by a successful refresh
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
  // Let the browser set the multipart boundary for file uploads — a
  // forced application/json header would corrupt the request body.
  if(opts.body instanceof FormData) delete opts.headers['Content-Type'];
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
_loadPublicConfig().then(() => _initTurnstile()).catch(()=>{});

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
      if(auth.classList.contains('hidden')){
        clearInterval(probe);
        onboardOpen();
      }
    }, 500);
    return;
  }
  onboardOpen();
}

/* ── Theme switch ──────────────────────────────────────────────────
 * tokens.css ships three themes via [data-theme] on <html>. _safe.js
 * stamps the saved choice before first paint; setTheme() is the live
 * switch wired to the topbar buttons via data-action delegation. The
 * choice is persisted to localStorage('procta_theme') (same key _safe.js
 * reads) so it survives reloads and applies flash-free next time. */
var _THEMES = ['dark', 'dark-oled', 'light'];
function setTheme(name){
  if(_THEMES.indexOf(name) === -1) name = 'dark';
  document.documentElement.setAttribute('data-theme', name);
  try { localStorage.setItem('procta_theme', name); } catch(_){}
  _syncThemeSwitch();
}
function _syncThemeSwitch(){
  var cur = document.documentElement.getAttribute('data-theme') || 'dark';
  document.querySelectorAll('.theme-opt').forEach(function(b){
    var on = b.getAttribute('data-theme-opt') === cur;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

/* ── Tools two-pane category switch ────────────────────────────────
 * Each .tool-card carries data-section; the left rail items carry the
 * same. showToolsSection() hides cards outside the active section and
 * highlights the matching rail item. The choice is remembered so the
 * teacher returns to the same category. */
var _TOOLS_SECTIONS = ['students', 'exam', 'integrations', 'maintenance', 'danger'];
function showToolsSection(name){
  if(_TOOLS_SECTIONS.indexOf(name) === -1) name = 'students';
  document.querySelectorAll('.tool-card[data-section]').forEach(function(c){
    c.classList.toggle('hidden', c.getAttribute('data-section') !== name);
  });
  document.querySelectorAll('.settings-nav-item').forEach(function(b){
    var on = b.getAttribute('data-section') === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  try { localStorage.setItem('procta_tools_section', name); } catch(_){}
}
function _restoreToolsSection(){
  var saved = 'students';
  try { saved = localStorage.getItem('procta_tools_section') || 'students'; } catch(_){}
  showToolsSection(saved);
}

// The "?" help button now lives as static markup in the topbar
// (data-action="onboardOpen") instead of being injected here — the old
// code queried a non-existent .topbar-right and fell back to .topbar,
// landing the button top-left where it looked broken.
document.addEventListener('DOMContentLoaded', () => {
  // Reflect the active theme (stamped on <html> by _safe.js before paint)
  // onto the topbar switch, and the saved Tools category onto the rail.
  _syncThemeSwitch();
  _restoreToolsSection();
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
  const btn = document.querySelector('.tab[data-tab="' + tab + '"]');
  // Never route (via #tab- hash or keyboard) into a tab the current role
  // can't see. Closes the hash-deeplink path to superadmin panels for a
  // non-superadmin (e.g. #tab-all-orgs typed into the URL).
  if(!btn || btn.style.display === 'none') return null;
  return btn;
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
  _syncExamBar(tab);   // hide the exam selector/management bar on non-exam-scoped tabs
  _dispatchTabLoad(tab);
  // Persist tab in URL hash so refresh doesn't lose state
  if (window.location.hash !== '#tab-' + tab) {
    history.replaceState(null, '', '#tab-' + tab);
  }
}

// Per-tab data loaders. Single source of truth shared by switchTab (when the
// user opens a tab) and onExamSwitch (so the ACTIVE tab reloads for the newly
// selected exam — otherwise tabs outside refreshAll()'s fixed set, e.g.
// analytics/history/org/members/billing/security, show the previous exam's
// data until the user manually toggles tabs). Loaders that guard on an empty
// data array reload after onExamSwitch resets those arrays.
function _dispatchTabLoad(tab){
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
     try{ if(typeof loadSensitivity==='function') loadSensitivity(); }catch(_){}
     try{ if(typeof loadAudioKeywords==='function') loadAudioKeywords(); }catch(_){}
     try{ if(typeof loadTemplates==='function') loadTemplates(); }catch(_){}
     try{ if(typeof loadGoogleClassroom==='function') loadGoogleClassroom(); }catch(_){}
   }
  if(tab==='org') loadOrgOverview();
  if(tab==='security') loadSecurity();
  if(tab==='profile') loadProfile();
  if(tab==='members') loadMembers();
  if(tab==='billing') loadBilling();
  if(tab==='org-settings') loadOrgSettings();
  if(tab==='all-orgs') loadAllOrgs();
  if(tab==='issues') loadIssues();
  if(tab==='review'){ loadReview(); loadAppeals(); }
  if(tab==='privacy'){ const el=document.getElementById('sar-result'); if(el) el.textContent=''; }
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
let currentIsSolo = false;  // solo account → force pure-teacher view (spec §B)
let currentIsBillingOwner = false;  // owns the org subscription (gates Billing)

function decodeJWT(token){
  try{ return JSON.parse(atob(token.split('.')[1])); }catch(e){ return null; }
}

function applyOrgRole(org_role){
  const requested = org_role || 'teacher';
  // The org_role from /login + /me is honest now (account-types, phase135):
  // a solo teacher and an invited teacher are both 'teacher'; an org admin is
  // a manager-only 'admin'. No more solo-downgrade override — the server tells
  // us the real role and we render it directly.
  currentOrgRole = requested;
  // Tabs use inline `style.display = ''` / 'none' since they belong to a
  // flex row. Other role-gated elements (teacher-filter dropdowns,
  // analytics filter row) get the same treatment so admin-only UI
  // appears/disappears uniformly.
  document.querySelectorAll('[data-roles]').forEach(el => {
    const roles = (el.dataset.roles || '').split(' ');
    el.style.display = roles.includes(currentOrgRole) ? '' : 'none';
  });
  // Manager-only admin (account-types, phase135): an org admin owns billing +
  // members + oversight but must NOT author exams. Hide every exam-authoring
  // surface tagged data-hide-for-admin (Questions / Tools / Review / Chat tabs
  // + the exam-management bar) when the role is admin. This runs AFTER the
  // data-roles pass so a data-roles="teacher admin" element (e.g. Review) that
  // the reveal logic just un-hid is re-hidden for admin. Live Sessions /
  // Results / Student History / Analytics carry NO data-hide-for-admin, so
  // admins keep their read-only oversight of them. Superadmin is unaffected:
  // those authoring tabs already carry data-roles excluding superadmin.
  if(currentOrgRole === 'admin'){
    document.querySelectorAll('[data-hide-for-admin]').forEach(el => {
      el.style.display = 'none';
    });
  }
  // Surface the pending-appeals count on the Review tab as soon as the role
  // (and thus the tab) is known, so teachers notice disputes without opening it.
  try{ if(typeof refreshAppealsBadge==='function') refreshAppealsBadge(); }catch(_){}
  // Billing is gated on honest ownership (organizations.owner_teacher_id),
  // NOT role: a self-signup solo teacher (org_role='teacher') IS the billing
  // owner and must see/manage their own subscription; an org admin owner sees
  // it too; an invited teacher (also org_role='teacher') never does. The
  // server resolves this into teacher.is_billing_owner, captured in _onAuthed.
  document.querySelectorAll('[data-billing-owner]').forEach(el => {
    el.style.display = currentIsBillingOwner ? '' : 'none';
  });
  // Hard-gate founder-internal tooling (all-orgs / issues / debug). These
  // tabs carry data-roles="superadmin", so the forEach above already sets
  // them to display:none for any non-superadmin and back to visible for a
  // superadmin — reversibly, within the same page session (a later super
  // admin login restores them without a reload). We deliberately do NOT
  // .remove() them: a hidden tab can't be clicked, and _tabButtonForName()
  // returns null for a display:none tab, so the #tab- hash-deeplink path is
  // already closed. Their panels only become visible via switchTab(), which
  // is reachable only through those same guarded entry points.
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
  applyOrgRole(role || 'teacher');
  // Card-on-signup gate (no-op unless CARD_ON_SIGNUP_ENFORCED is on server-side).
  checkOnboardingGate();
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

async function applyTeacherFilter(source){
  const sel = document.getElementById(`${source}-teacher-filter`);
  currentTeacherFilter = sel ? sel.value : '';
  document.querySelectorAll('.teacher-filter').forEach(other => { other.value = currentTeacherFilter; });
  _analyticsCache = {};
  // Teacher-first: re-scope the exam selector to the chosen teacher. If the
  // currently-selected exam doesn't belong to them, loadExams() falls back to
  // that teacher's first exam, so the view below renders coherent data.
  try{ await loadExams(); }catch(_){}
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
  try{ if(typeof loadOrgLiveMonitor==='function') loadOrgLiveMonitor(); }catch(_){}
}

// Org-wide live monitor (ported from the dropped React LiveMonitor). Lists
// every in-progress session across the org via /api/v1/admin/live-monitor,
// which scopes results by the caller's role (admin → org-wide).
async function loadOrgLiveMonitor(){
  const body = document.getElementById('org-live-body');
  const countEl = document.getElementById('org-live-count');
  if(!body) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/live-monitor`);
    if(!r.ok){ body.textContent = 'Could not load live sessions.'; return; }
    const d = await r.json();
    const sessions = Array.isArray(d.sessions) ? d.sessions : [];
    if(countEl) countEl.textContent = sessions.length ? `(${sessions.length})` : '';
    if(!sessions.length){ body.textContent = 'No exams in progress right now.'; return; }
    const now = Date.now();
    const rows = sessions.map(s=>{
      const name = _escHtml(s.full_name || s.roll_number || s.email || '—');
      const risk = (s.risk_score != null) ? Math.round(s.risk_score) : '—';
      const riskColor = (s.risk_score != null && s.risk_score >= 60) ? 'var(--red)' : (s.risk_score != null && s.risk_score >= 30) ? 'var(--amber)' : 'var(--muted)';
      let since = '';
      if(s.started_at){
        const mins = Math.max(0, Math.floor((now - new Date(s.started_at).getTime())/60000));
        since = isFinite(mins) ? `${mins}m` : '';
      }
      const viol = s.latest_violation ? _escHtml(s.latest_violation) : '—';
      return `<tr>
        <td style="padding:6px 10px">${name}</td>
        <td style="padding:6px 10px;color:var(--muted)">${_escHtml(s.exam_id || '—')}</td>
        <td style="padding:6px 10px;color:${riskColor};font-weight:600">${risk}</td>
        <td style="padding:6px 10px;color:var(--muted)">${viol}</td>
        <td style="padding:6px 10px;color:var(--muted)">${since}</td>
      </tr>`;
    }).join('');
    body.innerHTML = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase">
        <th style="padding:6px 10px">Student</th><th style="padding:6px 10px">Exam</th>
        <th style="padding:6px 10px">Risk</th><th style="padding:6px 10px">Last flag</th>
        <th style="padding:6px 10px">Elapsed</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }catch(e){ body.textContent = 'Network error loading live sessions.'; }
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

// Module-level cache of the org members list so the teacher-transfer modal
// can populate its target <select> from the SAME data loadMembers() rendered
// (the receiving teacher must already be in the org).
let _membersData = [];

async function loadMembers(){
  try{
    const r = await authFetch(`${BASE}/api/v1/org/members`);
    if(!r.ok) return;
    const d = await r.json();
    const tbody = document.getElementById('members-tbody');
    const members = d.members || [];
    _membersData = members;
    const countEl = document.getElementById('members-count');
    if(countEl) countEl.textContent = String(members.length);
    tbody.innerHTML = members.map(m => {
      let actions = '';
      if(m.org_role==='teacher'){
        // Only teachers author teaching data, so only they can be offboarded.
        actions = `<button class="btn btn-secondary btn-sm" style="font-size:11px;padding:4px 8px;margin-right:6px" data-action="openTeacherTransferModal" data-args='${_jsonArgsForAttr(m.id)}'>Transfer data / Offboard</button>`
          + `<button class="btn btn-secondary btn-sm" style="color:var(--red);font-size:11px;padding:4px 8px" data-action="removeOrgMember" data-args='${_jsonArgsForAttr(m.id)}'>Remove</button>`;
      }
      return `
      <tr>
        <td>${_escHtml(m.full_name||'--')}</td>
        <td>${_escHtml(m.email)}</td>
        <td>${_escHtml(m.org_role)}</td>
        <td>${m.created_at||'--'}</td>
        <td style="white-space:nowrap">${actions}</td>
      </tr>
    `;
    }).join('');
  }catch(_){}
}

// ── Teacher reassign / offboarding (admin-only) ──────────────────
// Moves ALL of one teacher's teaching data (exams, students, sessions,
// analytics) to another teacher who is ALREADY in the org. Backend:
// POST /api/v1/admin/teachers/{fromId}/reassign { to_teacher_id }.
function openTeacherTransferModal(fromId){
  if(!fromId) return;
  const from = (_membersData || []).find(m => m.id === fromId);
  const fromName = from ? (from.full_name || from.email || 'this teacher') : 'this teacher';
  // Eligible targets: every OTHER member that is a teacher (exclude the
  // from-teacher and any admins — you don't hand teaching data to an admin).
  const targets = (_membersData || []).filter(m => m.id !== fromId && m.org_role === 'teacher');

  const els = _appModalEls();
  if(!els.overlay || !els.title || !els.body || !els.ok || !els.cancel){ return; }
  if(_appDialogResolve) _appDialogResolve(null);
  _appDialogMode = 'teacher_transfer';
  els.title.textContent = 'Transfer data / Offboard';
  els.body.innerHTML = '';

  const intro = document.createElement('div');
  intro.style.cssText = 'color:var(--text-muted);font-size:13px;line-height:1.5;margin-bottom:14px';
  intro.textContent = `This moves ALL of ${fromName}'s exams, students, sessions, and analytics to the selected teacher. `
    + 'The receiving teacher must already be in your organization. This cannot be undone.';
  els.body.appendChild(intro);

  if(!targets.length){
    const none = document.createElement('div');
    none.style.cssText = 'color:var(--amber, #fbbf24);font-size:13px';
    none.textContent = 'There is no other teacher in this organization to receive the data. Invite a teacher first.';
    els.body.appendChild(none);
    els.ok.textContent = 'Transfer';
    els.ok.disabled = true;
    els.cancel.textContent = 'Close';
    els.cancel.style.display = '';
    els.overlay.style.display = 'flex';
    els.body._transferState = { fromId, getTarget: () => '' };
    return new Promise(resolve => { _appDialogResolve = resolve; });
  }

  const label = document.createElement('div');
  label.style.cssText = 'font-size:12px;color:var(--text-muted);margin-bottom:6px';
  label.textContent = 'Receiving teacher';
  els.body.appendChild(label);

  const select = document.createElement('select');
  select.id = 'teacher-transfer-target';
  select.style.cssText = 'width:100%;background:rgba(255,255,255,.04);border:1px solid var(--border);'
    + 'border-radius:10px;color:var(--text);padding:10px 12px;font-size:13px;outline:none;box-sizing:border-box';
  targets.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.id;
    opt.textContent = (t.full_name ? t.full_name + ' — ' : '') + (t.email || t.id);
    select.appendChild(opt);
  });
  els.body.appendChild(select);

  els.ok.textContent = 'Transfer data';
  els.ok.disabled = false;
  els.cancel.textContent = 'Cancel';
  els.cancel.style.display = '';
  els.overlay.style.display = 'flex';
  setTimeout(() => select.focus(), 0);
  els.body._transferState = { fromId, getTarget: () => select.value };
  return new Promise(resolve => { _appDialogResolve = resolve; });
}

async function _submitTeacherTransfer(fromId, toId){
  if(!fromId || !toId) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/teachers/${encodeURIComponent(fromId)}/reassign`, {
      method: 'POST',
      body: JSON.stringify({ to_teacher_id: toId })
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      showModal('Transfer failed', _detailText(d, 'Could not transfer teaching data.'));
      return;
    }
    const d = await r.json().catch(()=>({}));
    const counts = (d && d.counts) || d || {};
    // showModal renders its body as plain text (textContent), so build a
    // newline-separated summary rather than HTML.
    const movedKeys = Object.keys(counts).filter(k => typeof counts[k] === 'number');
    const total = movedKeys.reduce((a, k) => a + counts[k], 0);
    // showModal renders body as collapsed text, so keep the per-table
    // breakdown on a single readable line (table=rows · table=rows · …).
    const parts = movedKeys.sort().map(k => `${k}=${counts[k]}`).join(' · ');
    const summary = movedKeys.length
      ? `Moved ${total} row${total===1?'':'s'} across ${movedKeys.length} table${movedKeys.length===1?'':'s'}. ${parts}`
      : 'Teaching data transferred successfully.';
    await showModal('Transfer complete', summary);
    loadMembers();
  }catch(e){
    showModal('Transfer failed', (e && e.message) || 'Network error during transfer.');
  }
}

// ── ALL ORGS (superadmin) ──────────────────────────────────────
async function loadAllOrgs(){
  try{
    const r = await authFetch(BASE + '/api/v1/admin/all-orgs');
    if(!r.ok) return;
    const d = await r.json();
    const tbody = document.getElementById('all-orgs-tbody');
    const countEl = document.getElementById('all-orgs-count');
    const orgs = d.orgs || [];
    if(countEl) countEl.textContent = String(orgs.length);
    tbody.innerHTML = orgs.map(o => {
      const ovrd = o.max_students_override != null ? String(o.max_students_override) : '—';
      const cr = o.billing_credit_inr != null ? '₹' + String(o.billing_credit_inr) : '₹0';
      return '<tr>' +
        '<td>' + _escHtml(o.name||'') + '</td>' +
        '<td>' + (o.teacher_count||0) + '</td>' +
        '<td>' + (o.student_count||0) + '/' + _escHtml(String(o.max_students||'')) + '</td>' +
        '<td>' + _escHtml(o.plan||'') + '</td>' +
        '<td>' + _escHtml(o.status||'') + '</td>' +
        '<td>' + ovrd + '</td>' +
        '<td>' + cr + '</td>' +
        '<td style="white-space:nowrap">' +
          '<button class="btn btn-secondary btn-sm" style="font-size:10px;padding:3px 6px;margin-right:4px" data-action="setCapOverride" data-args=\'' + _jsonArgsForAttr(o.id) + '\'>Cap</button>' +
          '<button class="btn btn-secondary btn-sm" style="font-size:10px;padding:3px 6px" data-action="grantOrgCredit" data-args=\'' + _jsonArgsForAttr(o.id) + '\'>Credit</button>' +
        '</td>' +
        '<td>' + (o.created_at||'') + '</td>' +
        '</tr>';
    }).join('');
  }catch(_){}
}

async function setCapOverride(orgId){
  const raw = prompt('Enter student cap override (number, or blank to clear):');
  if(raw === null) return;
  const val = raw.trim() === '' ? null : parseInt(raw.trim(), 10);
  if(raw.trim() !== '' && (isNaN(val) || val < 0 || val > 100000)){
    alert('Must be 0–100000, or blank to clear.');
    return;
  }
  try{
    const r = await authFetch(BASE + '/api/v1/admin/orgs/' + encodeURIComponent(orgId) + '/limit-override', {
      method: 'POST',
      body: JSON.stringify({max_students_override: val})
    });
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Request failed')); }
    loadAllOrgs();
  }catch(e){ alert('Set cap failed: ' + e.message); }
}

async function grantOrgCredit(orgId){
  const rawAmt = prompt('Enter credit amount in INR (positive=grant, negative=deduct):');
  if(rawAmt === null) return;
  const amt = parseInt(rawAmt.trim(), 10);
  if(isNaN(amt)){ alert('Invalid amount.'); return; }
  const reason = prompt('Reason for this credit adjustment:');
  if(!reason || reason.trim() === ''){ alert('Reason is required.'); return; }
  try{
    const r = await authFetch(BASE + '/api/v1/admin/orgs/' + encodeURIComponent(orgId) + '/credit', {
      method: 'POST',
      body: JSON.stringify({amount_inr: amt, reason: reason.trim()})
    });
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Request failed')); }
    loadAllOrgs();
  }catch(e){ alert('Grant credit failed: ' + e.message); }
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
  // The onboarding gate reuses this modal as a blocking overlay — can't be
  // dismissed until the billing owner sets up a payment method.
  if(window._onboardingGateActive) return;
  document.getElementById('upgrade-modal').classList.add('hidden');
}

// Card-on-signup onboarding gate. For a billing-owner admin whose subscription
// is still 'created' (no payment mandate), show the plan picker as a blocking
// overlay so they choose a plan + set up payment before using the product.
// /billing/onboarding-status returns needs_payment_setup=false unless
// CARD_ON_SIGNUP_ENFORCED is on, so this is dormant until that flag flips.
async function checkOnboardingGate(){
  if(currentOrgRole !== 'admin' && currentOrgRole !== 'superadmin') return;
  try{
    const r = await authFetch(`${BASE}/api/v1/billing/onboarding-status`);
    if(!r.ok) return;
    const d = await r.json();
    if(!d.needs_payment_setup){ window._onboardingGateActive = false; return; }
    window._onboardingGateActive = true;
    const close = document.querySelector('#upgrade-modal .close');
    if(close) close.style.display = 'none';
    showUpgradeModal('Welcome to Procta! To start your 14-day free trial, choose a plan and add a payment method below — you won\'t be charged until the trial ends.');
  }catch(_){ /* gate is best-effort; backend still blocks usage */ }
}

function trialBannerClick(){
  const badge = document.getElementById('topbar-trial-badge');
  const days = parseInt(badge.textContent.match(/\d+/)?.[0] || '0', 10);
  showUpgradeModal('Your trial ends in ' + days + ' day' + (days === 1 ? '' : 's') + '. Upgrade to keep using Procta.');
}

// ── SECURITY (2FA + SESSIONS) ──────────────────────────────────
function loadSecurity(){
  load2FAStatus();
  loadSessions();
  loadNotifPrefs();
  // Org-wide MFA policy is admin/superadmin-only (gap #20). The card is
  // hidden for plain teachers via data-roles, so only fetch when relevant.
  if(currentOrgRole === 'admin' || currentOrgRole === 'superadmin') loadOrgMfaPolicy();
}

// ── Org-wide MFA policy (gap #20) ────────────────────────────────────
// Admin toggle that forces every member through the email-OTP step at
// login (enforced in app/routers/auth.py via organizations.require_2fa).
async function loadOrgMfaPolicy(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/require-2fa`);
    if(!r.ok) return;
    const d = await r.json();
    renderOrgMfaPolicy(!!d.require_2fa);
  }catch(_){}
}

function renderOrgMfaPolicy(on){
  const statusEl = document.getElementById('security-org-2fa-status');
  const enableBtn = document.getElementById('security-org-2fa-enable-btn');
  const disableBtn = document.getElementById('security-org-2fa-disable-btn');
  if(statusEl){
    statusEl.innerHTML = on
      ? '✅ Org-wide 2FA is <strong style="color:var(--emerald)">required</strong>. Every member gets an email code at sign-in.'
      : 'ℹ️ Org-wide 2FA is <strong style="color:var(--amber)">optional</strong> — each member chooses for themselves.';
  }
  if(enableBtn) enableBtn.style.display = on ? 'none' : '';
  if(disableBtn) disableBtn.style.display = on ? '' : 'none';
}

async function setOrgRequire2fa(value){
  const resultEl = document.getElementById('security-org-2fa-result');
  if(resultEl){ resultEl.style.color = 'var(--text-muted)'; resultEl.textContent = value ? 'Enabling…' : 'Disabling…'; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/require-2fa`, {
      method: 'POST',
      body: JSON.stringify({require_2fa: !!value})
    });
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Update failed')); }
    if(resultEl){
      resultEl.textContent = value ? '✅ Now required for all members.' : 'Now optional for members.';
      resultEl.style.color = 'var(--emerald)';
    }
    renderOrgMfaPolicy(!!value);
  }catch(e){
    if(resultEl){ resultEl.textContent = e.message || 'Update failed'; resultEl.style.color = 'var(--red)'; }
  }
}

// ── Notification preferences (gap #28) ──────────────────────────────
const _NOTIF_CATEGORIES = {
  billing: 'Billing alerts — payment failures',
  security: 'Security alerts — suspicious sign-ins',
  student_activity: 'Student activity — account deletions'
};

async function loadNotifPrefs(){
  try{
    const r = await authFetch(`${BASE}/api/v1/notification-preferences`);
    if(!r.ok){ document.getElementById('notification-prefs-card')?.remove(); return; }
    const d = await r.json();
    renderNotifPrefs(d);
  }catch(_){ document.getElementById('notification-prefs-card')?.remove(); }
}

function renderNotifPrefs(prefs){
  const container = document.getElementById('notification-prefs-list');
  if(!container) return;
  container.innerHTML = '';
  for(const [key, label] of Object.entries(_NOTIF_CATEGORIES)){
    const on = prefs[key] !== false;
    const wrapper = document.createElement('label');
    wrapper.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;font-size:13px';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = on;
    cb.dataset.notifCategory = key;
    cb.addEventListener('change', ()=> toggleNotifPref(key, cb.checked));
    wrapper.appendChild(cb);
    wrapper.appendChild(document.createTextNode(label));
    container.appendChild(wrapper);
  }
}

async function toggleNotifPref(category, enabled){
  const resultEl = document.getElementById('notification-prefs-result');
  if(resultEl){ resultEl.textContent = 'Saving…'; resultEl.style.color = 'var(--text-muted)'; }
  try{
    const r = await authFetch(`${BASE}/api/v1/notification-preferences`, {
      method: 'PATCH',
      body: JSON.stringify({[category]: !!enabled})
    });
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Update failed')); }
    if(resultEl){
      resultEl.textContent = enabled ? `${category} notifications ON` : `${category} notifications OFF`;
      resultEl.style.color = 'var(--emerald)';
      setTimeout(()=>{ if(resultEl) resultEl.textContent = ''; }, 3000);
    }
  }catch(e){
    if(resultEl){ resultEl.textContent = e.message || 'Save failed'; resultEl.style.color = 'var(--red)'; }
    loadNotifPrefs(); // reload to reset checkbox
  }
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

// Re-auth helper shared by 2FA flows and destructive action
// confirmations. Prompts for the user's password and exchanges it
// for a 5-minute reauth_token. Centralised here so all callers use
// identical logic.
async function _getReauthToken(action){
  const password = await appPrompt(`Enter your password to ${action}:`, '', {title:'Re-authentication required', okText:'Continue', inputType:'password'});
  if(!password) return null;
  const rr = await authFetch(`${BASE}/api/v1/auth/reauth`, {
    method:'POST',
    body: JSON.stringify({password})
  });
  if(!rr.ok){ const d=await rr.json().catch(()=>({})); throw new Error(_detailText(d, 'Re-authentication failed')); }
  const rd = await rr.json();
  return rd.reauth_token;
}

async function enable2FA(){
  const resultEl = document.getElementById('security-2fa-result');
  resultEl.style.color = 'var(--text-muted)';
  resultEl.textContent = '';
  try{
    const reauth_token = await _getReauthToken('enable');
    if(!reauth_token) return;
    resultEl.textContent = 'Enabling...';
    const r = await authFetch(`${BASE}/api/v1/auth/2fa/enable`, {
      method:'POST',
      body: JSON.stringify({reauth_token})
    });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Failed to enable 2FA')); }
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
    const reauth_token = await _getReauthToken('disable');
    if(!reauth_token) return;
    resultEl.textContent = 'Disabling...';
    const r = await authFetch(`${BASE}/api/v1/auth/2fa/disable`, {
      method:'POST',
      body: JSON.stringify({reauth_token})
    });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(_detailText(d, 'Disable failed')); }
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
    const sessions = d.sessions || [];
    const html = sessions.length ? sessions.map(s => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-subtle)">
        <div style="font-size:12px;color:var(--text)">${_escHtml(s.user_agent||'Unknown browser')}</div>
        <div style="font-size:11px;color:var(--muted);font-family:monospace">${s.ip||''}</div>
        <button class="btn btn-ghost btn-sm" data-action="revokeSession" data-args='${_jsonArgsForAttr(s.jti)}' style="font-size:10px;color:var(--red);padding:2px 6px">Revoke</button>
      </div>
    `).join('') : '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:12px">No active sessions</div>';
    // Fill whichever session containers are present (Security and/or Profile tab).
    ['security-sessions','profile-sessions'].forEach(id => { const el = document.getElementById(id); if(el) el.innerHTML = html; });
  }catch(_){}
}

// ── PROFILE tab ─────────────────────────────────────────────────────
async function loadProfile(){
  let t = currentTeacherProfile;
  if(!t){ try{ const r = await authFetch(`${BASE}/api/v1/auth/me`); if(r.ok) t = await r.json(); }catch(_){ } }
  if(t){
    const nameEl = document.getElementById('profile-name'); if(nameEl) nameEl.value = t.full_name || '';
    const emEl = document.getElementById('profile-email'); if(emEl) emEl.textContent = t.email || '—';
    const roleEl = document.getElementById('profile-role');
    if(roleEl){
      const role = (t.org_role || 'teacher'); const cap = role.charAt(0).toUpperCase() + role.slice(1);
      roleEl.textContent = cap;
      if(!t.is_solo){ try{ const o = await authFetch(`${BASE}/api/v1/org`); if(o.ok){ const od = await o.json(); if(od && od.name) roleEl.textContent = cap + ' · ' + od.name; } }catch(_){ } }
    }
  }
  loadSessions();
}
async function saveProfileName(){
  const el = document.getElementById('profile-name'); const res = document.getElementById('profile-name-result');
  const name = ((el && el.value) || '').trim();
  if(!name){ if(res) res.textContent = 'Name cannot be empty.'; return; }
  if(res) res.textContent = 'Saving…';
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/me`, { method:'PATCH', body: JSON.stringify({full_name:name}) });
    if(r.ok){
      if(res) res.textContent = '✅ Saved';
      const tn = document.getElementById('teacher-name'); if(tn) tn.textContent = name;
      if(currentTeacherProfile) currentTeacherProfile.full_name = name;
    } else { const d = await r.json().catch(()=>({})); if(res) res.textContent = d.detail || 'Save failed'; }
  }catch(_){ if(res) res.textContent = 'Save failed'; }
}
async function profileChangePassword(){
  const res = document.getElementById('profile-pwd-result');
  const email = (currentTeacherProfile && currentTeacherProfile.email) || '';
  if(!email){ if(res) res.textContent = 'No email on file.'; return; }
  if(res) res.textContent = 'Sending…';
  try{
    const body = { email }; if(typeof _turnstileToken !== 'undefined' && _turnstileToken) body.captcha_token = _turnstileToken;
    await authFetch(`${BASE}/api/v1/auth/password-reset`, { method:'POST', body: JSON.stringify(body) });
  }catch(_){ }
  if(res) res.textContent = '✅ Check your email for a secure reset link.';
}

// ── Left sidebar collapse/expand (desktop) ─────────────────────────
function toggleSidebar(){
  const collapsed = document.body.classList.toggle('sidebar-collapsed');
  try{ localStorage.setItem('procta_sidebar_collapsed', collapsed ? '1' : ''); }catch(e){}
}
// Restore the collapsed preference on load (runs as the script parses).
try{ if(localStorage.getItem('procta_sidebar_collapsed')==='1') document.body.classList.add('sidebar-collapsed'); }catch(e){}

// Registration QR — show/hide a scannable QR of the self-registration link.
// The PNG is served by /api/v1/admin/qr (same-origin, cookie-auth, CSP-safe).
function toggleRegQR(){
  const box = document.getElementById('reg-qr-box');
  if(!box) return;
  if(box.style.display === 'none' || !box.style.display){
    const link = (document.getElementById('share-register-link')||{}).value || '';
    const img = document.getElementById('reg-qr-img');
    if(link && img) img.src = `${BASE}/api/v1/admin/qr?data=${encodeURIComponent(link)}`;
    box.style.display = '';
  } else { box.style.display = 'none'; }
}

// ── Recent Activity (Historical Log) — recently ENDED sessions in the Live tab ──
async function renderRecentActivity(){
  const el = document.getElementById('recent-activity-list'); if(!el) return;
  el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 0">Loading…</div>';
  try{
    const ex = currentExamId ? `&exam_id=${encodeURIComponent(currentExamId)}` : '';
    const r = await authFetch(`${BASE}/api/v1/results?page=1&page_size=15${ex}`);
    if(!r.ok){ el.innerHTML = '<div style="color:var(--text-muted);font-size:12px">Could not load recent activity.</div>'; return; }
    const d = await r.json();
    const rows = d.results || [];
    if(!rows.length){ el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 0">No completed sessions yet.</div>'; return; }
    el.innerHTML = rows.map(s => {
      const name = _escHtml(s.full_name || s.email || ('#'+(s.roll_number||'')) || 'Unnamed');
      const when = _escHtml((s.submitted_at||'').split(',').slice(0,2).join(',')) || '—';
      const ended = (String(s.status||'').toLowerCase() === 'force_submitted') ? 'Force-ended' : 'Submitted';
      const vc = s.violation_count || 0;
      const flags = vc ? (vc + ' flag' + (vc>1?'s':'')) : 'clean';
      const flagColor = vc ? 'var(--amber)' : 'var(--text-muted)';
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-subtle);font-size:12px">
        <span style="color:var(--text-muted);font-family:monospace;white-space:nowrap">${when}</span>
        <span style="flex:1;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name} <span style="color:var(--muted)">#${_escHtml(s.roll_number||'')}</span></span>
        <span style="color:var(--text-muted);white-space:nowrap">${ended} · <span style="color:${flagColor}">${flags}</span></span>
      </div>`;
    }).join('');
  }catch(e){ el.innerHTML = '<div style="color:var(--text-muted);font-size:12px">Could not load recent activity.</div>'; }
}
function toggleRecentActivity(){
  const el = document.getElementById('recent-activity-list');
  const caret = document.getElementById('recent-activity-caret');
  if(!el) return;
  const open = (el.style.display === 'none' || !el.style.display);
  el.style.display = open ? '' : 'none';
  if(caret) caret.textContent = open ? '▾' : '▸';
  if(open) renderRecentActivity();
}

// ── Coding "Preview as student" — see the question as students do + run samples ──
let _cpvQid = null;
function codingPreviewAsStudent(){
  const msg = document.getElementById('coding-save-msg');
  if(typeof _editingCodingId === 'undefined' || !_editingCodingId){ if(msg) msg.textContent = 'Save the question first, then preview it.'; return; }
  const statement = (document.getElementById('coding-statement')||{}).value || '';
  let langs = ['python']; try{ langs = JSON.parse((document.getElementById('coding-langs')||{}).value || '["python"]'); }catch(e){}
  const starter = (document.getElementById('coding-starter-code')||{}).value || '';
  _cpvEnsure(); _cpvQid = _editingCodingId;
  document.getElementById('cpv-statement').textContent = statement || '(no statement)';
  const sel = document.getElementById('cpv-lang');
  sel.innerHTML = (langs.length?langs:['python']).map(l => `<option value="${_escHtml(l)}">${_escHtml(l)}</option>`).join('');
  document.getElementById('cpv-code').value = starter || '';
  document.getElementById('cpv-results').innerHTML = '';
  document.getElementById('cpv-overlay').style.display = 'flex';
}
function _cpvEnsure(){
  if(document.getElementById('cpv-overlay')) return;
  const ov = document.createElement('div');
  ov.id = 'cpv-overlay'; ov.className = 'modal-overlay coding-modal'; ov.style.display = 'none';
  ov.setAttribute('role','dialog'); ov.setAttribute('aria-modal','true');
  ov.setAttribute('data-action','codingPreviewClose'); ov.setAttribute('data-guard-self','');
  ov.innerHTML = '<div class="modal-box"><div class="modal-title">Preview — as students see it</div>'
    + '<div class="coding-form">'
    + '<div><label>Problem</label><div id="cpv-statement" style="white-space:pre-wrap;font-size:13px;color:var(--text);background:var(--surface-1,#161a22);border:1px solid var(--border-subtle,rgba(255,255,255,.1));border-radius:8px;padding:10px 12px;max-height:160px;overflow:auto"></div></div>'
    + '<div class="field-row-2"><div><label for="cpv-lang">Language</label><select id="cpv-lang"></select></div>'
    + '<div style="display:flex;align-items:flex-end"><button class="modal-btn" data-action="codingPreviewRun" style="width:100%">Run sample tests</button></div></div>'
    + '<div><label for="cpv-code">Your code</label><textarea id="cpv-code" rows="10" style="font-family:var(--font-mono);font-size:12px;width:100%"></textarea></div>'
    + '<div id="cpv-results"></div></div>'
    + '<div class="coding-form-actions" style="margin-top:12px"><span style="flex:1"></span><button class="modal-btn modal-btn-secondary" data-action="codingPreviewClose">Close</button></div></div>';
  document.body.appendChild(ov);
}
function codingPreviewClose(){ const o=document.getElementById('cpv-overlay'); if(o) o.style.display='none'; }
async function codingPreviewRun(){
  const res = document.getElementById('cpv-results'); if(!res) return;
  const language = (document.getElementById('cpv-lang')||{}).value || '';
  const source = (document.getElementById('cpv-code')||{}).value || '';
  res.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:6px 0">Running…</div>';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/coding-question/preview-run`, { method:'POST', body: JSON.stringify({question_id:_cpvQid, language, source}) });
    const d = await r.json().catch(()=>({}));
    if(!r.ok){ const e=(d.detail&&d.detail.error)||d.detail||'Run failed'; res.innerHTML = `<div style="color:var(--red);font-size:12px">${_escHtml(String(e))}</div>`; return; }
    const cases = d.cases||[];
    if(!cases.length){ res.innerHTML = '<div style="color:var(--text-muted);font-size:12px">This question has no sample test cases.</div>'; return; }
    res.innerHTML = `<div style="font-size:13px;font-weight:600;margin:8px 0">${d.passed}/${d.total} sample tests passed</div>` + cases.map((c,i)=>{
      const pass = c.status==='passed';
      return `<div style="border:1px solid var(--border-subtle);border-radius:8px;padding:8px;margin-bottom:6px;font-size:12px">
        <div style="font-weight:600;color:${pass?'var(--green,#3fb950)':'var(--red)'}">Sample ${i+1}: ${_escHtml(c.status)}</div>
        <div style="color:var(--text-muted);margin-top:4px">Input: <code>${_escHtml(c.input||'')}</code></div>
        <div style="color:var(--text-muted)">Expected: <code>${_escHtml(c.expected_output||'')}</code></div>
        <div style="color:var(--text-muted)">Got: <code>${_escHtml(c.output||'')}</code></div>
        ${c.error?`<div style="color:var(--red);margin-top:4px">${_escHtml(String(c.error))}</div>`:''}
      </div>`;
    }).join('');
  }catch(e){ res.innerHTML = '<div style="color:var(--red);font-size:12px">Run failed.</div>'; }
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
  const resultEl = document.getElementById('security-sessions-result'); // only on Security tab
  const say = (m) => { if(resultEl) resultEl.textContent = m; };
  say('Revoking...');
  let reauth_token;
  try { reauth_token = await _getReauthToken('sign out other devices'); }
  catch(e){ say(e.message || 'Re-authentication failed'); return; }
  if(!reauth_token) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/auth/sessions/revoke-others`, {
      method:'POST',
      body: JSON.stringify({reauth_token})
    });
    if(!r.ok) throw new Error();
    say('✅ Other sessions revoked.');
    loadSessions();
  }catch(e){ say('Failed: '+e.message); }
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
    return `<div class="grade-row" data-aid="${escAttr(a.answer_id)}" style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;background:var(--card,#161a22);${isGraded?'opacity:0.55':''}">
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
  const ids = _pendingGrades.filter(a => a.ai_score==null && a.teacher_score==null).map(a => a.answer_id);
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
      body: JSON.stringify({answer_id: a.answer_id, score})
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

// ── Secondary (phone) camera review — aggregate grid popup ──────────
// One popup showing EVERY student currently pending room-cam approval, each with
// its live phone feed + Approve/Reject, so the teacher reviews them all in one
// place instead of clicking each student's row. Reuses the per-session room-cam
// endpoints (start / frame / keepalive / approve / reject / stop).
let _scTimers = {};   // sid -> {frame, keepalive}
let _scOpen = false;

function _pendingRoomCamSessions(){
  return (typeof liveData !== 'undefined' && Array.isArray(liveData) ? liveData : [])
    .filter(s => String(s.room_cam_status || '') === 'pending');
}
function _scSidOf(s){ return s.session_key || s.session_id || s.sid || ''; }
function _scTile(sid){
  const b = document.getElementById('roomcam-grid-body');
  return b ? b.querySelector(`.roomcam-tile[data-sid="${(window.CSS && CSS.escape) ? CSS.escape(sid) : sid}"]`) : null;
}

async function openSecondaryCamGrid(){
  _scOpen = true;
  const body = document.getElementById('roomcam-grid-body');
  const empty = document.getElementById('roomcam-grid-empty');
  const pending = _pendingRoomCamSessions();
  body.innerHTML = '';
  empty.style.display = pending.length ? 'none' : '';
  document.getElementById('roomcam-grid-modal').classList.remove('hidden');
  for(const s of pending){
    const sid = _scSidOf(s);
    if(!sid) continue;
    const name = s.full_name || s.roll_number || sid;
    const tile = document.createElement('div');
    tile.className = 'roomcam-tile';
    tile.dataset.sid = sid;
    tile.innerHTML =
      `<div class="rc-feed"><div class="rc-ph">Connecting…</div>`
      + `<img alt="${escAttr(name)} phone camera" style="display:none"></div>`
      + `<div class="rc-info"><span class="rc-name">${_escHtml(name)}</span>`
      + `<span class="rc-status">●&nbsp;…</span></div>`
      + `<div class="rc-actions">`
      + `<button class="rc-approve" data-action="scApprove" data-args='${_jsonArgsForAttr(sid)}'>Approve</button>`
      + `<button class="rc-reject" data-action="scReject" data-args='${_jsonArgsForAttr(sid)}'>Reject</button></div>`;
    body.appendChild(tile);
    try{ await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/start`, {method:'POST'}); }catch(_){}
    _scTimers[sid] = {
      frame: setInterval(() => _scPollFrame(sid), 1500),
      keepalive: setInterval(() => _scKeepalive(sid), 30000),
    };
    _scPollFrame(sid);
  }
}

function _scPollFrame(sid){
  if(!_scOpen) return;
  const tile = _scTile(sid); if(!tile) return;
  const headers = {}; if(authToken) headers.Authorization = `Bearer ${authToken}`;
  fetchWithTimeout(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/frame?t=${Date.now()}`,
                   {credentials:'include', headers})
    .then(r => { if(!r.ok) throw new Error(); return r.blob(); })
    .then(blob => {
      const img = tile.querySelector('img'), ph = tile.querySelector('.rc-ph'), st = tile.querySelector('.rc-status');
      const old = img.src;
      img.src = URL.createObjectURL(blob); img.style.display = ''; if(ph) ph.style.display = 'none';
      if(old && old.startsWith('blob:')) URL.revokeObjectURL(old);
      if(st){ st.innerHTML = '●&nbsp;Live'; st.style.color = 'var(--emerald)'; }
    })
    .catch(() => { const st = tile.querySelector('.rc-status'); if(st){ st.innerHTML = '●&nbsp;Offline'; st.style.color = 'var(--muted)'; } });
}

async function _scKeepalive(sid){
  try{ await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/keepalive`, {method:'POST'}); }catch(_){}
}

function _scStopOne(sid){
  const t = _scTimers[sid];
  if(t){ clearInterval(t.frame); clearInterval(t.keepalive); delete _scTimers[sid]; }
  const tile = _scTile(sid);
  if(tile){ const img = tile.querySelector('img'); if(img && img.src && img.src.startsWith('blob:')) URL.revokeObjectURL(img.src); }
  authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/stop`, {method:'POST'}).catch(()=>{});
}

async function _scDecide(sid, action){
  const tile = _scTile(sid), st = tile && tile.querySelector('.rc-status');
  if(st){ st.innerHTML = action === 'approve' ? 'Approving…' : 'Rejecting…'; st.style.color = 'var(--muted)'; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/${action}`, {method:'POST'});
    if(!r.ok) throw new Error();
    _scStopOne(sid);
    if(tile) tile.remove();
    const body = document.getElementById('roomcam-grid-body');
    if(body && body.children.length === 0) document.getElementById('roomcam-grid-empty').style.display = '';
  }catch(e){ if(st){ st.innerHTML = '●&nbsp;Failed'; st.style.color = 'var(--red)'; } }
}
function scApprove(sid){ return _scDecide(sid, 'approve'); }
function scReject(sid){ return _scDecide(sid, 'reject'); }

function closeSecondaryCamGrid(){
  _scOpen = false;
  Object.keys(_scTimers).forEach(_scStopOne);
  _scTimers = {};
  const body = document.getElementById('roomcam-grid-body'); if(body) body.innerHTML = '';
  const m = document.getElementById('roomcam-grid-modal'); if(m) m.classList.add('hidden');
}



// ── LIVE WEBCAM VIEW ───────────────────────────────────────────────
// Ported from the React LiveSessionsPanel into the (canonical) legacy
// dashboard. Mirrors the room-cam flow above but for the student's PRIMARY
// webcam: live-view/start → 1.5s frame poll → 25s keepalive → live-view/stop.
// The 25s keepalive is REQUIRED: the server's liveview:{sid} key has a 60s
// TTL and the proctor's control loop stops streaming once it lapses, so
// without renewal the feed froze after ~60s.
async function openLiveView(sid){
  _liveViewSid = sid;
  const img = document.getElementById('liveview-img');
  const ph = document.getElementById('liveview-placeholder');
  const meta = document.getElementById('liveview-meta');
  const statusEl = document.getElementById('liveview-status');
  if(img){ img.style.display = 'none'; }
  if(ph){ ph.style.display = ''; }
  if(meta){ meta.textContent = sid; }
  if(statusEl){ statusEl.innerHTML = '● Connecting'; }
  document.getElementById('liveview-modal').classList.remove('hidden');

  try{
    const r = await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/live-view/start`, {method:'POST'});
    if(!r.ok) throw new Error();
  }catch(e){ if(statusEl) statusEl.innerHTML = '● Failed'; return; }

  if(_liveViewFrameTimer) clearInterval(_liveViewFrameTimer);
  if(_liveViewKeepaliveTimer) clearInterval(_liveViewKeepaliveTimer);
  _liveViewFrameTimer = setInterval(_pollLiveFrame, 1500);
  _liveViewKeepaliveTimer = setInterval(_liveViewKeepalive, 25000);
}

function _pollLiveFrame(){
  if(!_liveViewSid) return;
  const img = document.getElementById('liveview-img');
  const ph = document.getElementById('liveview-placeholder');
  const statusEl = document.getElementById('liveview-status');
  const tsEl = document.getElementById('liveview-ts');
  const t = Date.now();
  const headers = {};
  if(authToken) headers.Authorization = `Bearer ${authToken}`;
  fetchWithTimeout(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_liveViewSid)}/live-frame?t=${t}`, {
    credentials: 'include',
    headers,
  }).then(r => {
    if(!r.ok) throw new Error();
    return r.blob();
  }).then(blob => {
    if(_liveViewSid && img){
      // Revoke the prior frame's object URL before swapping in the new one —
      // the room-cam poll above leaks one blob: URL per 1.5s tick; this
      // doesn't (matters over a long watch).
      const prior = img.src;
      img.src = URL.createObjectURL(blob); img.style.display = '';
      if(ph) ph.style.display = 'none';
      if(prior && prior.startsWith('blob:')){ try{ URL.revokeObjectURL(prior); }catch(_){} }
      if(tsEl) tsEl.textContent = new Date().toLocaleTimeString();
      if(statusEl) statusEl.innerHTML = '● Live';
    }
  }).catch(() => {
    if(statusEl) statusEl.innerHTML = '●&#160;Offline';
  });
}

async function _liveViewKeepalive(){
  if(_liveViewSid){
    try{ await authFetch(`${BASE}/api/v1/admin/sessions/${encodeURIComponent(_liveViewSid)}/live-view/keepalive`, {method:'POST'}); }catch(_){}
  }
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
      throw new Error(_detailText(d, `Save failed (${r.status})`));
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

// On-device proctor readiness, from the proctoring_tier / proctor_camera_failed
// events the backend folds into each session. Only surfaced when DEGRADED so a
// teacher can see a student whose exam is proctored at reduced capacity — or
// not at all — instead of assuming full AI coverage. Full/unknown shows nothing.
function _proctorBadge(s){
  const tier = s && s.proctor_tier;
  if(!tier || tier === 'full') return '';
  const missing = (s.proctor_missing || []).join(', ');
  const styles = {
    camera_failed: ['No camera', 'rgba(192,57,43,.18)', '#f87171', 'rgba(192,57,43,.5)', 'Proctor could not open a camera — AI proctoring is disabled for this student'],
    minimal:       ['AI minimal', 'rgba(192,57,43,.18)', '#f87171', 'rgba(192,57,43,.5)', 'Face detection unavailable — only basic checks are running'],
    reduced:       ['AI reduced', 'rgba(245,158,11,.18)', '#fbbf24', 'rgba(245,158,11,.5)', 'Some AI detectors are unavailable'],
  };
  const st = styles[tier] || ['AI ' + tier, 'rgba(245,158,11,.18)', '#fbbf24', 'rgba(245,158,11,.5)', ''];
  const title = missing ? `${st[4]} (missing: ${missing})` : st[4];
  return ` <span class="badge" title="${escAttr(title)}" style="background:${st[1]};color:${st[2]};border:1px solid ${st[3]};margin-left:4px">⚠ ${_escHtml(st[0])}</span>`;
}

function renderLiveStats(activeRows=[], allRows=[]){
  const el = document.getElementById('live-stats');
  if(!el) return;
  const all = Array.isArray(allRows) ? allRows : [];
  const active = Array.isArray(activeRows) ? activeRows : [];
  const submitted = all.filter(s => s.submitted || s.live_state === 'submitted' || s.live_state === 'force_submitted').length;
  const stale = all.filter(s => s.live_state === 'stale').length;
  const highRisk = all.filter(s => Number(s.risk_score || 0) > 40).length;
  const pendingCam = all.filter(s => String(s.room_cam_status || '') === 'pending').length;
  // Students waiting for phone-camera approval get one actionable tile that
  // opens the review grid — no per-row hunting. Only shown when any are waiting.
  const camTile = pendingCam > 0
    ? `<button class="stat-tile stat-tile-action" data-action="openSecondaryCamGrid" title="Review phone cameras waiting for approval"><div class="stat-tile-label">Phone cams waiting</div><div class="stat-tile-value" style="color:var(--amber)">${pendingCam}</div></button>`
    : '';
  el.innerHTML = `
    <div class="stat-tile"><div class="stat-tile-label">Live Now</div><div class="stat-tile-value accent">${active.length}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">All Sessions</div><div class="stat-tile-value">${all.length}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">High Risk</div><div class="stat-tile-value" style="color:var(--red)">${highRisk}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Submitted</div><div class="stat-tile-value success">${submitted}</div></div>
    <div class="stat-tile"><div class="stat-tile-label">Stale</div><div class="stat-tile-value">${stale}</div></div>
    ${camTile}
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
  // Completed / submitted / force-submitted sessions are DONE — they belong in
  // the Results tab, not the live monitor, so they don't pile up here forever.
  // (Abandoned + stale stay: they're recoverable via the ↺ Reset button.)
  rows = rows.filter(s => !(s.submitted || s.live_state === 'submitted'));
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
    const isPaused    = s.live_state === 'paused';
    const isSubmitted = s.submitted || s.live_state === 'force_submitted' || s.live_state === 'completed';
    // Reset re-opens a CLOSED/abandoned session (disconnect recovery). Shown for
    // submitted/terminal rows and stale ones (the reaper marks a long-disconnected
    // session abandoned → stale here). The backend refuses an active session.
    const isResettable = isSubmitted || s.live_state === 'stale' || s.live_state === 'abandoned';
    const state = s.submitted ? 'Submitted'
                 : s.live_state === 'force_submitted' ? 'Force Submitted'
                 : s.live_state === 'abandoned' ? 'Abandoned'
                 : isPaused ? 'PAUSED'
                 : (s.live_state || 'Active');
    // Room (phone) camera: surface a button to view/approve it whenever the
    // student has a room cam in play. 'pending' = student is waiting on the
    // exam screen for the teacher to approve — make it loud (amber).
    const rcStatus = s.room_cam_status || 'disabled';
    const rcActive = !isSubmitted && rcStatus !== 'disabled' && rcStatus !== 'offline';
    const roomCamBtn = rcActive
      ? `<button class="btn btn-secondary btn-sm" title="View / approve the student's room (phone) camera" data-action="openRoomCamView" data-args='${_jsonArgsForAttr(sid)}'${rcStatus === 'pending' ? ' style="background:rgba(245,158,11,.18);color:#fbbf24;border:1px solid rgba(245,158,11,.5)"' : ''}>🎥 Room${rcStatus === 'pending' ? ' • approve' : ''}</button>`
      : '';
    // Phase 74: Warn / Pause / Resume / End intervention buttons.
    // Only shown on still-running sessions (not after submit/terminate).
    const interventionBtns = isSubmitted ? '' : `
        <button class="btn btn-secondary btn-sm" title="Warn student" data-action="openInterventionWarn" data-args='${_jsonArgsForAttr(sid)}'>⚠️</button>
        ${isPaused
          ? `<button class="btn btn-secondary btn-sm" title="Resume exam" data-action="confirmResumeSession" data-args='${_jsonArgsForAttr(sid)}' style="color:var(--emerald)">▶</button>`
          : `<button class="btn btn-secondary btn-sm" title="Pause exam"  data-action="confirmPauseSession"  data-args='${_jsonArgsForAttr(sid)}'>⏸</button>`}
        <button class="btn btn-secondary btn-sm" title="End exam (with reason)" data-action="confirmEndSession" data-args='${_jsonArgsForAttr(sid)}' style="color:var(--red)">⛔</button>`;
    return `<tr data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}' style="cursor:pointer">
      <td><span style="font-family:var(--font-mono);font-size:11px">${_escHtml(sid)}</span>${_proctorBadge(s)}</td>
      <td>${_escHtml((s.last_event || '--').replace(/_/g,' '))}</td>
      <td><span class="sev ${escAttr(String(s.last_severity || 'low').toLowerCase())}">${_escHtml(s.last_severity || '--')}</span></td>
      <td><span class="badge">${_escHtml(risk)}</span></td>
      <td>${_calBadge(s.calibration)}</td>
      <td>${_escHtml(s.last_seen || (s.heartbeat_age_sec != null ? `${s.heartbeat_age_sec}s ago` : '--'))}</td>
      <td>${isPaused ? `<span class="badge" style="background:rgba(245,158,11,.18);color:#fbbf24;border:1px solid rgba(245,158,11,.5)">${_escHtml(state)}</span>` : _escHtml(state)}</td>
      <td>
        <button class="btn btn-secondary btn-sm" data-action="openTriage" data-args='${_jsonArgsForAttr(sid)}'>Insight</button>
        <button class="btn btn-secondary btn-sm" data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}'>Timeline</button>
        ${isSubmitted ? '' : `<button class="btn btn-secondary btn-sm" title="Watch the student's live webcam" data-action="openLiveView" data-args='${_jsonArgsForAttr(sid)}'>📷 Camera</button>`}
        ${roomCamBtn}
        ${interventionBtns}
        ${isResettable ? `<button class="btn btn-secondary btn-sm" title="Re-open this session so the student can re-enter (e.g. after a disconnection)" data-action="confirmResetSession" data-args='${_jsonArgsForAttr(sid)}' style="color:var(--emerald)">↺ Reset</button>` : ''}
      </td>
    </tr>`;
  }).join('');
}

// ── Phase 74 — live teacher intervention handlers ────────────────

const _WARN_CHIPS = [
  ['eyes_off_screen',    'Eyes off screen'],
  ['phone_visible',      'Phone visible'],
  ['talking_to_someone', 'Talking to someone'],
  ['multiple_tabs',      'Multiple tabs'],
  ['other',              'Other (note required)'],
];

const _END_REASON_CHIPS = [
  ['academic_dishonesty', 'Suspected academic dishonesty'],
  ['identity_fraud',      'Identity could not be re-verified'],
  ['environment_issue',   'Unsuitable exam environment'],
  ['repeated_violations', 'Repeated proctoring violations'],
  ['student_request',     'Student requested termination'],
  ['technical_failure',   'Persistent technical failure'],
  ['other',               'Other (note required)'],
];

// Reuse the chip + textarea modal pattern from _openIdReasonModal,
// generalised. Returns {chip_code, text} or null on cancel. Set
// `requireTextOnOther: true` to inline-validate that "Other" picks
// have a non-empty note before the dialog resolves.
function _openChipPickerModal({title, intro, chips, okText, requireTextOnOther}){
  const els = _appModalEls();
  if(!els.overlay || !els.title || !els.body || !els.ok || !els.cancel){
    return Promise.resolve(null);
  }
  if(_appDialogResolve) _appDialogResolve(null);
  _appDialogMode = 'chip_picker';
  els.title.textContent = title;
  els.body.innerHTML = '';
  if(intro){
    const introEl = document.createElement('div');
    introEl.style.cssText = 'color:var(--text-muted);font-size:13px;margin-bottom:10px';
    introEl.textContent = intro;
    els.body.appendChild(introEl);
  }
  const chipRow = document.createElement('div');
  chipRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px';
  let selectedCode = '';
  for(const [code, label] of chips){
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.dataset.code = code;
    chip.textContent = label;
    chip.style.cssText = 'background:rgba(255,255,255,.04);border:1px solid var(--border);' +
      'border-radius:999px;color:var(--text);padding:6px 12px;font-size:12px;cursor:pointer;' +
      'transition:background .15s,border-color .15s';
    chip.onclick = () => {
      selectedCode = (selectedCode === code) ? '' : code;
      Array.from(chipRow.children).forEach(c => {
        const isSel = c.dataset.code === selectedCode;
        c.style.background  = isSel ? 'rgba(91,138,240,.18)' : 'rgba(255,255,255,.04)';
        c.style.borderColor = isSel ? 'var(--blue, #5b8af0)' : 'var(--border)';
      });
    };
    chipRow.appendChild(chip);
  }
  els.body.appendChild(chipRow);
  const textLabel = document.createElement('div');
  textLabel.style.cssText = 'font-size:12px;color:var(--text-muted);margin-bottom:4px';
  textLabel.textContent = 'Add a note (optional, max 500 chars)';
  els.body.appendChild(textLabel);
  const textarea = document.createElement('textarea');
  textarea.id = 'app-modal-chip-text';
  textarea.rows = 3;
  textarea.maxLength = 500;
  textarea.style.cssText = 'width:100%;background:rgba(255,255,255,.04);border:1px solid var(--border);' +
    'border-radius:10px;color:var(--text);padding:10px 12px;font-size:13px;outline:none;' +
    'box-sizing:border-box;resize:vertical';
  els.body.appendChild(textarea);
  els.ok.textContent = okText || 'Submit';
  els.cancel.textContent = 'Cancel';
  els.cancel.style.display = '';
  els.overlay.style.display = 'flex';
  setTimeout(() => textarea.focus(), 0);
  els.body._chipState = {
    getCode: () => selectedCode,
    getText: () => textarea.value,
    requireTextOnOther: !!requireTextOnOther,
  };
  return new Promise(resolve => { _appDialogResolve = resolve; });
}

async function openInterventionWarn(sid){
  const r = await _openChipPickerModal({
    title: 'Warn the student',
    intro: 'The student will see an amber banner with the chip label and any note you add.',
    chips: _WARN_CHIPS,
    okText: 'Send warning',
    requireTextOnOther: true,
  });
  if(!r) return;
  try{
    const resp = await authFetch(`${BASE}/api/v1/admin/session/${encodeURIComponent(sid)}/warn`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({chip_code: r.code, text: r.text}),
    });
    if(!resp.ok){
      const d = await resp.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${resp.status}`));
    }
  }catch(e){
    showModal('Warning failed', e.message || 'Could not send warning.');
  }
}

async function confirmResetSession(sid){
  // Re-open a closed/abandoned session so the student can re-enter. Their saved
  // answers are preserved server-side, so a genuine disconnect resumes where it
  // left off. Optional note is for the teacher's own audit trail.
  const note = await appPrompt(
    'Re-open this session so the student can re-enter?\n\n'
    + 'Use this for a genuine disconnection — the student\'s saved answers are kept, so they resume where they left off. '
    + 'Add an optional note for your records, or leave blank.',
    '',
    {title:'Reset session', okText:'Re-open', multiline:true});
  if(note === null) return;  // teacher cancelled
  try{
    const resp = await authFetch(`${BASE}/api/v1/admin/session/${encodeURIComponent(sid)}/reset`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({note: (note || '').slice(0, 200)}),
    });
    if(!resp.ok){
      const d = await resp.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${resp.status}`));
    }
    await refreshLive();
  }catch(e){
    showModal('Reset failed', e.message || 'Could not re-open the session.');
  }
}

async function confirmPauseSession(sid){
  // Confirm + optional one-line note via appPrompt's multiline mode.
  // Empty note submits cleanly — note is purely additive UX. The
  // confirm framing happens inside the prompt body so we don't make
  // the teacher click through two dialogs.
  const note = await appPrompt(
    'Pause this exam? The student\'s clock stops and their screen locks until you resume.\n\n'
    + 'Add an optional one-line note for the student (shown in the pause overlay). Leave blank to send no note.',
    '',
    {title:'Pause exam', okText:'Pause', multiline:true});
  if(note === null) return;  // teacher cancelled
  try{
    const resp = await authFetch(`${BASE}/api/v1/admin/session/${encodeURIComponent(sid)}/pause`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({note: (note || '').slice(0, 200)}),
    });
    if(!resp.ok){
      const d = await resp.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${resp.status}`));
    }
    await refreshLive();
  }catch(e){
    showModal('Pause failed', e.message || 'Could not pause the exam.');
  }
}

async function confirmResumeSession(sid){
  const ok = await appConfirm(
    'Resume this exam? The student\'s clock will start again from where it stopped.',
    'Resume exam', {okText:'Resume'});
  if(!ok) return;
  try{
    const resp = await authFetch(`${BASE}/api/v1/admin/session/${encodeURIComponent(sid)}/resume`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:'{}',
    });
    if(!resp.ok){
      const d = await resp.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${resp.status}`));
    }
    await refreshLive();
  }catch(e){
    showModal('Resume failed', e.message || 'Could not resume the exam.');
  }
}

async function confirmEndSession(sid){
  const ok = await appConfirm(
    'End this exam? This CLOSES the session and writes a permanent record. The student\'s answers will be scored as-is.',
    'End exam', {okText:'End exam'});
  if(!ok) return;
  // Reauth gate — matches existing forceSubmit pattern.
  const password = await appPrompt(
    'Enter your password to end this exam:',
    '', {title:'Re-authentication required', okText:'Continue', inputType:'password'});
  if(!password) return;
  let reauthToken = '';
  try{
    const rauth = await authFetch(`${BASE}/api/v1/auth/reauth`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({password}),
    });
    if(!rauth.ok){
      const d = await rauth.json().catch(()=>({}));
      throw new Error(_detailText(d, 'Re-auth failed'));
    }
    const rd = await rauth.json();
    reauthToken = rd.reauth_token || '';
  }catch(e){
    showModal('Re-auth failed', e.message || 'Wrong password.');
    return;
  }
  const r = await _openChipPickerModal({
    title: 'End exam — pick a reason',
    intro: 'The reason is shown to the student, embedded in their scorecard PDF, and saved to the audit log.',
    chips: _END_REASON_CHIPS,
    okText: 'End exam',
    requireTextOnOther: true,
  });
  if(!r) return;
  try{
    const resp = await authFetch(`${BASE}/api/v1/admin-submit/${encodeURIComponent(sid)}`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        reauth_token: reauthToken,
        reason_code:  r.code,
        reason_text:  r.text,
      }),
    });
    if(!resp.ok){
      const d = await resp.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${resp.status}`));
    }
    await refreshLive();
  }catch(e){
    showModal('End failed', e.message || 'Could not end the exam.');
  }
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
        <button class="btn btn-secondary btn-sm" title="Branded result summary — share with the student / institution" data-action="dlScorecard" data-args='${_jsonArgsForAttr(sid)}'>Scorecard</button>
        <button class="btn btn-secondary btn-sm" title="Full proctoring evidence log — per-event violations, confidence + screenshots, for review &amp; appeals" data-action="dlPDF" data-args='${_jsonArgsForAttr(sid)}'>Audit Report</button>
        <button class="btn btn-secondary btn-sm" data-action="openTimelineForSession" data-args='${_jsonArgsForAttr(sid)}'>Timeline</button>
      </td>
    </tr>`;
  }).join('');
}

function filterResults(){
  currentGroupFilter = document.getElementById('results-group-filter')?.value || '';
  currentBatchFilter = document.getElementById('results-batch-filter')?.value || '';
  renderResults();
  // When cohort filters are active the server must re-filter.
  if(currentGroupFilter || currentBatchFilter) refreshResults();
}

// Tracks the prior pending count so we can auto-focus the first card
// only on the transition from 0 → N. Avoids scroll-jumping every poll.
let _lastIdReviewCount = 0;

// Mirror of the rows last rendered into the queue. The focus modal
// (openIdReviewModal) reads this so it can page through students and
// auto-advance after each decision without re-fetching. Kept in sync by
// refreshIdReviews on every poll.
let _idReviewQueue = [];
let _idReviewModalIdx = -1;   // which student the modal is showing
let _idReviewPhotoIdx = 0;    // which of that student's photos is shown
let _idReviewRenderKey = '';  // identity of what's painted, so a poll that
                              // changes nothing visible skips re-render
                              // (preserves an in-progress zoom).

async function refreshIdReviews(){
  const section = document.getElementById('id-reviews-section');
  const list = document.getElementById('id-reviews-list');
  const count = document.getElementById('id-reviews-count');
  if(!section || !list || !count) return;
  try{
    // NO exam scoping here on purpose. An ID check is an IDENTITY step that
    // happens BEFORE the exam, and the student's session exam_id may not match
    // (or may be unset for) whatever exam the teacher has selected in the
    // dropdown — scoping by it silently hid students from the review queue, so
    // they sat "waiting for examiner" with no card to approve. Show ALL of the
    // teacher's pending verifications (same rationale as the live-sessions view).
    const r = await authFetch(`${BASE}/api/v1/admin/pending-verifications`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const rows = d.pending || [];
    count.textContent = rows.length;
    section.style.display = rows.length ? '' : 'none';
    // Compact, single-click rows. The full review — large photos shown
    // one at a time with Approve / Retake / Reject below — lives in the
    // focus modal (openIdReviewModal) so the dashboard isn't flooded.
    // `tabindex=0` keeps the A/R/X keyboard shortcuts working on a
    // focused row for power users who don't want to open the modal.
    list.innerHTML = rows.map(v => {
      const safeName = _escHtml(v.full_name || v.roll_number || 'Student');
      const safeKey  = _escHtml(v.session_key || '');
      const thumbUrl = v.selfie_url || v.id_url || '';
      const thumb = thumbUrl
        ? `<img src="${escAttr(thumbUrl)}" alt="" class="id-review-thumb" data-error-action="_irImgError">`
        : `<div class="id-review-thumb id-review-thumb-empty">ID</div>`;
      return `
      <div class="id-review-card" tabindex="0" role="button"
           data-action="openIdReviewModal" data-args='${_jsonArgsForAttr(v.id)}'
           data-violation-id="${v.id}" data-session-key="${escAttr(v.session_key || '')}"
           data-full-name="${escAttr(v.full_name || v.roll_number || 'Student')}"
           title="Open review">
        ${thumb}
        <div class="id-review-info">
          <div class="id-review-name">${safeName}</div>
          <div class="id-review-roll">${safeKey}</div>
          <div class="id-review-time">${_escHtml(v.created_at || '')}</div>
        </div>
        <span class="id-review-open-hint" aria-hidden="true">Review →</span>
      </div>
    `;
    }).join('');
    // Keep a focus modal pinned to the SAME student across a background
    // poll: capture its violation id BEFORE we swap the queue array out
    // from under it, then re-sync.
    const _modalOpen = !!document.getElementById('id-review-modal');
    const _prevVid = (_modalOpen && _idReviewQueue[_idReviewModalIdx])
      ? _idReviewQueue[_idReviewModalIdx].id : null;
    _idReviewQueue = rows;
    if(_modalOpen) _syncIdReviewModalAfterRefresh(_prevVid);
    // Auto-focus + pulse the first card on the 0→N transition.
    if(_lastIdReviewCount === 0 && rows.length > 0){
      const first = list.firstElementChild;
      if(first){
        first.scrollIntoView({block: 'nearest', behavior: 'smooth'});
        first.classList.add('id-review-pulse');
        setTimeout(() => first.classList.remove('id-review-pulse'), 1100);
      }
    }
    _lastIdReviewCount = rows.length;
  }catch(e){
    console.warn('refreshIdReviews', e);
    section.style.display = 'none';
  }
}

// ── ID review focus modal ────────────────────────────────────────
// A click-to-open popup that focuses one student at a time: their
// captured photos shown large, one at a time (selfie / ID card), with
// Approve / Retake / Reject below. Reuses decideIdReview() for the
// actual decision (so the confirm + reason flow is identical) and
// auto-advances to the next pending student after each decision.
function _currentIdReviewStudent(){
  return _idReviewQueue[_idReviewModalIdx] || null;
}
function _currentIdReviewPhotos(){
  const v = _currentIdReviewStudent();
  if(!v) return [];
  const out = [];
  if(v.selfie_url) out.push({label: 'Selfie', url: v.selfie_url});
  if(v.id_url)     out.push({label: 'ID card', url: v.id_url});
  return out;
}
// Identity of the currently-visible modal state. If a refresh leaves this
// unchanged we can skip re-rendering (and so preserve an in-progress zoom).
function _idReviewKey(){
  const v = _currentIdReviewStudent();
  if(!v) return '';
  const photos = _currentIdReviewPhotos();
  const pIdx = _idReviewPhotoIdx >= photos.length ? 0 : _idReviewPhotoIdx;
  const cur = photos[pIdx];
  return `${v.id}|${_idReviewModalIdx}|${_idReviewQueue.length}|${pIdx}|${(cur && cur.url) || ''}`;
}

function openIdReviewModal(violationId){
  const idx = _idReviewQueue.findIndex(v => String(v.id) === String(violationId));
  if(idx < 0) return;
  _idReviewModalIdx = idx;
  _idReviewPhotoIdx = 0;
  let ov = document.getElementById('id-review-modal');
  if(ov) ov.remove();
  ov = document.createElement('div');
  ov.id = 'id-review-modal';
  ov.className = 'ir-modal';
  ov.setAttribute('role', 'dialog');
  ov.setAttribute('aria-modal', 'true');
  // Click on the backdrop itself (guard-self) closes; clicks inside the
  // card bubble up to a data-action button instead.
  ov.setAttribute('data-action', '_closeIdReviewModal');
  ov.setAttribute('data-guard-self', '');
  document.body.appendChild(ov);
  document.addEventListener('keydown', _idReviewModalKeydown);
  _renderIdReviewModal();
}

function _renderIdReviewModal(){
  const ov = document.getElementById('id-review-modal');
  if(!ov) return;
  const v = _currentIdReviewStudent();
  if(!v){ _closeIdReviewModal(); return; }
  const photos = _currentIdReviewPhotos();
  if(_idReviewPhotoIdx >= photos.length) _idReviewPhotoIdx = 0;
  const total = _idReviewQueue.length;
  const pos   = _idReviewModalIdx + 1;
  _idReviewRenderKey = _idReviewKey();
  const name  = _escHtml(v.full_name || v.roll_number || 'Student');
  const key   = _escHtml(v.session_key || '');
  const cur   = photos[_idReviewPhotoIdx];
  const stage = cur
    ? `<div class="ir-stage-label">${_escHtml(cur.label)} · ${_idReviewPhotoIdx + 1} / ${photos.length}</div>
       <img class="ir-stage-img" src="${escAttr(cur.url)}" alt="${_escHtml(cur.label)}"
            title="Click to zoom" data-action="_idReviewZoomToggle" data-error-action="_irImgError">`
    : `<div class="ir-stage-empty">No photos were captured for this student.</div>`;
  const photoNav = photos.length > 1
    ? `<button class="ir-arrow ir-arrow-prev" data-action="_idReviewPhotoNav" data-args='[-1]' aria-label="Previous photo">‹</button>
       <button class="ir-arrow ir-arrow-next" data-action="_idReviewPhotoNav" data-args='[1]' aria-label="Next photo">›</button>`
    : '';
  ov.innerHTML = `
  <div class="ir-card" role="document">
    <div class="ir-head">
      <div class="ir-who">
        <div class="ir-name">${name}</div>
        <div class="ir-key">${key}</div>
      </div>
      <div class="ir-pos">Review ${pos} of ${total}</div>
      <button class="ir-close" data-action="_closeIdReviewModal" title="Close (Esc)" aria-label="Close">✕</button>
    </div>
    <div class="ir-stage">
      ${photoNav}
      ${stage}
    </div>
    <div class="ir-dots">${photos.map((p, i) => `<span class="ir-dot${i === _idReviewPhotoIdx ? ' on' : ''}"></span>`).join('')}</div>
    <div class="ir-actions">
      <button class="btn id-btn-approve" data-action="_idReviewModalDecide" data-args='["approved"]'>Approve ✓</button>
      <button class="btn id-btn-retake"  data-action="_idReviewModalDecide" data-args='["retake"]'>Retake</button>
      <button class="btn id-btn-reject"  data-action="_idReviewModalDecide" data-args='["rejected"]'>Reject ✕</button>
    </div>
    <div class="ir-foot">
      <button class="ir-link" data-action="_idReviewStudentNav" data-args='[-1]' ${pos <= 1 ? 'disabled' : ''}>‹ Prev</button>
      <span class="ir-hint">A approve · R retake · X reject · ←/→ photos · Esc close</span>
      <button class="ir-link" data-action="_idReviewStudentNav" data-args='[1]' ${pos >= total ? 'disabled' : ''}>Next ›</button>
    </div>
  </div>`;
}

function _idReviewPhotoNav(delta){
  const photos = _currentIdReviewPhotos();
  if(photos.length < 2) return;
  _idReviewPhotoIdx = (_idReviewPhotoIdx + delta + photos.length) % photos.length;
  _renderIdReviewModal();
}
function _idReviewStudentNav(delta){
  const next = _idReviewModalIdx + delta;
  if(next < 0 || next >= _idReviewQueue.length) return;
  _idReviewModalIdx = next;
  _idReviewPhotoIdx = 0;
  _renderIdReviewModal();
}
function _idReviewZoomToggle(){ this.classList.toggle('zoomed'); }
function _irImgError(){ this.style.display = 'none'; }

function _closeIdReviewModal(){
  const ov = document.getElementById('id-review-modal');
  if(ov) ov.remove();
  document.removeEventListener('keydown', _idReviewModalKeydown);
  _idReviewModalIdx = -1;
}

// Re-render / advance / close the modal after the queue is refreshed.
// `prevVid` is the violation id the modal was showing before the swap.
// If it's still pending → keep showing it (a background poll shouldn't
// move the teacher). If it's gone (just decided) → the next student has
// slid into the same slot, so show that one; close if the queue emptied.
function _syncIdReviewModalAfterRefresh(prevVid){
  if(!document.getElementById('id-review-modal')) return;
  if(_idReviewQueue.length === 0){ _closeIdReviewModal(); return; }
  let at = (prevVid != null)
    ? _idReviewQueue.findIndex(x => String(x.id) === String(prevVid))
    : -1;
  if(at < 0){
    // Student resolved/removed — keep the slot so the next slides in.
    at = Math.min(_idReviewModalIdx, _idReviewQueue.length - 1);
    _idReviewPhotoIdx = 0;
  }
  _idReviewModalIdx = Math.max(0, at);
  // A background poll that changes nothing visible shouldn't repaint the
  // image (would reset an in-progress zoom). Only re-render on a change.
  if(_idReviewKey() === _idReviewRenderKey) return;
  _renderIdReviewModal();
}

async function _idReviewModalDecide(decision){
  const v = _currentIdReviewStudent();
  if(!v) return;
  // decideIdReview runs the same confirm + reason flow as the inline
  // queue. On a committed decision it calls refreshIdReviews(), which
  // re-syncs this modal (advance / close). On cancel nothing changes and
  // the modal stays on the same student.
  await decideIdReview(v.id, v.session_key, v.full_name || v.roll_number || '', decision);
}

function _idReviewModalKeydown(e){
  if(!document.getElementById('id-review-modal')) return;
  // A stacked confirm / reason modal owns the keyboard while it's open.
  const ovEl = document.getElementById('app-modal-overlay');
  if(ovEl && ovEl.style.display && ovEl.style.display !== 'none') return;
  if(e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if(tag === 'input' || tag === 'textarea' || tag === 'select') return;
  const k = e.key;
  if(k === 'Escape'){ e.preventDefault(); _closeIdReviewModal(); return; }
  if(k === 'ArrowLeft'){ e.preventDefault(); _idReviewPhotoNav(-1); return; }
  if(k === 'ArrowRight'){ e.preventDefault(); _idReviewPhotoNav(1); return; }
  const map = { a: 'approved', A: 'approved', r: 'retake', R: 'retake', x: 'rejected', X: 'rejected' };
  if(map[k]){ e.preventDefault(); _idReviewModalDecide(map[k]); }
}

// Build a copy of the side-by-side overlay each call so multiple
// open/close cycles don't accumulate stale handlers. No new dep —
// just absolute-positioned divs over a darkened backdrop.
function _openIdComparisonOverlay(selfieUrl, idUrl, fullName){
  // Clean any prior overlay.
  const prior = document.getElementById('id-compare-overlay');
  if(prior) prior.remove();
  const ov = document.createElement('div');
  ov.id = 'id-compare-overlay';
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(6,8,13,.92);z-index:9999;'
    + 'display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px';
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if(e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  ov.addEventListener('click', e => { if(e.target === ov) close(); });
  // Header row with name + zoom controls + swap + close.
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;gap:14px;align-items:center;margin-bottom:14px;color:#c9d1d9;font-size:13px';
  header.innerHTML = `<div style="font-weight:600">${_escHtml(fullName || 'Student')}</div>`
    + `<div style="opacity:.6">·</div>`
    + `<button id="id-compare-zoom-1" class="btn btn-secondary btn-sm" style="padding:4px 10px;font-size:11px">1×</button>`
    + `<button id="id-compare-zoom-2" class="btn btn-secondary btn-sm" style="padding:4px 10px;font-size:11px">2×</button>`
    + `<button id="id-compare-zoom-fit" class="btn btn-secondary btn-sm" style="padding:4px 10px;font-size:11px">Fit</button>`
    + `<button id="id-compare-swap" class="btn btn-secondary btn-sm" style="padding:4px 10px;font-size:11px">Swap</button>`
    + `<div style="flex:1"></div>`
    + `<button id="id-compare-close" class="btn btn-secondary btn-sm" style="padding:4px 12px;font-size:11px">Close ✕</button>`;
  ov.appendChild(header);
  // Image row. Each pane wraps its image so we can swap by reorder.
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:18px;align-items:stretch;justify-content:center;max-width:100%;'
    + 'max-height:calc(100vh - 96px);overflow:auto';
  const makePane = (url, label) => {
    const pane = document.createElement('div');
    pane.dataset.label = label;
    pane.style.cssText = 'display:flex;flex-direction:column;gap:8px;align-items:center';
    pane.innerHTML = `<div style="font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;font-weight:600">${label}</div>`
      + `<img src="${escAttr(url)}" style="max-width:46vw;max-height:80vh;border-radius:10px;border:1px solid #30363d;transition:transform .2s;transform-origin:center top">`;
    return pane;
  };
  const selfiePane = makePane(selfieUrl || '', 'Selfie');
  const idPane     = makePane(idUrl     || '', 'ID card');
  if(selfieUrl) row.appendChild(selfiePane);
  if(idUrl)     row.appendChild(idPane);
  ov.appendChild(row);
  document.body.appendChild(ov);
  // Wire controls.
  const setZoom = (factor) => {
    row.querySelectorAll('img').forEach(img => {
      img.style.transform = factor === 'fit' ? 'none' : `scale(${factor})`;
    });
  };
  document.getElementById('id-compare-zoom-1').onclick   = () => setZoom(1);
  document.getElementById('id-compare-zoom-2').onclick   = () => setZoom(2);
  document.getElementById('id-compare-zoom-fit').onclick = () => setZoom('fit');
  document.getElementById('id-compare-swap').onclick = () => {
    if(row.children.length === 2){
      row.insertBefore(row.children[1], row.children[0]);
    }
  };
  document.getElementById('id-compare-close').onclick = close;
}
// Dispatch wrapper so the data-action attribute can invoke it.
function openIdComparison(selfieUrl, idUrl, fullName){
  _openIdComparisonOverlay(selfieUrl, idUrl, fullName);
}

async function decideIdReview(violationId, sessionKey, fullName, decision){
  // Approve is non-destructive — single-click stays.
  if(decision === 'approved'){
    return _submitIdDecision(violationId, sessionKey, decision, '', '');
  }
  // Retake + Reject get a confirm modal first to catch misclicks, then
  // a reason picker so the student sees WHY.
  const verb = decision === 'rejected' ? 'Reject' : 'Retake';
  const who  = fullName || 'this student';
  const confirmMsg = decision === 'rejected'
    ? `Reject ${who}'s identity? This CLOSES their exam session and cannot be undone.`
    : `Ask ${who} to retake their photos? They will see your reason on their screen.`;
  const okText = decision === 'rejected' ? 'Reject' : 'Send retake request';
  const ok = await appConfirm(confirmMsg, `${verb} identity`, {okText});
  if(!ok) return;
  const reason = await _openIdReasonModal({
    decision,
    fullName: who,
    okText: decision === 'rejected' ? 'Reject identity' : 'Send retake',
  });
  if(!reason) return; // teacher backed out of the reason picker
  return _submitIdDecision(violationId, sessionKey, decision, reason.reason_code, reason.reason_text);
}

async function _submitIdDecision(violationId, sessionKey, decision, reason_code, reason_text){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/id-decision`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        violation_id: violationId,
        session_key:  sessionKey,
        decision,
        reason_code:  reason_code || '',
        reason_text:  reason_text || '',
      })
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${r.status}`));
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
  fetchBlob(`${BASE}/api/v1/export-pdf/${encodeURIComponent(sid)}`, `audit-report_${sid.split('_')[0]}.pdf`);
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
    if(!r.ok) throw new Error(_detailText(d, `Failed to load insight (${r.status})`));
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
      throw new Error(_detailText(d, 'Failed to send invite'));
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
    if (!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
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
    if(!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
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
    if(!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
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
    if(!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
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


// Statuses that mean the org currently has an entitling plan — mirrors the
// server's ENTITLING_STATUSES (services/billing.py). Used to lock plan tiles
// and short-circuit a switch attempt with an honest message instead of a 409.
const _ENTITLING_BILLING_STATUSES = new Set(['trialing','authenticated','active','past_due','cancelling']);
let _billingState = { plan: '', status: '' };
let _billingCycle = 'monthly';

// Client-side plan helpers (mirrors app/constants.py PLANS)
const _PLAN_PRICE = {starter:2400, growth:12000, pro:30000, enterprise:0};
const _PLAN_ANNUAL_PRICE = {starter:24000, growth:120000, pro:300000, enterprise:0};
const _PLAN_NAMES = {starter:'Starter', growth:'Growth', pro:'Pro', enterprise:'Enterprise'};
function planName(id){ return _PLAN_NAMES[String(id||'').toLowerCase()] || (id||'Unknown'); }
function planPrice(id, cycle){
  id = String(id||'').toLowerCase();
  cycle = String(cycle||_billingCycle).toLowerCase();
  const prices = cycle === 'annual' ? _PLAN_ANNUAL_PRICE : _PLAN_PRICE;
  return prices[id] || 0;
}
function fmtINR(paise){ return '\u20b9'+(paise||0).toLocaleString('en-IN'); }
function fmtDate(isoStr){
  if(!isoStr) return '';
  const d = new Date(isoStr);
  if(isNaN(d.getTime())) return '';   // never render "Invalid Date"
  return d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
}

// Reflect the active subscription in the plan tiles. When entitled, non-current
// tiles get an enabled CTA (Upgrade/Downgrade) wired to changePlan(). When not
// entitled (no subscription yet), tiles keep the original upgradePlan handler.
// Respects _billingCycle to show monthly or annual pricing.
function updateBillingTiles(plan, status){
  const cur = String(plan || '').toLowerCase();
  const entitled = _ENTITLING_BILLING_STATUSES.has(String(status || '').toLowerCase());
  const cycle = String(_billingCycle || 'monthly').toLowerCase();
  const isAnnual = cycle === 'annual';
  document.querySelectorAll('#billing-plans .plan-tile').forEach(tile => {
    const p = String(tile.dataset.plan || '').toLowerCase();
    const cta = tile.querySelector('.plan-cta');
    const isCurrent = entitled && p === cur;
    tile.classList.toggle('is-current', isCurrent);
    tile.style.cursor = (entitled && !isCurrent) ? 'pointer' : (entitled ? 'default' : 'pointer');

    // Update price display
    const monthlyAmtEl = tile.querySelector('.plan-amount');
    const periodEl = tile.querySelector('.plan-period');
    const savingsEl = tile.querySelector('.plan-savings');
    if(monthlyAmtEl && periodEl){
      const monthlyPrice = _PLAN_PRICE[p] || 0;
      const annualPrice = _PLAN_ANNUAL_PRICE[p] || 0;
      if(isAnnual && annualPrice){
        monthlyAmtEl.textContent = fmtINR(annualPrice).replace(/^\u20b9/,'');
        if(periodEl) periodEl.textContent = '/ year';
      }else{
        monthlyAmtEl.textContent = fmtINR(monthlyPrice).replace(/^\u20b9/,'');
        if(periodEl) periodEl.textContent = '/ month';
      }
    }
    // Show/hide savings badge
    if(savingsEl){
      const monthlyPrice = _PLAN_PRICE[p] || 0;
      const annualPrice = _PLAN_ANNUAL_PRICE[p] || 0;
      if(isAnnual && annualPrice){
        const savings = monthlyPrice * 12 - annualPrice;
        if(savings > 0){
          savingsEl.textContent = 'Save '+fmtINR(savings)+' / 2 months free';
          savingsEl.style.display = '';
        }else{
          savingsEl.style.display = 'none';
        }
      }else{
        savingsEl.style.display = 'none';
      }
    }

    if(!cta) return;
    if(isCurrent){
      cta.style.display = 'none';
      cta.disabled = true;
    }else if(entitled){
      cta.style.display = '';
      cta.disabled = false;
      cta.removeAttribute('title');
      cta.className = cta.className.replace(/\bis-downgrade\b/g,'').trim();
      const curPrice = planPrice(cur, cycle);
      const tilePrice = planPrice(p, cycle);
      if(tilePrice > curPrice){
        cta.textContent = 'Upgrade';
        cta.classList.remove('is-downgrade');
      }else{
        cta.textContent = 'Downgrade';
        cta.classList.add('is-downgrade');
      }
      tile.dataset.action = 'changePlan';
      tile.dataset.args = JSON.stringify([p, cycle]);
      if(cta.dataset.action !== 'changePlan') cta.dataset.action = 'changePlan';
      if(!cta.dataset.args) cta.dataset.args = tile.dataset.args;
    }else{
      cta.style.display = '';
      cta.disabled = false;
      cta.removeAttribute('title');
      cta.className = cta.className.replace(/\bis-downgrade\b/g,'').trim();
      cta.textContent = cta.dataset.label || cta.textContent;
      tile.dataset.action = 'upgradePlan';
      tile.dataset.args = JSON.stringify([p, cycle]);
      if(cta.dataset.action !== 'upgradePlan') cta.dataset.action = 'upgradePlan';
      if(!cta.dataset.args) cta.dataset.args = tile.dataset.args;
    }
  });
}

async function upgradePlan(planId, billingCycle, couponCode){
  // Recurring Subscriptions only — create a Razorpay subscription and redirect
  // to its hosted checkout (UPI Autopay / NACH). Entitlement is granted on the
  // server only when the subscription activates (webhook → reconcile).
  const resultEl = document.getElementById('upgrade-result');
  const cycle = String(billingCycle || _billingCycle || 'monthly').toLowerCase();
  // Block switching while a plan is active — matches the server's 409 guard.
  // Gives instant feedback instead of a round-trip that just errors.
  const cur = String(_billingState.plan || '').toLowerCase();
  if(_ENTITLING_BILLING_STATUSES.has(String(_billingState.status || '').toLowerCase())){
    if(resultEl){
      resultEl.style.color = 'var(--text-secondary)';
      resultEl.textContent = String(planId || '').toLowerCase() === cur
        ? 'You’re already on this plan.'
        : 'You already have an active plan. Cancel it first — you keep access until the end of your billing period — to switch plans.';
    }
    return;
  }
  if(resultEl){ resultEl.textContent = 'Creating subscription...'; resultEl.style.color = 'var(--text-secondary)'; }
  try{
  const body = {plan_id:planId, billing_cycle: cycle};
  const cc = couponCode || _getCouponCode();
  if(cc){ body.coupon_code = cc; }
    const r = await authFetch(`${BASE}/api/v1/billing/create-subscription`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    const d = await r.json().catch(()=>({}));
    if(!r.ok){ throw new Error(_detailText(d, 'Subscription failed')); }
    if(d.short_url){
      if(resultEl){ resultEl.textContent = 'Redirecting to Razorpay for UPI Autopay setup...'; }
      window.location.href = d.short_url;
    }else if(resultEl){
      resultEl.textContent = d._note || 'Subscription created.';
      resultEl.style.color = 'var(--emerald)';
      if(typeof loadBilling === 'function') loadBilling();
    }
  }catch(e){
    if(resultEl){ resultEl.textContent = e.message; resultEl.style.color = 'var(--red)'; }
  }
}

function toggleBillingCycle(){
  const input = document.getElementById('billing-cycle-input');
  const checked = input ? input.checked : false;
  _billingCycle = checked ? 'annual' : 'monthly';
  // Update toggle label active state
  document.querySelectorAll('.billing-cycle-label').forEach(lbl => {
    lbl.classList.toggle('is-active', lbl.dataset.cycle === _billingCycle);
  });
  // Show/hide savings badge
  const badge = document.getElementById('billing-cycle-savings-badge');
  if(badge) badge.style.display = checked ? '' : 'none';
  // Re-render plan tiles with the new cycle
  updateBillingTiles(_billingState.plan, _billingState.status);
}

// ── Coupon helpers ───────────────────────────────────────────
let _validatedCouponCode = null;

function _getCouponCode(){
  return _validatedCouponCode;
}

function _clearCoupon(){
  _validatedCouponCode = null;
  document.getElementById('coupon-status').textContent = '';
  document.getElementById('upgrade-coupon-status').textContent = '';
}

async function _validateCouponUI(inputId, statusId){
  const input = document.getElementById(inputId);
  const statusEl = document.getElementById(statusId);
  if(!input || !statusEl) return;
  const code = input.value.trim();
  if(!code){ statusEl.textContent = ''; _validatedCouponCode = null; return; }
  statusEl.textContent = 'Validating...';
  statusEl.style.color = 'var(--text-muted)';
  try{
    const r = await authFetch(`${BASE}/api/v1/billing/validate-coupon?code=${encodeURIComponent(code)}`);
    const d = await r.json().catch(()=>({}));
    if(d.valid){
      _validatedCouponCode = code;
      statusEl.textContent = d.description || 'Coupon applied!';
      statusEl.style.color = 'var(--emerald)';
    }else{
      _validatedCouponCode = null;
      statusEl.textContent = 'Invalid or expired coupon code.';
      statusEl.style.color = 'var(--red)';
    }
  }catch(e){
    _validatedCouponCode = null;
    statusEl.textContent = 'Could not validate coupon.';
    statusEl.style.color = 'var(--red)';
  }
}

function applyCoupon(){
  _validateCouponUI('upgrade-coupon-input', 'upgrade-coupon-status');
}

function applyCouponBilling(){
  _validateCouponUI('coupon-input', 'coupon-status');
}

async function changePlan(planId, billingCycle){
  const cur = String(_billingState.plan || '').toLowerCase();
  const curPrice = _PLAN_PRICE[cur] || 0;
  const newPrice = _PLAN_PRICE[planId] || 0;
  const isUpgrade = newPrice > curPrice;
  const pName = planName(planId);
  const msg = isUpgrade
    ? `Upgrade to ${pName} now? You'll be charged a prorated amount for the rest of this cycle, then ${fmtINR(newPrice)}/cycle from the next renewal.`
    : `Downgrade to ${pName}? You keep ${planName(cur)} until your current cycle ends, then it switches. No refund.`;
  if(!(await appConfirm(msg, 'Change plan?', {okText:'Continue'}))) return;
  const reauth = await _getReauthToken('change your plan');
  if(!reauth) return;
  const r = await authFetch(`${BASE}/api/v1/billing/change-plan`, {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Reauth-Token':reauth},
    body:JSON.stringify({plan_id:planId})
  });
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ showModal('Plan change failed', _detailText(d, 'Plan change failed')); return; }
  if(d.cleared){
    showModal('Schedule cleared', 'The pending plan change has been cancelled.');
  }else if(isUpgrade){
    showModal('Upgraded', `Upgraded to ${pName}. Prorated charge: ${fmtINR(d.proration_inr||0)}.`);
  }else{
    showModal('Downgrade scheduled', `Downgrade to ${pName} scheduled for ${fmtDate(d.scheduled_plan_effective_at)}.`);
  }
  loadBilling();
}

async function cancelScheduledChange(){
  // Reuse changePlan with the current plan = cancel the schedule
  const cur = String(_billingState.plan || '').toLowerCase();
  if(!(await appConfirm('Keep your current plan? The scheduled downgrade will be cancelled.','Cancel downgrade',{okText:'Keep current plan'}))) return;
  const reauth = await _getReauthToken('change your plan');
  if(!reauth) return;
  const r = await authFetch(`${BASE}/api/v1/billing/change-plan`, {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Reauth-Token':reauth},
    body:JSON.stringify({plan_id:cur})
  });
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ showModal('Error', _detailText(d, 'Failed to cancel scheduled change')); return; }
  showModal('Schedule cleared', 'Your plan will stay the same. The scheduled downgrade has been cancelled.');
  loadBilling();
}

// Payment-method help. Razorpay (unlike Stripe) has NO hosted customer portal
// to swap a card — UPI Autopay / card eMandate is managed by the customer's
// bank/UPI app, and Razorpay emails an update link on a failed charge. So
// instead of opening a (non-existent) portal, explain the real flow + point at
// the working in-app controls (change plan / cancel) in this Billing tab.
function openBillingPortal(){
  showModal('Payment method',
    'Your payment method (UPI Autopay or card eMandate) is held securely by Razorpay — Procta never stores your card. ' +
    'If a renewal payment fails, Razorpay emails you a secure link to update it. ' +
    'To switch to a different card or UPI app, cancel your plan here (you keep access until the end of the current billing period) and then re-subscribe with the new method. ' +
    'To change tier, use the plan cards above.');
}

// Ported from the dropped React ReviewPanel: cluster false-positive triage.
// Clusters + dismiss are both scope-wide (no exam_id) so they always match.
async function loadReview(){
  const el = document.getElementById('review-body');
  if(!el) return;
  el.innerHTML = '<div class="exams-empty">Loading…</div>';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/violations/clusters`);
    if(!r.ok) throw new Error('Failed to load clusters');
    const d = await r.json();
    const clusters = d.clusters || [];
    if(!clusters.length){ el.innerHTML = '<div class="exams-empty">No active flags to review.</div>'; return; }
    let html = '<table class="data-table" style="width:100%"><thead><tr><th>Type</th><th>Severity</th><th>Count</th><th></th></tr></thead><tbody>';
    clusters.forEach(c=>{
      html += `<tr><td>${_escHtml(c.violation_type||'')}</td><td>${_escHtml(c.severity||'')}</td><td>${c.count||0}</td>`
        + `<td><button class="btn btn-secondary btn-sm" type="button" data-action="dismissCluster" data-args='${_escHtml(JSON.stringify([c.violation_type||'', c.severity||'']))}'>Dismiss all</button></td></tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  }catch(e){ el.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load clusters</strong></div>'; }
}

async function dismissCluster(violationType, severity){
  if(!(await appConfirm(`Dismiss all "${violationType}" (${severity}) flags in scope? This clears them from risk scoring.`, 'Bulk dismiss?', {okText:'Dismiss all'}))) return;
  try{
    const body = { violation_type: violationType };
    if(severity) body.severity = severity;
    const r = await authFetch(`${BASE}/api/v1/admin/violations/bulk-dismiss`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(_detailText(d, 'Dismiss failed'));
    showModal(`Dismissed ${d.dismissed||0} flag(s).`);
    loadReview();
  }catch(e){ showModal('Dismiss failed: ' + e.message); }
}

// ─── STUDENT APPEALS (teacher review) ─────────────────────────────────────────
// Ported from the dropped React ReviewPanel: list + resolve student appeals.
// The backend (appeals.py: GET /admin/appeals, POST /admin/appeals/{id}/resolve)
// already shipped, but the live HTML dashboard had no surface — so students
// could file disputes no teacher could see or action. Accepting a flag-linked
// appeal dismisses the flag + recomputes risk server-side. "View flag + context"
// reuses the forensics timeline, which renders the pre-violation context strip
// (t-3s..t-0) for the disputed flag. Media stays teacher-side: the student's own
// evidence view never returns frames (privacy boundary in appeals.py).
function _setAppealsBadge(n){
  const badge = document.getElementById('appeals-pending-badge');
  if(!badge) return;
  if(n > 0){ badge.textContent = n; badge.style.display = ''; }
  else { badge.style.display = 'none'; }
}

async function refreshAppealsBadge(){
  // Lightweight pending-count poll. Best-effort — never throws into callers.
  if(!document.getElementById('appeals-pending-badge')) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/appeals?status=pending`);
    if(!r.ok) return;
    const d = await r.json();
    _setAppealsBadge((d.appeals || []).length);
  }catch(_){ /* badge is informational */ }
}

async function loadAppeals(){
  const el = document.getElementById('appeals-body');
  if(!el) return;
  const filterEl = document.getElementById('appeals-filter');
  const status = (filterEl && filterEl.value) || 'pending';
  el.innerHTML = '<div class="exams-empty">Loading…</div>';
  try{
    const qs = status === 'all' ? '' : `?status=${encodeURIComponent(status)}`;
    const r = await authFetch(`${BASE}/api/v1/admin/appeals${qs}`);
    if(!r.ok) throw new Error('Failed to load appeals');
    const d = await r.json();
    const appeals = d.appeals || [];
    // Badge always reflects PENDING regardless of the active filter.
    _setAppealsBadge(status === 'pending' ? appeals.length
      : appeals.filter(a => a.status === 'pending').length);
    if(!appeals.length){
      el.innerHTML = `<div class="exams-empty">No ${status === 'all' ? '' : _escHtml(status) + ' '}appeals.</div>`;
      return;
    }
    el.innerHTML = appeals.map(renderAppealCard).join('');
    _lazyLoadAuthThumbs(el);
  }catch(e){
    el.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load appeals</strong></div>';
  }
}

// Fetch the auth-gated thumbnail endpoints (same shape the timeline uses) as
// blobs and swap them in. Frames are never inlined in JSON — they stream
// through /admin/screenshot, which re-checks teacher scope.
function _lazyLoadAuthThumbs(scopeEl){
  if(!scopeEl) return;
  scopeEl.querySelectorAll('.tl-thumb[data-src]').forEach(img => {
    authFetch(img.dataset.src)
      .then(r => { if(!r.ok) throw new Error(); return r.blob(); })
      .then(b => { img.src = URL.createObjectURL(b); })
      .catch(() => { img.style.display = 'none'; });
  });
}

function renderAppealCard(a){
  const ex = examsList.find(e => e.exam_id === a.exam_id);
  const examName = ex ? ex.exam_title : (a.exam_id || '—');
  let created = '';
  try{ created = a.created_at ? new Date(a.created_at).toLocaleString() : ''; }
  catch(_){ created = a.created_at || ''; }
  const sColor = a.status === 'accepted' ? 'var(--emerald)'
    : a.status === 'rejected' ? 'var(--red)' : 'var(--amber)';
  const isPending = a.status === 'pending';
  const _ctxUrls = Array.isArray(a.evidence_context) ? a.evidence_context : [];
  const _hasInline = !!(a.evidence_primary || _ctxUrls.length);
  const _aThumb = (src, label) =>
    `<img class="tl-thumb appeal-thumb" title="${escAttr(label)}" data-src="${escAttr(src)}" data-action="_showLightbox" data-args='${_jsonArgsForAttr(src, label, created)}' data-error-action="_hideSelf">`;
  const evidenceStrip = _hasInline
    ? `<div class="appeal-evidence">
         <div class="appeal-evidence-label">${_ctxUrls.length ? 'Lead-up to the flag (t-3s → flag)' : 'Flag evidence'}</div>
         <div class="appeal-evidence-row">${_ctxUrls.map((s, ci) => _aThumb(s, `context ${ci + 1} of ${_ctxUrls.length}`)).join('')}${a.evidence_primary ? _aThumb(a.evidence_primary, 'flag moment') : ''}</div>
       </div>`
    : '';
  const evidenceBtn = a.session_key
    ? `<button class="btn btn-secondary btn-sm" type="button" data-action="viewAppealEvidence" data-args='${_jsonArgsForAttr(a.session_key)}'>${_hasInline ? 'Open full timeline' : (a.violation_id ? 'View flag + context' : 'View session')}</button>`
    : '';
  const actions = isPending
    ? `<div class="appeal-actions">
         <textarea id="appeal-note-${escAttr(a.id)}" class="appeal-note" rows="2" placeholder="Note to the student (shown on their appeal). Required to reject."></textarea>
         <div class="appeal-btns">
           <button class="btn btn-secondary btn-sm" type="button" data-action="resolveAppeal" data-args='${_jsonArgsForAttr(a.id, 'rejected')}' style="color:var(--red)">Reject</button>
           <button class="btn btn-primary btn-sm" type="button" data-action="resolveAppeal" data-args='${_jsonArgsForAttr(a.id, 'accepted')}'>Accept</button>
         </div>
       </div>`
    : `<div class="appeal-resolved">
         ${a.resolution ? `<div>Outcome: ${_escHtml(a.resolution.replace(/_/g,' '))}</div>` : ''}
         ${a.teacher_note ? `<div class="appeal-note-readback">“${_escHtml(a.teacher_note)}”</div>` : ''}
       </div>`;
  return `<div class="appeal-card">
    <div class="appeal-head">
      <div>
        <span class="appeal-roll">${_escHtml(a.roll_number || a.student_id || '—')}</span>
        <span class="appeal-type">${_escHtml((a.appeal_type || '').toUpperCase())}</span>
        <span class="appeal-exam">${_escHtml(examName)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:11px;color:var(--text-muted)">${_escHtml(created)}</span>
        <span class="appeal-status" style="color:${sColor}">${_escHtml((a.status || 'pending').toUpperCase())}</span>
      </div>
    </div>
    ${a.description ? `<div class="appeal-desc">${_escHtml(a.description)}</div>` : ''}
    ${evidenceStrip}
    <div class="appeal-foot">
      <div class="appeal-foot-evidence">${evidenceBtn}</div>
      ${actions}
    </div>
  </div>`;
}

async function resolveAppeal(appealId, status){
  const noteEl = document.getElementById(`appeal-note-${appealId}`);
  const teacher_note = ((noteEl && noteEl.value) || '').trim();
  if(status === 'rejected' && !teacher_note){
    if(!(await appConfirm('Reject without a note? A short reason is shown to the student and helps them understand the decision.', 'Reject appeal?', {okText:'Reject anyway'}))) return;
  }
  if(status === 'accepted'){
    if(!(await appConfirm('Accept this appeal? If it disputes a specific flag, that flag is dismissed and the risk score recomputed.', 'Accept appeal?', {okText:'Accept'}))) return;
  }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/appeals/${encodeURIComponent(appealId)}/resolve`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ status, teacher_note }),
    });
    const d = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(_detailText(d, 'Failed to resolve appeal'));
    let msg = status === 'accepted' ? 'Appeal accepted.' : 'Appeal rejected.';
    if(d.resolution === 'flag_dismissed'){
      msg += ' The disputed flag was dismissed';
      msg += (d.risk_score != null) ? ` — the risk score is now ${d.risk_score}.` : '.';
    }
    showModal('Done', msg);
    loadAppeals();
  }catch(e){ showModal('Error', e.message || 'Failed to resolve appeal'); }
}

function viewAppealEvidence(sessionKey){
  // Reuse the forensics timeline — it already renders the primary frame, the
  // phone-cam companion, AND the pre-violation context strip for each flag.
  if(!sessionKey) return;
  openTimelineForSession(sessionKey);
}

// Ported from the dropped React PrivacyPanel: superadmin DPDP data export.
async function sarExport(){
  const el = document.getElementById('sar-result');
  const subj = ((document.getElementById('sar-subject')||{}).value || '').trim();
  const type = (document.getElementById('sar-type')||{}).value || 'student';
  if(!subj){ if(el){ el.textContent = 'Enter an email or account id.'; el.style.color = 'var(--red)'; } return; }
  if(el){ el.textContent = 'Exporting…'; el.style.color = 'var(--text-secondary)'; }
  try{
    const body = { target_user_type: type };
    if(subj.includes('@')) body.target_email = subj; else body.target_user_id = subj;
    const r = await authFetch(`${BASE}/api/v1/admin/sar/export`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(_detailText(d, 'Export failed'));
    const blob = new Blob([JSON.stringify(d, null, 2)], {type:'application/json'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'sar_export.json'; a.click(); URL.revokeObjectURL(a.href);
    if(el){ el.textContent = 'Export downloaded.'; el.style.color = 'var(--emerald)'; }
  }catch(e){ if(el){ el.textContent = e.message; el.style.color = 'var(--red)'; } }
}

// Ported from the dropped React BillingPanel: usage detail (overage + recent
// overage charges) and self-serve cancel. Backend endpoints already exist
// (/api/v1/billing/usage, /api/v1/billing/cancel); legacy had no UI for them.
async function loadBillingUsage(){
  const el = document.getElementById('billing-usage-detail');
  if(!el) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/billing/usage`);
    if(!r.ok) return;
    const u = await r.json();
    let html = `<div style="font-size:12px;color:var(--text-secondary)">Active students this billing period: <strong>${u.students_used||0}</strong> of <strong>${u.plan_limit||0}</strong> included`;
    if((u.overage||0) > 0){
      html += ` · <span style="color:var(--amber)">${u.overage} over plan`;
      if(u.overage_billing_enabled && (u.overage_amount||0) > 0) html += ` (₹${u.overage_amount} overage)`;
      html += `</span>`;
    }
    html += `</div>`;
    // Clarify the two different "students" numbers (roster count up top vs the
    // active-this-period figure billing is based on).
    html += `<div style="font-size:11px;color:var(--text-muted);margin-top:2px">Billed on students who take an exam this month; extras beyond your plan are charged as overage. (The top number is your total roster.)</div>`;
    const charges = u.overage_charges || [];
    if(charges.length){
      html += `<div style="margin-top:6px;font-size:11px;color:var(--text-muted)">Recent overage charges</div><ul style="margin:4px 0 0;padding-left:16px;font-size:11px;color:var(--text-muted)">`;
      charges.slice(0,5).forEach(c=>{
        html += `<li>${_escHtml(String(c.period_start||'').slice(0,10))}: ${c.overage_count||0} student(s) · ₹${c.amount_inr||0} · ${_escHtml(c.status||'')}</li>`;
      });
      html += `</ul>`;
    }
    el.innerHTML = html;
  }catch(e){ /* non-fatal — usage detail is informational */ }
}

async function cancelSubscription(){
  if(!(await appConfirm('Cancel your subscription? You keep access until the end of the current billing period, then it reverts to the free tier. No refund.', 'Cancel plan?', {okText:'Cancel plan'}))) return;
  const resultEl = document.getElementById('upgrade-result');
  try{
    const r = await authFetch(`${BASE}/api/v1/billing/cancel`, {method:'POST'});
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(_detailText(d, 'Cancel failed'));
    if(resultEl){ resultEl.textContent = d.message || 'Subscription cancelled.'; resultEl.style.color = 'var(--emerald)'; }
    loadBilling();
  }catch(e){ if(resultEl){ resultEl.textContent = e.message; resultEl.style.color = 'var(--red)'; } }
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
      throw new Error(_detailText(d, `Billing failed (${billingRes.status})`));
    }
    const b = await billingRes.json();
    planEl.textContent = (b.plan || '--').toUpperCase();
    statusEl.textContent = b.status || '--';
    usageEl.textContent = `${b.student_count || 0}/${b.max_students || 0}`;
    _billingState = { plan: b.plan || '', status: b.status || '' };
    updateBillingTiles(b.plan, b.status);
    loadBillingUsage();
    const _cancelWrap = document.getElementById('billing-cancel-wrap');
    if(_cancelWrap){
      const _st = String(b.status||'').toLowerCase();
      _cancelWrap.style.display = (_ENTITLING_BILLING_STATUSES.has(_st) && _st!=='cancelling') ? '' : 'none';
    }

    // Scheduled-downgrade banner
    const schedEl = document.getElementById('billing-scheduled');
    if(schedEl){
      if(b.scheduled_plan && b.scheduled_plan_effective_at){
        schedEl.hidden = false;
        const _when = fmtDate(b.scheduled_plan_effective_at);
        const _whenTxt = _when ? `on <strong>${escAttr(_when)}</strong>` : 'at the end of your billing period';
        schedEl.innerHTML = `<span class="billing-scheduled-icon">\u23F3</span>
          <span class="billing-scheduled-text">Scheduled to downgrade to <strong>${escAttr(planName(b.scheduled_plan))}</strong> ${_whenTxt}.</span>
          <button class="btn btn-secondary btn-sm billing-scheduled-btn" type="button" data-action="cancelScheduledChange">Keep current plan</button>`;
      }else{
        schedEl.hidden = true;
      }
    }

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
    statusEl.textContent = `Connected as ${_escHtml(d.email)}`;
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
        <span style="font-size:12px;color:${c.linked?'var(--accent-light)':'var(--text-secondary)'}">${_escHtml(c.name)} ${c.section?'('+_escHtml(c.section)+')':''}</span>
      </label>
    `).join('');
    // Populate exam select
    const sel = document.getElementById('google-exam-select');
    const exams = document.querySelectorAll('#exam-select option');
    if(sel){
      const curr = sel.value;
      sel.innerHTML = '<option value="">Select exam…</option>';
      document.querySelectorAll('#exam-select option').forEach(o => {
        if(o.value) sel.innerHTML += `<option value="${escAttr(o.value)}">${_escHtml(o.text)}</option>`;
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
      body: JSON.stringify({course_id: courseId.dataset.courseId, exam_id: examId}),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    let msg = `Imported ${d.imported} student(s)`;
    if(d.updated) msg += `, updated ${d.updated}`;
    if(d.batch) msg += ` into cohort "${d.batch}"`;
    msg += ` (${d.total} total in course).`;
    if(d.warning) msg += ` ⚠️ ${d.warning}`;
    resultEl.textContent = msg;
    // The synced class becomes a cohort (students.batch) — refresh the batch
    // pickers so it's immediately selectable in "Restrict to Batches/Cohorts"
    // and the cohort-enrollment link without a page reload.
    if((d.imported || d.updated) && typeof loadExamBatches === 'function'){
      try{ loadExamBatches(); }catch(_){}
    }
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
        throw new Error(_detailText(err, ('HTTP '+r.status)));
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
      const reauth_token = await _getReauthToken('clear live sessions');
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
        throw new Error(_detailText(err, ('HTTP '+r.status)));
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
    const reauth_token = await _getReauthToken('force-submit this session');
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
    // Scope the count to the selected exam when one is active. The
    // backend reads per-exam membership from student_invites; with no
    // exam_id it returns the teacher-wide roster count (legacy default).
    const examParam = (typeof currentExamId!=='undefined' && currentExamId)
      ? `?exam_id=${encodeURIComponent(currentExamId)}` : '';
    const r=await authFetch(`${BASE}/api/v1/admin/registered-count${examParam}`);
    if(!r.ok) return;
    const d=await r.json();
    document.getElementById('tools-registered').textContent=d.count;
    const lbl=document.getElementById('tools-registered-label');
    if(lbl) lbl.textContent = (d.scope==='exam') ? 'Registered (this exam)' : 'Registered Students';
  }catch(e){}
}

// ── TEACHER-SIDE ROSTER REFRESH ────────────────────────────────
// Student registration happens outside the teacher dashboard, so there is
// no local click event to trigger a refresh. Keep the roster-facing surfaces
// fresh without touching heavy live monitoring paths: count always refreshes;
// History and invite rows refresh only when the teacher is looking at them.
const ROSTER_REFRESH_MS = 15000;
let _rosterRefreshInFlight = false;

function _activeTabName(){
  const active = document.querySelector('.tab.active');
  return active ? active.dataset.tab : '';
}

function _startRosterAutoRefresh(){
  if(autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(()=>refreshRosterSurfaces(), ROSTER_REFRESH_MS);
}

async function refreshRosterSurfaces(opts = {}){
  if(!currentTeacherProfile) return;
  if(_loggedOut || _rosterRefreshInFlight) return;
  if(document.visibilityState === 'hidden' && !opts.force) return;
  _rosterRefreshInFlight = true;
  try{
    const tab = _activeTabName();
    const jobs = [loadRegisteredCount()];
    if(tab === 'history'){
      // Avoid rerendering the table while the teacher is typing a search term.
      if(!(document.activeElement && document.activeElement.id === 'history-search')){
        jobs.push(refreshStudentList());
      }
    }
    if(tab === 'tools'){
      jobs.push(loadInvites());
    }
    await Promise.allSettled(jobs);
  }finally{
    _rosterRefreshInFlight = false;
  }
}

// ── EXAM SCHEDULE ───────────────────────────────────────────────
async function loadSchedule(){
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/exam-schedule${_examQuery('?')}`);
    if(!r.ok) return;
    const d=await r.json();
    if(d.starts_at) document.getElementById('schedule-start').value=utcToLocalInput(d.starts_at);
    if(d.ends_at) document.getElementById('schedule-end').value=utcToLocalInput(d.ends_at);
    const ej=document.getElementById('schedule-early-join');
    if(ej && d.early_join_minutes!=null) ej.value=d.early_join_minutes;
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
    const _ejEl=document.getElementById('schedule-early-join');
    let _ej=_ejEl?parseInt(_ejEl.value,10):null;
    if(_ej==null||Number.isNaN(_ej)) _ej=null; else _ej=Math.max(0,Math.min(_ej,240));
    const r=await authFetch(`${BASE}/api/v1/admin/exam-schedule`,{
      method:'POST',
      body:JSON.stringify({starts_at:localInputToUtc(starts),ends_at:localInputToUtc(ends),early_join_minutes:_ej,exam_id:currentExamId})
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json().catch(()=>({}));
    st.style.color='var(--emerald)';
    st.textContent='Schedule saved!';
    setTimeout(()=>loadSchedule(),1000);
    // If students already submitted this exam, a reschedule alone won't let them
    // retake — offer (never force) a one-click reset.
    if(d && d.attempted_count > 0){
      await offerResetAfterReschedule(d.attempted_count);
    }
  }catch(e){
    st.style.color='var(--red)';
    st.textContent='Failed: '+e.message;
  }
}

// After a reschedule on an exam that some students already submitted: warn the
// teacher and offer a one-click reset so those students can retake within the
// new window. The reschedule itself already saved — declining just leaves the
// finished attempts as-is (the teacher can still reset later from Sessions).
async function offerResetAfterReschedule(n){
  const who = n === 1 ? '1 student has' : `${n} students have`;
  const subj = n === 1 ? 'this student' : `these ${n} students`;
  const ok = await appConfirm(
    `${who} already submitted this exam. Rescheduling on its own won't let them retake — their finished attempt still shows as completed.\n\n`+
    `Reset ${subj} now so they can re-enter and retake the exam? Their saved answers are kept, and you can also do this later from the Sessions view.`,
    'Students already attempted',
    {okText:`Reset ${n} for retake`, cancelText:'Not now'}
  );
  if(!ok) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exam/${encodeURIComponent(currentExamId)}/reset-attempts`,
                              {method:'POST', body:'{}'});
    const d = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(_detailText(d, `HTTP ${r.status}`));
    const c = d.reset_count || 0;
    showModal('Students reset', `${c} student${c===1?'':'s'} can now re-enter and retake the exam.`);
  }catch(e){
    showModal('Reset failed', 'Could not reset the attempts: '+e.message+'. You can retry from the Sessions view.');
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

// Ported from the dropped React dashboard (SensitivityPanel): per-exam
// AI flagging strictness. Reads/writes /api/v1/admin/proctoring-sensitivity,
// scoped to the currently selected exam.
async function loadSensitivity(){
  const sel = document.getElementById('sensitivity-select');
  if(!sel) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/proctoring-sensitivity${_examQuery('?')}`);
    if(!r.ok) return;
    const d = await r.json();
    if(d.proctoring_sensitivity) sel.value = d.proctoring_sensitivity;
  }catch(e){ /* silent */ }
}

async function saveSensitivity(){
  const sel = document.getElementById('sensitivity-select');
  const st = document.getElementById('sensitivity-status');
  if(!sel) return;
  const body = { exam_id: currentExamId, proctoring_sensitivity: sel.value };
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/proctoring-sensitivity`,{
      method:'POST',
      body: JSON.stringify(body),
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    if(st){ st.style.color = 'var(--emerald)'; st.textContent = 'Saved.'; }
  }catch(e){
    if(st){ st.style.color = 'var(--red)'; st.textContent = 'Failed to save: '+e.message; }
  }
}

// Ported from the dropped React dashboard (AudioKeywordsPanel): per-exam
// spoken-word flagging. Reads/writes /api/v1/admin/audio-keywords. The
// input is comma-separated; the API expects an array of strings.
async function loadAudioKeywords(){
  const inp = document.getElementById('audio-keywords-input');
  if(!inp) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/audio-keywords${_examQuery('?')}`);
    if(!r.ok) return;
    const d = await r.json();
    inp.value = Array.isArray(d.audio_keywords) ? d.audio_keywords.join(', ') : '';
  }catch(e){ /* silent */ }
}

async function saveAudioKeywords(){
  const inp = document.getElementById('audio-keywords-input');
  const st = document.getElementById('audio-keywords-status');
  if(!inp) return;
  const list = inp.value.split(',').map(s=>s.trim()).filter(Boolean);
  const body = { exam_id: currentExamId, audio_keywords: list, audio_keywords_language: 'en' };
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/audio-keywords`,{
      method:'POST',
      body: JSON.stringify(body),
    });
    if(!r.ok){
      let msg = `HTTP ${r.status}`;
      try{ const e = await r.json(); if(e && e.detail) msg = e.detail; }catch(_){}
      throw new Error(msg);
    }
    const d = await r.json();
    if(Array.isArray(d.audio_keywords)) inp.value = d.audio_keywords.join(', ');
    if(st){ st.style.color = 'var(--emerald)'; st.textContent = 'Saved.'; }
  }catch(e){
    if(st){ st.style.color = 'var(--red)'; st.textContent = 'Failed to save: '+e.message; }
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
let qData = [];       // array of question objects (with correct answers) — excludes coding
let _codingQuestions = []; // array of coding question summaries {id,question,options,...}
let qPreviewMode = false;
let qDirty = false;   // unsaved changes flag

// loadQuestions() only fires on a tab-switch INTO the Questions tab
// (switchTab) when qData is empty. When the active exam changes while the
// teacher is already viewing Questions, nothing re-triggers it, so the
// editor keeps showing the previous exam's title + questions. Call this
// after any exam change to resync the editor in that case. (When NOT on
// the Questions tab we leave qData empty so switchTab reloads it on the
// next visit — the existing behaviour.)
function _reloadQuestionsIfActive(){
  const p = document.getElementById('panel-questions');
  if(p && p.classList.contains('active')) loadQuestions();
}

async function loadQuestions(){
  const el = document.getElementById('q-editor');
  el.innerHTML='<div class="loading-msg"><span class="spinner"></span> Loading questions...</div>';
  document.getElementById('q-save-msg').textContent='';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/questions${_examQuery('?')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const allQuestions = d.questions || [];
    qData = [];
    _codingQuestions = [];
    for(const q of allQuestions){
      if((q.question_type||'').toLowerCase()==='coding'){
        _codingQuestions.push(q);
      }else{
        qData.push(q);
      }
    }
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
  const dur = parseInt(document.getElementById('q-duration').value, 10)||60;
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
  if(!['mcq_single','mcq_multi','true_false','short_answer','numeric'].includes(t)) t='mcq_single';
  q.question_type=t;
  return t;
}
function qTypeLabel(t){
  if(t==='mcq_multi') return 'Multi-select';
  if(t==='true_false') return 'True / False';
  if(t==='short_answer') return 'Short answer (AI-graded)';
  if(t==='numeric') return 'Numeric (range)';
  return 'Single choice';
}
// Parse a "range:MIN:MAX" correct value into {min, max} display strings.
function qParseRange(correct){
  const c = String(correct||'');
  if(c.toLowerCase().startsWith('range:')){
    const p = c.split(':');
    return { min: p[1]!=null?p[1]:'', max: p[2]!=null?p[2]:'' };
  }
  return { min:'', max:'' };
}
function qBuildImageUrl(u){
  if(!u) return '';
  // Server-issued relative URLs need the admin Bearer token. We use a
  // blob fetched via authFetch so we never leak the token into the DOM.
  return u;
}

// ── CODING QUESTION AUTHORING FORM ───────────────────────────────
let _editingCodingId = null;   // question_id string when editing existing

// Auto-grow: textareas in the coding modal track their content height instead of
// a manual resize handle. A delegated input listener handles typing; explicit
// calls size programmatically-set content (edit-load, starter swap, new cards).
function _autoGrow(ta){
  if(!ta) return;
  ta.style.height = 'auto';
  ta.style.height = Math.max(ta.scrollHeight, 32) + 'px';
}
function _codingSizeTextareas(){
  document.querySelectorAll('#coding-form-overlay textarea').forEach(_autoGrow);
}
let _codingAutogrowWired = false;
function _wireCodingAutogrow(){
  if(_codingAutogrowWired) return; _codingAutogrowWired = true;
  document.getElementById('coding-form-overlay').addEventListener('input', (e)=>{
    if(e.target && e.target.tagName === 'TEXTAREA') _autoGrow(e.target);
  });
}

function showCodingForm(questionId, seed){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  _wireCodingAutogrow();
  _editingCodingId = questionId ? String(questionId) : null;
  _codingResetForm();
  document.getElementById('coding-ai-banner-area').innerHTML = '';
  document.getElementById('coding-gen-panel-area').innerHTML = '';
  document.getElementById('coding-ref-solution-area').style.display = 'none';
  document.getElementById('coding-form-title').textContent = questionId ? 'Edit Coding Question' : 'New Coding Question';
  document.getElementById('coding-save-msg').textContent = '';
  if(questionId){
    _loadCodingForEdit(questionId);
  }else if(seed){
    _codingPopulateForm(seed);   // carried-over wizard draft (same shape as edit-load)
    if(!document.getElementById('coding-tc-tbody').children.length) codingAddTestCase();
  }else{
    codingAddTestCase();
  }
  document.getElementById('coding-form-overlay').style.display = 'flex';
  requestAnimationFrame(_codingSizeTextareas);   // size once visible (scrollHeight needs layout)
}

function hideCodingForm(){
  document.getElementById('coding-form-overlay').style.display = 'none';
  _editingCodingId = null;
}

function _codingResetForm(){
  document.getElementById('coding-statement').value = '';
  _codingStarter = {};
  _codingStarterLang = null;
  _codingSetLangs(['javascript']);   // also renders starter tabs + default
  document.getElementById('coding-marks').value = 10;
  document.getElementById('coding-marks-policy').value = 'partial';
  document.getElementById('coding-time-limit').value = 5;   // seconds
  const tbody = document.getElementById('coding-tc-tbody');
  if(tbody) tbody.innerHTML = '';
  document.getElementById('coding-ref-solution-code').textContent = '';
  document.getElementById('coding-ref-solution-area').style.display = 'none';
  _updateTcHint();
}

function _codingSetLangs(arr){
  const hidden = document.getElementById('coding-langs');
  hidden.value = JSON.stringify(arr);
  document.querySelectorAll('#coding-lang-chips .lang-chip').forEach(el => {
    el.classList.toggle('selected', arr.includes(el.dataset.lang));
  });
  _renderStarterTabs();
}

function codingToggleLang(lang){
  const hidden = document.getElementById('coding-langs');
  const current = JSON.parse(hidden.value || '[]');
  const idx = current.indexOf(lang);
  if(idx >= 0) current.splice(idx, 1);
  else current.push(lang);
  _codingSetLangs(current);
}

// ── Per-language starter code ───────────────────────────────────────────────
// Each allowed language keeps its own starter template (the editor scaffolding
// the student opens with). Stored as a {lang: code} map; a tab switches which
// language's code the textarea is bound to.
const _STARTER_LANG_LABELS = {javascript:'JavaScript', typescript:'TypeScript',
  python:'Python', c:'C', cpp:'C++', java:'Java'};
const _STARTER_DEFAULTS = {
  javascript: '// Read stdin, write your answer to stdout.\nconst data = require("fs").readFileSync(0, "utf8").trim();\n',
  typescript: '// Read stdin, write your answer to stdout.\nconst data: string = require("fs").readFileSync(0, "utf8").trim();\n',
  python: '# Read stdin, print your answer.\nimport sys\ndata = sys.stdin.read().split()\n',
  c: '#include <stdio.h>\n\nint main(void) {\n    // read from stdin, print to stdout\n    return 0;\n}\n',
  cpp: '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    // read from stdin, print to stdout\n    return 0;\n}\n',
  java: 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        // print your answer to stdout\n    }\n}\n',
};
let _codingStarter = {};          // lang -> code (undefined = never visited)
let _codingStarterLang = null;    // currently-open tab

function _codingFlushStarter(){
  if(_codingStarterLang != null){
    _codingStarter[_codingStarterLang] = document.getElementById('coding-starter-code').value;
  }
}

function _renderStarterTabs(){
  const tabs = document.getElementById('coding-starter-tabs');
  if(!tabs) return;
  _codingFlushStarter();   // preserve the open tab's edits before re-rendering
  const langs = JSON.parse(document.getElementById('coding-langs').value || '[]');
  // Keep the active tab valid; if its language was removed, fall back to the first.
  if(!langs.includes(_codingStarterLang)) { _codingStarterLang = langs[0] || null; }
  tabs.innerHTML = langs.map(l =>
    `<span class="starter-lang-tab${l===_codingStarterLang?' active':''}" data-action="codingStarterTab" data-args='${_jsonArgsForAttr(l)}'>${_escHtml(_STARTER_LANG_LABELS[l]||l)}</span>`
  ).join('');
  _loadStarterForActive();
}

function _loadStarterForActive(){
  const ta = document.getElementById('coding-starter-code');
  if(_codingStarterLang == null){ ta.value = ''; ta.disabled = true; return; }
  ta.disabled = false;
  if(_codingStarter[_codingStarterLang] === undefined){
    _codingStarter[_codingStarterLang] = _STARTER_DEFAULTS[_codingStarterLang] || '';
  }
  ta.value = _codingStarter[_codingStarterLang];
  _autoGrow(ta);
}

function codingStarterTab(lang){
  _codingFlushStarter();        // save what's in the box before switching
  _codingStarterLang = lang;
  document.querySelectorAll('#coding-starter-tabs .starter-lang-tab').forEach(el => {
    el.classList.toggle('active', el.textContent === (_STARTER_LANG_LABELS[lang]||lang));
  });
  _loadStarterForActive();
}

function _tcCardHtml(idx, c){
  c = c || {};
  const hiddenSel = (c.visibility === 'hidden' || !c.visibility) ? ' selected' : '';
  const sampleSel = c.visibility === 'sample' ? ' selected' : '';
  const ft = c.float_tolerance != null ? escAttr(String(c.float_tolerance)) : '';
  return `<div class="tc-card-head">
      <span class="tc-num">Test ${idx+1}</span>
      <select class="tc-visibility" data-change-action="_updateTcHint">
        <option value="sample"${sampleSel}>Sample (visible to student)</option>
        <option value="hidden"${hiddenSel}>Hidden (graded)</option>
      </select>
      <input type="text" class="tc-float-tol" placeholder="float ± (optional)" value="${ft}">
      <button class="tc-row-remove" data-action="codingRemoveTestCase" data-args='${_jsonArgsForAttr(idx)}' title="Remove test case">×</button>
    </div>
    <div><span class="tc-field-label">Input (stdin)</span><textarea rows="2" class="tc-input" placeholder="passed to the program's standard input">${_escHtml(c.input||'')}</textarea></div>
    <div><span class="tc-field-label">Expected output (stdout)</span><textarea rows="2" class="tc-expected" placeholder="exact text the program must print">${_escHtml(c.expected_output||'')}</textarea></div>`;
}

function codingAddTestCase(){
  const wrap = document.getElementById('coding-tc-tbody');
  const idx = wrap.children.length;
  const card = document.createElement('div');
  card.className = 'tc-card';
  card.dataset.tcidx = idx;
  card.innerHTML = _tcCardHtml(idx, {});
  wrap.appendChild(card);
  _updateTcHint();
}

function codingRemoveTestCase(idx){
  const wrap = document.getElementById('coding-tc-tbody');
  const card = wrap.querySelector(`[data-tcidx="${idx}"]`);
  if(card) card.remove();
  _renumberTcRows();
  _updateTcHint();
}

function _renumberTcRows(){
  const wrap = document.getElementById('coding-tc-tbody');
  Array.from(wrap.children).forEach((card, i) => {
    card.dataset.tcidx = i;
    const num = card.querySelector('.tc-num');
    if(num) num.textContent = `Test ${i + 1}`;
    const btn = card.querySelector('.tc-row-remove');
    if(btn) btn.setAttribute('data-args', _jsonArgsForAttr(i));
  });
}

function _updateTcHint(){
  const tbody = document.getElementById('coding-tc-tbody');
  const hidden = Array.from(tbody.querySelectorAll('.tc-visibility'))
    .filter(sel => sel.value === 'hidden').length;
  const hint = document.getElementById('coding-tc-hint');
  if(hint){
    hint.textContent = hidden === 0 ? 'needs ≥1 hidden case' : `${hidden} hidden, ${(tbody.children.length||0)-hidden} sample`;
    hint.style.color = hidden === 0 ? 'var(--sev-error-fg)' : 'var(--text-muted)';
  }
}

async function _loadCodingForEdit(questionId){
  const saveMsg = document.getElementById('coding-save-msg');
  saveMsg.textContent = 'Loading...';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/coding-question?question_id=${encodeURIComponent(questionId)}`);
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      saveMsg.textContent = _detailText(d, 'Failed to load coding question');
      return;
    }
    const data = await r.json();
    _codingPopulateForm(data);
    saveMsg.textContent = '';
  }catch(e){
    saveMsg.textContent = 'Load failed';
  }
}

function _codingPopulateForm(data){
  document.getElementById('coding-statement').value = data.question || '';
  const opts = data.options || {};
  const langs = opts.allowed_languages || ['javascript'];
  // starter_code: a {lang:code} map (new) or a single string (legacy → seed it
  // for every allowed language so nothing is lost; teacher can then edit per-lang).
  const sc = opts.starter_code;
  _codingStarter = {};
  _codingStarterLang = null;
  if(sc && typeof sc === 'object'){
    _codingStarter = Object.assign({}, sc);
  }else if(typeof sc === 'string' && sc){
    langs.forEach(l => { _codingStarter[l] = sc; });
  }
  _codingSetLangs(langs);   // renders starter tabs + loads the active language's code
  document.getElementById('coding-marks').value = opts.marks || 10;
  document.getElementById('coding-marks-policy').value = (opts.marks_policy || 'partial');
  document.getElementById('coding-time-limit').value = (opts.time_limit_ms || 5000) / 1000;
  codingRenderTestCases(data.test_cases || []);
  if(data.reference_solution){
    document.getElementById('coding-ref-solution-code').textContent = data.reference_solution;
    document.getElementById('coding-ref-solution-area').style.display = '';
  }
  requestAnimationFrame(_codingSizeTextareas);   // size loaded content to fit
}

function codingRenderTestCases(cases){
  const wrap = document.getElementById('coding-tc-tbody');
  wrap.innerHTML = '';
  cases.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = 'tc-card';
    card.dataset.tcidx = i;
    card.innerHTML = _tcCardHtml(i, c);
    wrap.appendChild(card);
  });
  _updateTcHint();
}

function _codingCollectForm(){
  const statement = document.getElementById('coding-statement').value.trim();
  const langs = JSON.parse(document.getElementById('coding-langs').value || '[]');
  const marks = parseInt(document.getElementById('coding-marks').value, 10) || 1;
  const marksPolicy = document.getElementById('coding-marks-policy').value;
  // UI is in seconds; the API contract stays in ms.
  const timeLimitSec = parseFloat(document.getElementById('coding-time-limit').value) || 5;
  const timeLimitMs = Math.round(timeLimitSec * 1000);
  // Per-language starter map: flush the open tab, then keep only allowed langs.
  _codingFlushStarter();
  const langsSel = JSON.parse(document.getElementById('coding-langs').value || '[]');
  const starterCode = {};
  langsSel.forEach(l => { if(_codingStarter[l] != null) starterCode[l] = _codingStarter[l]; });
  const tbody = document.getElementById('coding-tc-tbody');
  const testCases = [];
  const errors = [];
  Array.from(tbody.children).forEach((tr, i) => {
    const input = tr.querySelector('.tc-input').value;
    const expected = tr.querySelector('.tc-expected').value;
    const visibility = tr.querySelector('.tc-visibility').value;
    const ftRaw = tr.querySelector('.tc-float-tol').value.trim();
    let floatTolerance;
    if(ftRaw){
      floatTolerance = Number(ftRaw);
      // A non-numeric ± serializes to JSON null and the server treats null as "no
      // tolerance" — silently grading a float question by exact match. Surface it
      // instead of dropping it. (Number() is stricter than parseFloat: "1.2x"->NaN.)
      if(!Number.isFinite(floatTolerance) || floatTolerance < 0){
        errors.push(`Test case ${i+1}: float tolerance must be a non-negative number.`);
        floatTolerance = undefined;
      }
    }
    testCases.push({idx: i, input, expected_output: expected, visibility, ...(floatTolerance !== undefined ? {float_tolerance: floatTolerance} : {})});
  });
  if(!statement) errors.push('Problem statement is required.');
  if(!langs.length) errors.push('At least one language must be selected.');
  if(marks < 1 || marks > 100) errors.push('Marks must be between 1 and 100.');
  if(!['partial','all_or_nothing'].includes(marksPolicy)) errors.push('Invalid marks policy.');
  if(timeLimitSec < 1 || timeLimitSec > 15) errors.push('Run time must be between 1 and 15 seconds.');
  if(!testCases.length) errors.push('At least one test case is required.');
  const hidden = testCases.filter(c => c.visibility === 'hidden').length;
  if(!hidden) errors.push('At least one hidden test case is required.');
  if(testCases.length > 50) errors.push('Maximum 50 test cases.');
  for(let i=0;i<testCases.length;i++){
    if(!testCases[i].expected_output) errors.push(`Test case ${i+1}: expected_output is required.`);
  }
  return {statement, langs, marks, marksPolicy, timeLimitMs, starterCode, testCases, errors};
}

async function codingSave(){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  const saveMsg = document.getElementById('coding-save-msg');
  const {statement, langs, marks, marksPolicy, timeLimitMs, starterCode, testCases, errors} = _codingCollectForm();
  if(errors.length){
    saveMsg.innerHTML = `<span style="color:var(--red)">${_escHtml(errors.join('; '))}</span>`;
    return;
  }
  const payload = {
    exam_id: currentExamId,
    question: statement,
    options: {
      allowed_languages: langs,
      marks,
      marks_policy: marksPolicy,
      time_limit_ms: timeLimitMs,
      starter_code: starterCode,
    },
    test_cases: testCases.map(({idx, input, expected_output, visibility, float_tolerance}) => ({
      input, expected_output, visibility, ...(float_tolerance !== undefined ? {float_tolerance} : {}),
    })),
  };
  if(_editingCodingId) payload.question_id = _editingCodingId;
  saveMsg.innerHTML = '<span class="spinner"></span> Saving...';
  try{
    const method = _editingCodingId ? 'PUT' : 'POST';
    const r = await authFetch(`${BASE}/api/v1/admin/coding-question`, {method, body: JSON.stringify(payload)});
    const d = await r.json();
    if(!r.ok){
      saveMsg.innerHTML = `<span style="color:var(--red)">${_escHtml(_detailText(d, 'Save failed'))}</span>`;
      return;
    }
    saveMsg.innerHTML = `<span style="color:var(--emerald)">Saved! Question ID: ${_escHtml(d.question_id)} (${d.test_cases} cases, ${d.sample} sample / ${d.hidden} hidden)</span>`;
    hideCodingForm();
    loadQuestions();
  }catch(e){
    saveMsg.innerHTML = `<span style="color:var(--red)">Save failed: ${_escHtml(e.message)}</span>`;
  }
}

function editCodingQuestion(questionId){
  showCodingForm(questionId);
}

// ════════════════════════════════════════════════════════════════════
//  Guided coding-question wizard — a friendlier path to the SAME payload
//  codingSave builds. Self-contained: collects into _cwiz, validates per step,
//  POSTs to the coding-question endpoint. "Advanced editor" escapes to the modal.
// ════════════════════════════════════════════════════════════════════
const _CWIZ_TITLES = ['Problem','Languages','Examples','Hidden tests','Review'];
const _CWIZ_LANGS = [['javascript','JavaScript'],['typescript','TypeScript'],['python','Python'],['c','C'],['cpp','C++'],['java','Java']];
let _cwiz = null, _cwizAutogrowWired = false, _cwizBusy = false;

function _cwizReset(){
  _cwiz = { step:0, statement:'', langs:['python'],
            samples:[{input:'',expected:''}], hidden:[{input:'',expected:''}],
            marks:10, timeSec:5, policy:'partial' };
  _cwizBusy = false;
}
function showCodingWizard(){
  if(!currentExamId){ showModal('Select an exam first.'); return; }
  _cwizReset();
  if(!_cwizAutogrowWired){ _cwizAutogrowWired = true;
    document.getElementById('coding-wizard-overlay').addEventListener('input', (e)=>{
      if(e.target && e.target.tagName === 'TEXTAREA') _autoGrow(e.target); });
  }
  document.getElementById('coding-wizard-overlay').style.display = 'flex';
  _cwizRender();
}
function hideCodingWizard(){ document.getElementById('coding-wizard-overlay').style.display = 'none'; }

// Build the {question, options, test_cases} seed (the exact shape the GET edit-load
// returns, so the advanced form's _codingPopulateForm consumes it unchanged) from
// the current wizard draft — used when the teacher escapes to the full editor.
function _cwizToFormSeed(){
  const starter_code = {}; _cwiz.langs.forEach(l => { starter_code[l] = _STARTER_DEFAULTS[l] || ''; });
  // Carry any case with content (even half-filled) so the teacher keeps their work;
  // the advanced form validates on save.
  const mk = (arr, vis) => arr
    .filter(c => (c.input || '').trim() || (c.expected || '').trim())
    .map(c => ({input: c.input, expected_output: c.expected, visibility: vis}));
  return {
    question: _cwiz.statement,
    options: {
      allowed_languages: _cwiz.langs.slice(),
      marks: _cwiz.marks,
      marks_policy: _cwiz.policy,
      time_limit_ms: Math.round(_cwiz.timeSec * 1000),
      starter_code,
    },
    test_cases: [...mk(_cwiz.samples, 'sample'), ...mk(_cwiz.hidden, 'hidden')],
  };
}
function cwizUseAdvanced(){
  _cwizSaveStep();                 // capture the open step's edits first
  const seed = _cwizToFormSeed();
  hideCodingWizard();
  showCodingForm(null, seed);      // carry the draft over instead of discarding it
}

function _cwizCasesHtml(list, key, label){
  return list.map((c,i)=>`
    <div class="tc-card">
      <div class="tc-card-head"><span class="tc-num">${label} ${i+1}</span>
        <button class="tc-row-remove" data-action="cwizRemoveCase" data-args='${_jsonArgsForAttr(key,i)}' title="Remove">×</button></div>
      <div><span class="tc-field-label">Input (stdin)</span><textarea rows="2" class="cwc-input">${_escHtml(c.input||'')}</textarea></div>
      <div><span class="tc-field-label">Expected output</span><textarea rows="2" class="cwc-expected">${_escHtml(c.expected||'')}</textarea></div>
    </div>`).join('');
}
function _cwizStepHtml(step){
  if(step===0) return `<div class="cwiz-h">What problem will students solve?</div>
    <div class="cwiz-tip"><span>💡</span><div>Write it like a short story: what the program <b>reads</b>, what it must <b>print</b>, and a tiny example. Students read this exactly as you write it.</div></div>
    <textarea id="cwiz-statement" rows="6" placeholder="e.g. Read two whole numbers on one line and print their sum.&#10;&#10;Example: input &quot;3 5&quot; → output &quot;8&quot;.">${_escHtml(_cwiz.statement)}</textarea>`;
  if(step===1){
    const chips=_CWIZ_LANGS.map(([k,l])=>`<span class="lang-chip${_cwiz.langs.includes(k)?' selected':''}" data-action="cwizToggleLang" data-args='${_jsonArgsForAttr(k)}'>${l}</span>`).join('');
    return `<div class="cwiz-h">Which languages can students use?</div>
      <div class="cwiz-sub">Pick one or more — students choose from these in the exam. We'll pre-fill a starter template for each.</div>
      <div class="lang-chips">${chips}</div>`;
  }
  if(step===2) return `<div class="cwiz-h">Add a worked example or two</div>
    <div class="cwiz-tip"><span>🔎</span><div>A <b>test case</b> is an <b>input</b> we feed the program (its stdin) and the <b>exact output</b> it must print (stdout). These <b>sample</b> cases are shown to students as worked examples.</div></div>
    <div class="cwiz-eg">Example — "add two numbers":\nInput:           3 5\nExpected output: 8</div>
    <div class="test-case-cards" id="cwiz-samples">${_cwizCasesHtml(_cwiz.samples,'sample','Example')}</div>
    <button class="tc-add-btn" data-action="cwizAddCase" data-args='["sample"]'>+ Add example</button>`;
  if(step===3) return `<div class="cwiz-h">Add the hidden tests that grade the answer</div>
    <div class="cwiz-tip"><span>🔒</span><div><b>Hidden</b> tests grade the answer — students never see them. Cover the tricky inputs (big numbers, edge cases, empty input). You need at least one.</div></div>
    <div class="test-case-cards" id="cwiz-hidden">${_cwizCasesHtml(_cwiz.hidden,'hidden','Hidden')}</div>
    <button class="tc-add-btn" data-action="cwizAddCase" data-args='["hidden"]'>+ Add hidden test</button>`;
  const langNames=_cwiz.langs.map(k=>(_CWIZ_LANGS.find(x=>x[0]===k)||[k,k])[1]).join(', ')||'—';
  return `<div class="cwiz-h">Almost done — set the basics</div>
    <div class="field-row">
      <div><label for="cwiz-marks">Marks</label><input type="number" id="cwiz-marks" min="1" max="100" value="${_cwiz.marks}"></div>
      <div><label for="cwiz-time">Run time / test (sec)</label><input type="number" id="cwiz-time" min="1" max="15" step="0.5" value="${_cwiz.timeSec}"></div>
      <div><label for="cwiz-policy">Scoring</label><select id="cwiz-policy">
        <option value="partial"${_cwiz.policy==='partial'?' selected':''}>Partial credit</option>
        <option value="all_or_nothing"${_cwiz.policy==='all_or_nothing'?' selected':''}>All or nothing</option></select></div>
    </div>
    <div class="cwiz-review" style="margin-top:12px">
      <div class="r"><b>Languages</b><span>${_escHtml(langNames)}</span></div>
      <div class="r"><b>Sample tests</b><span>${_cwiz.samples.filter(c=>c.expected.trim()).length} shown to students</span></div>
      <div class="r"><b>Hidden tests</b><span>${_cwiz.hidden.filter(c=>c.expected.trim()).length} graded</span></div>
    </div>`;
}
function _cwizRender(){
  document.getElementById('cwiz-steps').innerHTML = _CWIZ_TITLES.map((t,i)=>
    `<span class="cwiz-dot${i===_cwiz.step?' active':''}${i<_cwiz.step?' done':''}">${i+1}. ${_escHtml(t)}</span>`).join('');
  document.getElementById('cwiz-body').innerHTML = _cwizStepHtml(_cwiz.step);
  document.getElementById('cwiz-back').style.visibility = _cwiz.step===0 ? 'hidden' : 'visible';
  document.getElementById('cwiz-next').textContent = _cwiz.step===_CWIZ_TITLES.length-1 ? 'Create question' : 'Next →';
  // Re-enable nav on every render (clears any disabled state left by a prior create).
  document.getElementById('cwiz-next').disabled = false;
  document.getElementById('cwiz-back').disabled = false;
  document.getElementById('cwiz-msg').textContent = '';
  requestAnimationFrame(()=>document.querySelectorAll('#coding-wizard-overlay textarea').forEach(_autoGrow));
}
function _cwizReadCases(id){
  const wrap=document.getElementById(id); if(!wrap) return [];
  return Array.from(wrap.children).map(card=>({
    input: card.querySelector('.cwc-input').value, expected: card.querySelector('.cwc-expected').value }));
}
function _cwizSaveStep(){
  const s=_cwiz.step;
  if(s===0){ const t=document.getElementById('cwiz-statement'); if(t) _cwiz.statement=t.value; }
  else if(s===2){ _cwiz.samples=_cwizReadCases('cwiz-samples'); }
  else if(s===3){ _cwiz.hidden=_cwizReadCases('cwiz-hidden'); }
  else if(s===4){
    // Clamp to the server's accepted ranges (marks 1..100, run time 1..15s) so an
    // out-of-range entry is corrected here instead of bouncing off a backend 400.
    _cwiz.marks=Math.min(100, Math.max(1, parseInt(document.getElementById('cwiz-marks').value,10)||1));
    _cwiz.timeSec=Math.min(15, Math.max(1, parseFloat(document.getElementById('cwiz-time').value)||5));
    _cwiz.policy=document.getElementById('cwiz-policy').value;
  }
}
function cwizNext(){
  _cwizSaveStep();
  let err=null;
  if(_cwiz.step===0 && !_cwiz.statement.trim()) err='Add a problem statement to continue.';
  else if(_cwiz.step===1 && !_cwiz.langs.length) err='Pick at least one language.';
  else if(_cwiz.step===3 && !_cwiz.hidden.some(c=>c.expected.trim())) err='Add at least one hidden test with an expected output.';
  if(err){ document.getElementById('cwiz-msg').innerHTML=`<span style="color:var(--red)">${_escHtml(err)}</span>`; return; }
  if(_cwiz.step < _CWIZ_TITLES.length-1){ _cwiz.step++; _cwizRender(); } else { _cwizFinish(); }
}
function cwizBack(){ _cwizSaveStep(); if(_cwiz.step>0){ _cwiz.step--; _cwizRender(); } }
function cwizToggleLang(k){ _cwizSaveStep(); const i=_cwiz.langs.indexOf(k); if(i>=0) _cwiz.langs.splice(i,1); else _cwiz.langs.push(k); _cwizRender(); }
function cwizAddCase(kind){ _cwizSaveStep(); (kind==='sample'?_cwiz.samples:_cwiz.hidden).push({input:'',expected:''}); _cwizRender(); }
function cwizRemoveCase(kind, i){ _cwizSaveStep(); const arr=(kind==='sample'?_cwiz.samples:_cwiz.hidden); arr.splice(i,1); if(!arr.length) arr.push({input:'',expected:''}); _cwizRender(); }

async function _cwizFinish(){
  if(_cwizBusy) return;                 // guard: a create is already in flight
  _cwizSaveStep();
  const mk=(arr,vis)=>arr.filter(c=>c.expected.trim()).map(c=>({input:c.input, expected_output:c.expected, visibility:vis}));
  const test_cases=[...mk(_cwiz.samples,'sample'), ...mk(_cwiz.hidden,'hidden')];
  const need=[];
  if(!_cwiz.statement.trim()) need.push('a problem statement');
  if(!_cwiz.langs.length) need.push('a language');
  if(!_cwiz.hidden.some(c=>c.expected.trim())) need.push('a hidden test');
  const msg=document.getElementById('cwiz-msg');
  if(need.length){ msg.innerHTML=`<span style="color:var(--red)">Still need: ${_escHtml(need.join(', '))}.</span>`; return; }
  // Seed each chosen language with its default template so students never open a
  // blank editor; the teacher can refine later via Advanced editor.
  const starter_code={}; _cwiz.langs.forEach(l=>{ starter_code[l]=_STARTER_DEFAULTS[l]||''; });
  const payload={ exam_id: currentExamId, question: _cwiz.statement.trim(),
    options:{ allowed_languages:_cwiz.langs, marks:_cwiz.marks, marks_policy:_cwiz.policy,
              time_limit_ms: Math.round(_cwiz.timeSec*1000), starter_code },
    test_cases };
  // Lock the wizard while the POST is in flight so a double-click (or a click during
  // the 800ms success delay) can't create a duplicate question.
  _cwizBusy=true;
  const nextBtn=document.getElementById('cwiz-next'), backBtn=document.getElementById('cwiz-back');
  if(nextBtn) nextBtn.disabled=true;
  if(backBtn) backBtn.disabled=true;
  const _unlock=()=>{ _cwizBusy=false; if(nextBtn) nextBtn.disabled=false; if(backBtn) backBtn.disabled=false; };
  msg.innerHTML='<span class="spinner"></span> Creating…';
  try{
    const r=await authFetch(`${BASE}/api/v1/admin/coding-question`, {method:'POST', body:JSON.stringify(payload)});
    const d=await r.json();
    if(!r.ok){ msg.innerHTML=`<span style="color:var(--red)">${_escHtml(_detailText(d,'Create failed'))}</span>`; _unlock(); return; }
    msg.innerHTML=`<span style="color:var(--emerald)">Created! ${d.hidden} hidden / ${d.sample} sample.</span>`;
    setTimeout(()=>{ hideCodingWizard(); loadQuestions(); }, 800);  // stays locked; wizard closes
  }catch(e){ msg.innerHTML=`<span style="color:var(--red)">Create failed: ${_escHtml(e.message)}</span>`; _unlock(); }
}

// ── Coding AI Generation ─────────────────────────────────────────
function codingShowGenPrompt(){
  const area = document.getElementById('coding-gen-panel-area');
  if(area.innerHTML){
    area.innerHTML = '';
    return;
  }
  area.innerHTML = `<div class="coding-gen-panel">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
      <div>
        <label for="coding-gen-topic">Topic *</label>
        <input type="text" id="coding-gen-topic" placeholder="e.g. binary search, array sorting">
      </div>
      <div>
        <label for="coding-gen-difficulty">Difficulty</label>
        <select id="coding-gen-difficulty">
          <option value="easy">Easy</option>
          <option value="medium" selected>Medium</option>
          <option value="hard">Hard</option>
        </select>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
      <div>
        <label for="coding-gen-language">Language</label>
        <select id="coding-gen-language">
          <option value="javascript">JavaScript</option>
          <option value="typescript">TypeScript</option>
          <option value="python">Python</option>
          <option value="c">C</option>
          <option value="cpp">C++</option>
          <option value="java">Java</option>
        </select>
      </div>
      <div>
        <label for="coding-gen-grade">Grade Level (optional)</label>
        <input type="text" id="coding-gen-grade" placeholder="e.g. grade 10, university">
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <button class="modal-btn" id="coding-gen-btn" data-action="codingGenerateWithAI">Generate</button>
      <span id="coding-gen-status" style="font-size:12px;color:var(--text-muted)"></span>
    </div>
  </div>`;
}

function codingHideGenPrompt(){
  document.getElementById('coding-gen-panel-area').innerHTML = '';
}

async function codingGenerateWithAI(){
  const topic = document.getElementById('coding-gen-topic').value.trim();
  const difficulty = document.getElementById('coding-gen-difficulty').value;
  const language = document.getElementById('coding-gen-language').value;
  const grade = document.getElementById('coding-gen-grade').value.trim();
  const status = document.getElementById('coding-gen-status');
  const btn = document.getElementById('coding-gen-btn');
  if(!topic){ status.style.color='var(--red)'; status.textContent='Topic required.'; return; }
  btn.disabled = true;
  btn.textContent = 'Generating…';
  status.style.color = 'var(--text-muted)';
  status.textContent = 'Calling AI...';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/coding-question/generate`, {
      method:'POST',
      body: JSON.stringify({topic, difficulty, language, grade_level: grade || undefined}),
    });
    const data = await r.json();
    if(!r.ok){
      if(r.status === 503){
        status.style.color='var(--red)';
        status.textContent = 'AI generation is not configured.';
        return;
      }
      status.style.color='var(--red)';
      status.textContent = _detailText(data, 'Generation failed.');
      return;
    }
    _codingPopulateForm(data);
    document.getElementById('coding-ai-banner-area').innerHTML = `<div class="coding-ai-banner">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4m0 4h.01"/><circle cx="12" cy="12" r="10"/></svg>
      <span><strong>AI-drafted</strong> — verify expected outputs before saving. Review each test case carefully.</span>
    </div>`;
    status.style.color = 'var(--emerald)';
    status.textContent = 'Form prefilled from AI draft.';
  }catch(e){
    status.style.color='var(--red)';
    status.textContent = 'Network error.';
  }finally{
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

// ── End coding question authoring ───────────────────────────────

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
    const isNumeric = qtype==='numeric';
    const _range = qParseRange(q.correct);
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
          <option value="numeric" ${qtype==='numeric'?'selected':''}>Numeric / Integer (range)</option>
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
      `:isNumeric?`
      <div class="q-field">
        <label>Accepted answer range (inclusive)</label>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <input type="number" step="any" id="qmin-${i}" value="${escAttr(_range.min)}"
                 data-input-action="_setQRange" data-qidx='${i}'
                 placeholder="Min" style="width:120px;padding:4px 6px">
          <span style="color:var(--muted)">to</span>
          <input type="number" step="any" id="qmax-${i}" value="${escAttr(_range.max)}"
                 data-input-action="_setQRange" data-qidx='${i}'
                 placeholder="Max" style="width:120px;padding:4px 6px">
        </div>
        <span class="q-correct-status" style="color:var(--muted);font-size:11px">
          The student types a number; it's marked correct if it falls within this range.
          For an exact answer, set min = max. Decimals allowed (e.g. 9.75 to 9.85).
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
    }).catch(()=>{});
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
  }else if(val==='numeric'){
    // No options. The tolerance band lives in `correct` as "range:MIN:MAX".
    q.options = {};
    if(typeof q.correct!=='string' || !q.correct.toLowerCase().startsWith('range:')) q.correct = '';
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
        body:JSON.stringify({data_url:b64, filename:file.name||''})
      });
      if(!r.ok){
        const err = await r.json().catch(()=>({detail:'Upload failed'}));
        throw new Error(_detailText(err, ('HTTP '+r.status)));
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
// Do NOT declare `esc` here. _safe.js (loaded BEFORE this file, non-module, same
// global scope) already defines `function esc`. A `const esc` re-declaration
// throws "Identifier 'esc' has already been declared" and breaks the ENTIRE
// dashboard at parse time. Call sites use the _safe.js global esc.

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
  // The teacher-transfer modal may disable OK (no eligible target); always
  // re-enable so the shared modal isn't left stuck-disabled for the next use.
  if(els.ok) els.ok.disabled = false;
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
  if(_appDialogMode === 'reason'){
    const els = _appModalEls();
    const state = els.body && els.body._reasonState;
    if(!state){ _resolveAppDialog(null); return; }
    const code = state.getCode();
    const text = (state.getText() || '').trim();
    // If the teacher picked "Other" they MUST type why — otherwise the
    // student sees "Other (see note)" with no note, which is worse than
    // showing nothing. Inline-validate instead of resolving.
    if(code === 'other' && !text){
      const ta = document.getElementById('app-modal-reason-text');
      if(ta){
        ta.style.borderColor = '#ef4444';
        ta.placeholder = 'Required when "Other" is selected';
        ta.focus();
      }
      return;
    }
    _resolveAppDialog({reason_code: code, reason_text: text});
    return;
  }
  if(_appDialogMode === 'chip_picker'){
    // Phase 74 — Warn / End reason picker. Same shape as 'reason' but
    // returns {code, text} so the call sites can rename to their own
    // API field names.
    const els = _appModalEls();
    const state = els.body && els.body._chipState;
    if(!state){ _resolveAppDialog(null); return; }
    const code = state.getCode();
    const text = (state.getText() || '').trim();
    if(state.requireTextOnOther && code === 'other' && !text){
      const ta = document.getElementById('app-modal-chip-text');
      if(ta){
        ta.style.borderColor = '#ef4444';
        ta.placeholder = 'Required when "Other" is selected';
        ta.focus();
      }
      return;
    }
    _resolveAppDialog({code, text});
    return;
  }
  if(_appDialogMode === 'teacher_transfer'){
    const els = _appModalEls();
    const state = els.body && els.body._transferState;
    if(!state){ _resolveAppDialog(null); return; }
    const toId = state.getTarget();
    if(!toId){ _resolveAppDialog(null); return; }
    const fromId = state.fromId;
    // Close the modal first, then fire the request (which opens its own
    // result/error modal over the same overlay).
    _resolveAppDialog(null);
    _submitTeacherTransfer(fromId, toId);
    return;
  }
  _resolveAppDialog(true);
}

function cancelAppModal(){
  if(_appDialogMode === 'confirm'){ _resolveAppDialog(false); return; }
  _resolveAppDialog(null);
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

// ─── ID verification reason picker ───────────────────────────────
//
// Used by the Retake / Reject flow. After the teacher confirms the
// destructive action, this modal lets them pick a chip + optionally
// add a free-text note. Resolves to {reason_code, reason_text} or
// null on cancel.
//
// MUST stay in sync with app/models/exam.py#ID_REJECT_REASON_CODES
// and renderer/index.html#_ID_REASON_LABELS so the student sees the
// same label.
const _ID_REASON_LABELS = {
  selfie_blurry:     'Selfie too blurry',
  id_not_visible:    'ID card not clearly visible',
  lighting_dark:     'Lighting too dark',
  wrong_angle:       'Wrong angle',
  face_mismatch:     'Face does not match ID',
  id_fake_or_edited: 'ID appears fake or edited',
  wrong_person:      'Wrong person in selfie',
  other:             'Other (see note)',
};
const _ID_REASON_CHIPS = {
  retake:   ['selfie_blurry', 'id_not_visible', 'lighting_dark', 'wrong_angle'],
  rejected: ['face_mismatch', 'id_fake_or_edited', 'wrong_person', 'other'],
};
function _openIdReasonModal({decision, fullName, okText}){
  const els = _appModalEls();
  if(!els.overlay || !els.title || !els.body || !els.ok || !els.cancel){
    return Promise.resolve(null);
  }
  if(_appDialogResolve) _appDialogResolve(null);
  _appDialogMode = 'reason';
  const verb = decision === 'rejected' ? 'Reject' : 'Retake';
  els.title.textContent = `${verb} — pick a reason`;
  els.body.innerHTML = '';
  const intro = document.createElement('div');
  intro.style.cssText = 'color:var(--text-muted);font-size:13px;margin-bottom:10px';
  intro.textContent = `Why are you asking ${fullName || 'this student'} to `
    + (decision === 'rejected' ? 'be rejected? ' : 'retake? ')
    + 'The student will see whichever chip you pick and any note you add.';
  els.body.appendChild(intro);
  const chipRow = document.createElement('div');
  chipRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px';
  let selectedCode = '';
  const codes = _ID_REASON_CHIPS[decision] || [];
  codes.forEach(code => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.dataset.code = code;
    chip.textContent = _ID_REASON_LABELS[code] || code;
    chip.style.cssText = 'background:rgba(255,255,255,.04);border:1px solid var(--border);'
      + 'border-radius:999px;color:var(--text);padding:6px 12px;font-size:12px;cursor:pointer;'
      + 'transition:background .15s,border-color .15s';
    chip.onclick = () => {
      selectedCode = (selectedCode === code) ? '' : code;
      Array.from(chipRow.children).forEach(c => {
        const isSel = c.dataset.code === selectedCode;
        c.style.background = isSel ? 'rgba(91,138,240,.18)' : 'rgba(255,255,255,.04)';
        c.style.borderColor = isSel ? 'var(--blue, #5b8af0)' : 'var(--border)';
      });
    };
    chipRow.appendChild(chip);
  });
  els.body.appendChild(chipRow);
  const textLabel = document.createElement('div');
  textLabel.style.cssText = 'font-size:12px;color:var(--text-muted);margin-bottom:4px';
  textLabel.textContent = 'Add a note (optional, max 500 chars)';
  els.body.appendChild(textLabel);
  const textarea = document.createElement('textarea');
  textarea.id = 'app-modal-reason-text';
  textarea.rows = 3;
  textarea.maxLength = 500;
  textarea.style.cssText = 'width:100%;background:rgba(255,255,255,.04);border:1px solid var(--border);'
    + 'border-radius:10px;color:var(--text);padding:10px 12px;font-size:13px;outline:none;'
    + 'box-sizing:border-box;resize:vertical';
  els.body.appendChild(textarea);
  els.ok.textContent = okText || (decision === 'rejected' ? 'Reject identity' : 'Send retake');
  els.cancel.textContent = 'Cancel';
  els.cancel.style.display = '';
  els.overlay.style.display = 'flex';
  setTimeout(() => textarea.focus(), 0);
  // Stash a lookup so confirmAppModal can read the chip + text.
  els.body._reasonState = {
    getCode: () => selectedCode,
    getText: () => textarea.value,
    decision,
  };
  return new Promise(resolve => { _appDialogResolve = resolve; });
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
    if(qtype==='numeric'){
      const r=qParseRange(q.correct);
      return `<div class="preview-q">
        <div class="pq-num">Question ${i+1} of ${qData.length} · ${escAttr(qTypeLabel(qtype))}</div>
        ${q.image_url?`<img class="pq-image" id="pqimg-${i}" alt="Q${i+1}">`:''}
        <div class="pq-text">${escAttr(q.question||'(empty)')}</div>
        <div style="border:1px dashed var(--border);border-radius:8px;padding:10px 12px;margin-top:8px;color:var(--muted);font-size:12px">
          Student types a number here.
          <span style="color:var(--emerald)">Accepted: ${escAttr(r.min||'?')} to ${escAttr(r.max||'?')}</span>
        </div>
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
    }).catch(()=>{});
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
      msg.textContent = _detailText(data, `Lint failed (${r.status})`);
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
    if(qtype==='numeric'){
      const r = qParseRange(q.correct);
      const lo = parseFloat(r.min), hi = parseFloat(r.max);
      if(r.min===''||r.max===''||isNaN(lo)||isNaN(hi)){
        errors.push(`Q${i+1}: numeric question needs a min and max value`);
      }else if(lo>hi){
        errors.push(`Q${i+1}: numeric min must be ≤ max`);
      }
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
    const firstIdx=parseInt((errors[0].match(/Q(\d+)/)||[])[1]||'1', 10)-1;
    const card=document.getElementById(`qcard-${firstIdx}`);
    if(card){card.classList.add('q-error');card.scrollIntoView({behavior:'smooth',block:'center'});
      setTimeout(()=>card.classList.remove('q-error'),3000);}
    return;
  }

  // Re-number IDs sequentially
  qData.forEach((q,i)=>q.id=i+1);

  const payload={
    exam_title: document.getElementById('q-title').value.trim() || 'Exam',
    duration_minutes: parseInt(document.getElementById('q-duration').value, 10) || 60,
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
      throw new Error(_detailText(err, 'Save failed'));
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
  // Phase 74 — live teacher intervention audit-trail markers
  teacher_warning:'&#9888;&#65039;', session_paused:'&#9208;&#65039;',
  session_resumed:'&#9654;&#65039;',
  // Phase 75 — on-device audio detection
  sustained_voice:'&#127908;', keyword_uttered:'&#128172;', multiple_voices_detected:'&#128101;',
  phone_consulting:'&#128242;', collaboration:'&#128101;&#8205;&#128172;',
  answer_memo:'&#129504;', note_reading:'&#128214;',
  sustained_offtask:'&#9203;', nervous_evasion:'&#128064;&#65039;',
};

// Kept in lockstep with the backend _NON_VIOLATION_TYPES (app/services/risk.py)
// so the timeline classifies events the same way the scorecard/report/risk do —
// lifecycle, ID/calibration, proctor-software diagnostics and room-cam plumbing
// are NOT student violations and shouldn't render as such.
const TL_NON_VIOLATION_TYPES = new Set([
  'answer_selected', 'heartbeat', 'exam_started', 'exam_submitted',
  'session_ended', 'enrollment_started', 'enrollment_complete',
  'face_enrolled', 'teacher_warning', 'session_paused', 'session_resumed',
  // ID + calibration lifecycle
  'id_verification', 'id_verification_captured',
  'calibration_started', 'calibration_complete', 'calibration_timeout',
  // proctor-software diagnostics (health, not behaviour)
  'proctor_boot', 'model_load_failed', 'restart_attempt', 'event_queue_full',
  'proctor_failed', 'proctor_camera_failed', 'system_check', 'proctoring_tier',
  'client_throttled', 'submit_failed',
  // session admin + room-cam plumbing
  'session_reset', 'session_abandoned', 'session_recovered',
  'room_cam_offline', 'room_cam_pending', 'room_cam_approved', 'room_cam_rejected',
]);

function _tlIsViolation(type){
  const key = String(type || '').toLowerCase();
  return key ? !TL_NON_VIOLATION_TYPES.has(key) : false;
}

function _resetTimelineFilter(){
  tlFilter = 'all';
  document.querySelectorAll('.tl-filter-btn').forEach(b => {
    const active = b.dataset.sev === 'all';
    b.classList.toggle('active', active);
    const input = b.querySelector('input[name="tl-sev"]');
    if(input) input.checked = active;
  });
}

function _sessionRollFromId(sid){
  const raw = String(sid || '');
  if(!raw) return '—';
  const parts = raw.split('_');
  return parts[0] || raw;
}

function _sameSession(row, sid){
  const needle = String(sid || '');
  if(!needle || !row) return false;
  return [row.session_id, row.session_key, row.id, row.key]
    .some(v => String(v || '') === needle);
}

function _findTimelineFallbackRow(sid){
  const pools = [liveData, resultsData];
  for(const pool of pools){
    if(!Array.isArray(pool)) continue;
    const row = pool.find(r => _sameSession(r, sid));
    if(row) return row;
  }
  return null;
}

function _buildFallbackTimeline(sid, row, reason){
  const type = row.last_event || row.event_type || row.status || 'session_status';
  const severity = String(row.last_severity || row.severity || 'low').toLowerCase();
  const timestamp = row.last_seen || row.submitted_at || row.started_at || '';
  const rawTs = row.raw_ts || row.last_seen_raw || row.updated_at || row.created_at || '';
  const details = row.details || row.last_details ||
    'This session is visible in Live Sessions, but the full forensic timeline is unavailable for this row.';
  return {
    session_id: sid,
    roll_number: row.roll_number || row.student_roll_number || _sessionRollFromId(sid),
    full_name: row.full_name || row.name || 'Session',
    status: row.live_state || row.status || (row.submitted ? 'submitted' : 'visible'),
    started_at: row.started_at || '',
    submitted_at: row.submitted_at || '',
    score: row.score,
    total: row.total,
    risk_score: row.risk_score,
    total_events: type ? 1 : 0,
    fallback_notice: `Full timeline unavailable (${reason || 'not found'}). Showing the latest authorized row already visible on this dashboard.`,
    timeline: type ? [{
      type,
      severity,
      timestamp,
      raw_ts: rawTs,
      details,
      is_violation: _tlIsViolation(type),
      screenshot: row.screenshot || row.screenshot_url || '',
      room_screenshot: row.room_screenshot || row.room_screenshot_url || '',
    }] : [],
  };
}

function openTimeline(){
  if(!currentSessionId) return;
  // closeModal() (below, to dismiss the detail modal) RESETS currentSessionId
  // to null — so capture it first and restore it, otherwise loadTimeline()
  // would fetch /api/v1/admin/timeline/null → 404 (this broke the timeline on
  // every open).
  const sid = currentSessionId;
  closeModal();
  currentSessionId = sid;
  const m=document.getElementById('timeline-modal');
  m.classList.add('open');
  _resetTimelineFilter();
  renderTimelineSummary(null);
  document.getElementById('tl-title').textContent='Loading...';
  document.getElementById('tl-meta').innerHTML='';
  document.getElementById('tl-events').innerHTML='<div class="tl-empty"><span class="spinner"></span> Loading timeline...</div>';
  document.getElementById('tl-scrubber-track').innerHTML='';
  document.getElementById('tl-scrubber-labels').innerHTML='';
  loadTimeline(sid);
}

async function loadTimeline(sid){
  // Defense in depth: never request /timeline/null|undefined (the bug above).
  if(!sid || sid === 'null' || sid === 'undefined'){
    document.getElementById('tl-events').innerHTML =
      '<div class="tl-empty">No session selected — open a session’s Timeline from the list.</div>';
    document.getElementById('tl-title').textContent = 'Timeline';
    return;
  }
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
    _resetTimelineFilter();
    renderTimelineSummary(tlData.summary||null);
    renderTimeline();
  }catch(e){
    const fallbackRow = _findTimelineFallbackRow(sid);
    if(fallbackRow){
      tlData = _buildFallbackTimeline(sid, fallbackRow, e.message);
      renderTimelineSummary(null);
      renderTimeline();
      return;
    }
    document.getElementById('tl-title').textContent='Timeline unavailable';
    document.getElementById('tl-meta').innerHTML=`<span>Session: <strong>${_escHtml(sid)}</strong></span>`;
    document.getElementById('tl-scrubber-track').innerHTML='';
    document.getElementById('tl-scrubber-labels').innerHTML='';
    document.getElementById('tl-events').innerHTML=`<div class="tl-empty" style="color:var(--red)">Failed to load timeline: ${_escHtml(e.message)}</div>`;
  }
}

function renderTimeline(){
  if(!tlData) return;
  const d=tlData;
  const timeline = Array.isArray(d.timeline) ? d.timeline : [];
  const totalEvents = d.total_events != null ? d.total_events : timeline.length;

  // Title & meta
  document.getElementById('tl-title').textContent=`${d.full_name||'Unknown'} — ${d.roll_number}`;
  document.getElementById('tl-meta').innerHTML=`
    <span>Status: <strong>${_escHtml(d.status || '—')}</strong></span>
    <span>Started: <strong>${_escHtml(d.started_at||'—')}</strong></span>
    <span>Submitted: <strong>${_escHtml(d.submitted_at||'—')}</strong></span>
    <span>Score: <strong>${d.score!=null?_escHtml(d.score+'/'+(d.total ?? '—')):'—'}</strong></span>
    <span>Risk: <strong>${d.risk_score!=null?_escHtml(d.risk_score+'/100'):'—'}</strong></span>
    <span>Events: <strong>${_escHtml(totalEvents)}</strong></span>
  `;

  // Filter events
  const events=timeline.filter(e=>{
    if(tlFilter==='all') return true;
    if(tlFilter==='violations') return e.is_violation;
    return e.severity===tlFilter;
  });

  // Scrubber — parse timestamps to build the bar
  const allTs=timeline.map(e=>parseRawTs(e.raw_ts)).filter(t=>Number.isFinite(t) && t>0);
  const minTs=allTs.length ? Math.min(...allTs) : 0;
  const maxTs=allTs.length ? Math.max(...allTs) : 0;
  const range=maxTs-minTs||1;

  const track=document.getElementById('tl-scrubber-track');
  track.innerHTML='';
  if(allTs.length){
    events.forEach((e,i)=>{
      const ts=parseRawTs(e.raw_ts);
      if(ts<=0) return;
      const pct=((ts-minTs)/range)*100;
      const dot=document.createElement('div');
      dot.className=`tl-dot sev-${e.severity}${e.screenshot?' has-screenshot':''}`;
      dot.style.left=pct+'%';
      dot.title=`${String(e.type || '').replace(/_/g,' ')} (${e.severity})`;
      dot.onclick=(ev)=>{ev.stopPropagation();scrollToEvent(i);};
      track.appendChild(dot);
    });
  }

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
  const noticeHtml = d.fallback_notice
    ? `<div class="tl-notice">${_escHtml(d.fallback_notice)}</div>`
    : '';
  if(!events.length){
    el.innerHTML=noticeHtml + '<div class="tl-empty">No events match the current filter.</div>';
    return;
  }
  // Render events and then lazy-load thumbnails
  el.innerHTML=noticeHtml + events.map((e,i)=>{
    const icon=TL_ICONS[e.type]||'&#128204;';
    const icCls=`ic-${e.severity}`;
    const timeStr=extractTime(e.timestamp);
    // Primary webcam frame, plus the phone-cam companion captured at the same
    // flag instant when present — shown side by side (both lazy-load + open in
    // the lightbox). Falls back to the single primary thumb when no phone.
    const _tlThumb=(src,suffix)=>`<img class="tl-thumb" title="${escAttr((e.type+suffix).replace(/_/g,' '))}" data-src="${escAttr(src)}" data-action="_showLightbox" data-args='${_jsonArgsForAttr(src,e.type+suffix,timeStr)}' data-error-action="_hideSelf">`;
    const thumbHtml=e.screenshot
      ?(e.room_screenshot
        ?`<div style="display:flex;gap:6px;align-items:flex-start">${_tlThumb(e.screenshot,' — primary camera')}${_tlThumb(e.room_screenshot,' — phone camera')}</div>`
        :_tlThumb(e.screenshot,''))
      :'';
    // Pre-violation context strip (t-3s..t-0) for appeal-critical flags — the
    // lead-up to the flag, so a dropped pen reads differently from a phone.
    // Reuses .tl-thumb (lazy-load + auth + lightbox); .tl-context shrinks them.
    const _ctxArr=Array.isArray(e.context_screenshots)?e.context_screenshots:[];
    const ctxHtml=_ctxArr.length
      ?`<div class="tl-context"><div class="tl-context-label">Before flag</div>`
        +`<div class="tl-context-row">${_ctxArr.map((s,ci)=>_tlThumb(s,` — context ${ci+1} of ${_ctxArr.length}`)).join('')}</div></div>`
      :'';
    return `<div class="tl-event sev-${e.severity}${e.is_violation?' is-violation':''}" id="tl-evt-${i}">
      <div class="tl-time">${timeStr}</div>
      <div class="tl-icon ${icCls}">${icon}</div>
      <div class="tl-body">
        <div class="tl-type">${_escHtml(String(e.type || '').replace(/_/g,' '))}<span style="margin-left:8px;font-size:11px;font-weight:400;color:${e.severity==='high'?'var(--red)':e.severity==='medium'?'var(--amber)':'var(--muted)'}">${_escHtml(String(e.severity || '').toUpperCase())}</span></div>
        ${e.details?`<div class="tl-detail">${_escHtml(e.details)}</div>`:''}
      </div>
      ${thumbHtml}
      ${ctxHtml}
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
  try{
    const normalized = String(raw).replace(' ','T').replace('Z','+00:00');
    const ms = new Date(normalized).getTime();
    return Number.isFinite(ms) ? ms/1000 : 0;
  }catch(e){return 0;}
}

function extractTime(formatted){
  // formatted is like "05 Apr 2026, 02:30:22 PM IST" — extract time part
  if(!formatted) return '--:--';
  const m=formatted.match(/(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)/i);
  return m ? m[1] : '--:--';
}

function filterTimeline(){
  if(!tlData) return;
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

// Guard against prototype-pollution via a hostile session_id from the
// chat WS feed (CodeQL js/remote-property-injection at the [sid]
// accesses below). Real session IDs are UUIDs or short slug-like
// tokens; anything matching __proto__ / constructor / prototype must
// not reach a bracket-index assignment on chatSessions.
function _isSafeSid(sid){
  if(typeof sid !== 'string' || !sid) return false;
  if(sid === '__proto__' || sid === 'constructor' || sid === 'prototype') return false;
  // Permissive but bounded: alnum + dash/underscore, 1-64 chars.
  return /^[a-zA-Z0-9_-]{1,64}$/.test(sid);
}

function chatEnsureSession(sid, meta){
  if(!_isSafeSid(sid)) return null;
  if(!Object.prototype.hasOwnProperty.call(chatSessions, sid)){
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
  if(t==='ping'){
    // Server liveness probe — reply so it doesn't reap us as a dead socket.
    try{ chatWs && chatWs.readyState===1 && chatWs.send(JSON.stringify({type:'pong'})); }catch(_){}
    return;
  }
  if(t==='roster'){
    chatSessions = {};
    (data.sessions||[]).forEach(s=>{
      // chatEnsureSession() returns null when _isSafeSid() rejects a
      // hostile session_id (e.g. __proto__). Skip silently — we drop
      // the row from the roster rather than throw.
      const sess = chatEnsureSession(s.session_id, s);
      if(!sess) return;
      sess.messages = (s.history||[]).slice();
    });
    chatRenderRoster();
    if(chatActiveSid && chatSessions[chatActiveSid]) chatRenderThread();
    return;
  }
  if(t==='presence'){
    if(!_isSafeSid(data.session_id)) return;
    const sess = chatEnsureSession(data.session_id, data);
    if(!sess) return;
    sess.online = !!data.online;
    if(!data.online && !sess.messages.length){
      // Student dropped before any message — remove entirely.
      // _isSafeSid() guard above keeps prototype-poisoning keys out
      // of this delete (CodeQL js/remote-property-injection).
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
    if(!sess) return;  // _isSafeSid rejected — drop hostile session_id
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
  refreshRosterSurfaces({force:true});
});

document.addEventListener('visibilitychange', ()=>{
  if(document.visibilityState === 'visible') refreshRosterSurfaces({force:true});
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
let _qListFilter = 'all';   // 'all' | 'mcq_single' | 'mcq_multi' | 'true_false' | 'numeric' | 'short_answer' | 'img'

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
  const total = qData.length + _codingQuestions.length;
  if(countEl) countEl.textContent = total;
  if(!total){
    wrap.innerHTML = '<div class="q-list-empty">No questions yet — click "Add" in the toolbar.</div>';
    return;
  }
  const searchEl = document.getElementById('q-list-search');
  const term = searchEl ? (searchEl.value||'').trim().toLowerCase() : '';
  const filtered = qData.map((q,i) => ({...q, _idx:i, _isCoding:false})).filter(q => {
    if(_qListFilter === 'img'){ if(!q.image_url) return false; }
    else if(_qListFilter !== 'all'){
      if((q.question_type||'mcq_single') !== _qListFilter) return false;
    }
    if(term && !(q.question||'').toLowerCase().includes(term)) return false;
    return true;
  }).concat(_codingQuestions.map((q,i) => ({...q, _idx:qData.length+i, _isCoding:true})).filter(q => {
    if(_qListFilter !== 'all' && _qListFilter !== 'coding') return false;
    if(term && !(q.question||'').toLowerCase().includes(term)) return false;
    return true;
  }));
  if(!filtered.length){
    wrap.innerHTML = '<div class="q-list-empty">No matching questions.</div>';
    return;
  }
  wrap.innerHTML = filtered.map(q => {
    const preview = (q.question||'(empty)').slice(0,80);
    if(q._isCoding){
      return `<div class="q-list-item" data-action="editCodingQuestion" data-args='${_jsonArgsForAttr(String(q.id))}'>
        <span class="q-list-num">${q._idx+1}</span>
        <span class="q-list-preview">${esc(preview)}</span>
        <span class="coding-badge">code</span>
      </div>`;
    }
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
        <button class="btn btn-secondary btn-sm" data-action="showQHistory" data-args='${_jsonArgsForAttr(q.id)}' style="padding:2px 8px;font-size:10px" title="Version history">History</button>
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
    }else{ showModal(_detailText(d, 'Error')); }
  }catch(e){ showModal('Failed to add questions'); }
}

async function saveBankToExamSingle(qid){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const r = await authFetch(`${BASE}/api/v1/admin/question-bank/to-exam`,
    {method:'POST',body:JSON.stringify({question_ids:[qid],exam_id:eid})});
  if(r.ok){ loadQuestions(); }
  else{ const d=await r.json(); showModal(_detailText(d, 'Error')); }
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
      status.textContent = _detailText(data, 'Generation failed.');
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

// Generate questions from an uploaded notes file (PDF/DOCX/PPTX). Reuses the
// same preview → Add-to-Bank flow as topic-based generation; only the source
// (file vs typed topic) differs.
function qbankGenFilePick(){ document.getElementById('qbank-gen-file')?.click(); }

async function qbankGenFileChosen(){
  const f = this.files && this.files[0];
  if(!f) return;
  this.value = '';
  const count = parseInt(document.getElementById('gen-count').value || '10', 10);
  const difficulty = document.getElementById('gen-difficulty').value;
  const topic = document.getElementById('gen-topic').value.trim();
  const status = document.getElementById('gen-status');
  const preview = document.getElementById('gen-preview');
  preview.innerHTML = '';
  status.style.color = 'var(--muted)';
  status.textContent = 'Reading your file and calling AI (typically 2-5 seconds)…';
  const fd = new FormData();
  fd.append('file', f);
  const qs = `?count=${encodeURIComponent(count)}&difficulty=${encodeURIComponent(difficulty)}`
           + (topic ? `&topic=${encodeURIComponent(topic)}` : '');
  let r;
  try{
    r = await authFetch(`${BASE}/api/v1/admin/question-bank/generate-from-file${qs}`,
                        { method: 'POST', body: fd });
  }catch(_){ r = null; }
  if(!r){ status.style.color='var(--red)'; status.textContent='Network error.'; return; }
  const data = await r.json().catch(() => ({}));
  if(!r.ok){
    status.style.color='var(--red)';
    status.textContent = _detailText(data, 'Generation failed.');
    return;
  }
  _genPreview = data.questions || [];
  if(!_genPreview.length){
    status.style.color='var(--red)';
    status.textContent = 'Couldn’t generate questions from this file — try a clearer section.';
    return;
  }
  status.style.color='var(--emerald)';
  status.textContent = `Generated ${_genPreview.length}. Review below, then click "Add to Bank".`
    + (data.truncated ? ' (Used the first ~15 pages — upload a smaller section for the rest.)' : '');
  _renderGenPreview();
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
    if(!r.ok){ status.style.color='var(--red)'; status.textContent = _detailText(d, 'Save failed.'); return; }
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
          status.textContent = `Saved to bank, but adding to exam failed: ${_detailText(d2, 'server error')}`;
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

// ── Question-bank PDF/DOCX import (on-device extraction → review) ──────
let _qbankExtracted = [];
const _QBANK_BLOCKING = ['no_answer', 'low_confidence', 'few_options', 'parse_error'];

function _qbankBlocked(q){ return (q.flags || []).some(f => _QBANK_BLOCKING.includes(f)); }

function qbankPdfPick(){ document.getElementById('qbank-pdf-file')?.click(); }

async function qbankPdfChosen(){
  const f = this.files && this.files[0];
  if(!f) return;
  this.value = '';                                  // allow re-picking the same file
  const fd = new FormData();
  fd.append('file', f);
  document.getElementById('qbank-extract-overlay').style.display = 'flex';
  document.getElementById('qbank-extract-summary').textContent = '';
  document.getElementById('qbank-extract-confirm').disabled = true;
  document.getElementById('qbank-extract-body').innerHTML =
    '<p style="color:var(--text-muted)">Reading your document… on-device, nothing leaves your server.</p>';
  let res;
  try{
    res = await authFetch('/api/v1/admin/question-bank/extract', { method: 'POST', body: fd });
  }catch(_){ res = null; }
  if(!res || !res.ok){
    let msg = 'Could not read this file.';
    if(res){ try{ msg = (await res.json()).detail || msg; }catch(_){ } }
    document.getElementById('qbank-extract-body').innerHTML =
      `<p style="color:var(--red)">${_escHtml(msg)}</p>`;
    return;
  }
  const data = await res.json();
  _qbankExtracted = data.questions || [];
  _qbankRenderTable(data);
}

function _qbankRenderTable(data){
  const body = document.getElementById('qbank-extract-body');
  document.getElementById('qbank-extract-summary').textContent =
    `${data.found} found — ${data.ready} ready, ${data.found - data.ready} need attention`;
  if(!_qbankExtracted.length){
    body.innerHTML = '<p style="color:var(--text-muted)">No questions detected — check the document format.</p>';
    return;
  }
  body.innerHTML = _qbankExtracted.map((q, i) => _qbankRowHtml(q, i)).join('');
  _qbankRefreshConfirm();
}

function _qbankRowHtml(q, i){
  const blocked = _qbankBlocked(q);
  const flagTxt = (q.flags || []).join(', ');
  const img = q.image_url
    ? `<img src="${escAttr(q.image_url)}" alt="" style="max-width:160px;max-height:90px;border:1px solid var(--border);border-radius:4px;margin:4px 0">`
    : '';
  const opts = Object.entries(q.options || {}).map(([k, v]) =>
    `<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
       <b style="width:16px">${_escHtml(k)}.</b>
       <input data-input-action="_qbankEditOpt" data-i="${i}" data-opt="${escAttr(k)}"
              value="${escAttr(String(v))}"
              style="flex:1;background:var(--surface-2);border:1px solid var(--border-subtle);
                     border-radius:4px;padding:4px 6px;color:var(--text);font-size:12px"></div>`).join('');
  return `<div id="qbank-row-${i}" data-qbank-row="${i}"
       style="display:flex;gap:10px;padding:10px;border-bottom:1px solid var(--border);
              ${blocked ? 'background:rgba(245,158,11,.08)' : ''}">
      <input type="checkbox" data-change-action="_qbankPickToggle" data-i="${i}"
             ${blocked ? 'disabled' : 'checked'}
             class="qbank-pick" style="margin-top:6px;flex-shrink:0">
      <div style="flex:1;min-width:0">
        ${img}
        <textarea data-input-action="_qbankEditStem" data-i="${i}" rows="2"
                  style="width:100%;background:var(--surface-2);border:1px solid var(--border-subtle);
                         border-radius:4px;padding:6px;color:var(--text);font-size:13px;resize:vertical;
                         box-sizing:border-box">${_escHtml(q.question)}</textarea>
        <div style="margin-top:4px">${opts}</div>
        <div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-size:11px;color:var(--text-muted)">Correct:</span>
          <input data-input-action="_qbankEditCorrect" data-i="${i}" value="${escAttr(q.correct || '')}"
                 placeholder="e.g. C"
                 style="width:80px;background:var(--surface-2);border:1px solid var(--border-subtle);
                        border-radius:4px;padding:4px 6px;color:var(--emerald);font-size:12px">
          <span style="font-size:10px;color:var(--text-muted)">${_escHtml(q.type)}</span>
          <span id="qbank-flags-${i}" style="font-size:10px;color:var(--amber,#fbbf24)">${_escHtml(flagTxt)}</span>
        </div>
      </div>
    </div>`;
}

// Mirror the server's _recompute_blocking so fixed rows unlock live.
function _qbankRecheck(i){
  const q = _qbankExtracted[i];
  const flags = [];
  const opts = q.options || {};
  if(!String(q.correct || '').trim()) flags.push('no_answer');
  if(['mcq_single', 'mcq_multi'].includes(q.type) && Object.keys(opts).length < 2) flags.push('few_options');
  if(!String(q.question || '').trim() && !q.image_url) flags.push('low_confidence');
  // preserve non-blocking informational flags (has_image / math_review)
  (q.flags || []).forEach(f => { if(!_QBANK_BLOCKING.includes(f) && !flags.includes(f)) flags.push(f); });
  q.flags = flags;
  const blocked = _qbankBlocked(q);
  const row = document.getElementById(`qbank-row-${i}`);
  const pick = row ? row.querySelector('.qbank-pick') : null;
  const flagEl = document.getElementById(`qbank-flags-${i}`);
  if(row) row.style.background = blocked ? 'rgba(245,158,11,.08)' : '';
  if(flagEl) flagEl.textContent = (q.flags || []).join(', ');
  if(pick){ pick.disabled = blocked; if(blocked) pick.checked = false; }
  _qbankRefreshConfirm();
}

function _qbankEditStem(){ const i = +this.dataset.i; _qbankExtracted[i].question = this.value; _qbankRecheck(i); }
function _qbankEditCorrect(){ const i = +this.dataset.i; _qbankExtracted[i].correct = this.value.trim(); _qbankRecheck(i); }
function _qbankEditOpt(){
  const i = +this.dataset.i;
  _qbankExtracted[i].options = _qbankExtracted[i].options || {};
  _qbankExtracted[i].options[this.dataset.opt] = this.value;
}
function _qbankPickToggle(){ _qbankRefreshConfirm(); }

function _qbankRefreshConfirm(){
  const picked = [...document.querySelectorAll('.qbank-pick')].filter(c => c.checked && !c.disabled);
  document.getElementById('qbank-extract-confirm').disabled = picked.length === 0;
}

async function qbankExtractConfirm(){
  const rows = [...document.querySelectorAll('.qbank-pick')];
  const picked = rows.filter(c => c.checked && !c.disabled).map(c => _qbankExtracted[+c.dataset.i]);
  if(!picked.length) return;
  const btn = document.getElementById('qbank-extract-confirm');
  btn.disabled = true;
  let res;
  try{
    res = await authFetch('/api/v1/admin/question-bank/extract/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questions: picked }),
    });
  }catch(_){ res = null; }
  const data = res ? await res.json().catch(() => ({})) : {};
  if(!res || !res.ok){
    alert((data && data.detail) || 'Import failed.');
    btn.disabled = false;
    return;
  }
  qbankExtractClose();
  if(typeof loadBank === 'function') loadBank();
  const status = document.getElementById('bank-import-status');
  if(status) status.textContent = `Imported ${data.imported} question(s) from your document.`;
}

function qbankExtractClose(){
  const ov = document.getElementById('qbank-extract-overlay');
  if(ov) ov.style.display = 'none';
  _qbankExtracted = [];
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
    }else{ status.style.color='var(--red)'; status.textContent=_detailText(d, 'Error'); }
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
    } else { const d=await r.json(); showModal(_detailText(d, 'Error')); }
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
    else { const d=await r.json(); showModal(_detailText(d, 'Error')); }
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
    loadExamBatches();  // gap #59 — exam↔batch restriction + cohort-link selects
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
    if(!r.ok){ const d=await r.json(); st.style.color='var(--red)'; st.textContent=_detailText(d, 'Error'); return; }
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
  let reauth_token;
  try { reauth_token = await _getReauthToken('remove this member'); }
  catch(e){ alert(e.message || 'Re-authentication failed'); return; }
  if(!reauth_token) return;
  const r = await authFetch(`${BASE}/api/v1/org/members/${encodeURIComponent(memberId)}`, {
    method:'DELETE',
    headers:{'X-Reauth-Token': reauth_token}
  });
  if(!r.ok){
    const d = await r.json().catch(()=>({}));
    showModal(_detailText(d, 'Could not remove member'));
    return;
  }
  loadMembers();
}

function populateGroupSelect(){
  const assignSel = document.getElementById('assign-group-select');
  if(assignSel){
    assignSel.innerHTML = '<option value="">Select a group to restrict access...</option>' +
      _groupsData.map(g=>`<option value="${escAttr(g.id)}">${_escHtml(g.group_name)} (${g.member_count||0})</option>`).join('');
  }
  const resultsSel = document.getElementById('results-group-filter');
  if(resultsSel){
    const cur = resultsSel.value;
    resultsSel.innerHTML = '<option value="">All groups</option>' +
      _groupsData.map(g=>`<option value="${escAttr(g.id)}"${g.id===cur?' selected':''}>${_escHtml(g.group_name)}</option>`).join('');
  }
  const inviteSel = document.getElementById('invite-from-group');
  if(inviteSel){
    const cur = inviteSel.value;
    inviteSel.innerHTML = '<option value="">— or pull from a group —</option>' +
      _groupsData.map(g=>`<option value="${escAttr(g.id)}"${g.id===cur?' selected':''}>${_escHtml(g.group_name)}</option>`).join('');
  }
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

// ── EXAM ↔ BATCH (cohort) assignment + cohort enrollment link (gap #59) ──
let _examBatches = [];        // batches currently assigned to the selected exam
let _allBatchesCache = [];    // every cohort label in scope (for the selects)

async function loadExamBatches(){
  try{
    const br = await authFetch(`${BASE}/api/v1/admin/student-batches`);
    _allBatchesCache = br.ok ? ((await br.json()).batches || []) : [];
  }catch(_){ _allBatchesCache = []; }
  const eid = currentExamId;
  if(!eid){
    _examBatches = [];
  }else{
    try{
      const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/batches`);
      _examBatches = r.ok ? (await r.json()) : [];
    }catch(_){ _examBatches = []; }
  }
  renderExamBatches();
  populateBatchSelects();
}

function renderExamBatches(){
  const list = document.getElementById('exam-batches-list');
  const none = document.getElementById('exam-batches-none');
  if(!list || !none) return;
  if(!_examBatches.length){ list.innerHTML=''; none.style.display=''; return; }
  none.style.display='none';
  list.innerHTML = _examBatches.map(b=>`
    <span style="display:inline-flex;align-items:center;gap:4px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.2);border-radius:14px;padding:3px 10px;margin:3px 3px;font-size:12px;color:var(--emerald)">
      ${_escHtml(b)}
      <span data-action="unassignBatch" data-args='${_jsonArgsForAttr(b)}' style="cursor:pointer;opacity:0.6;font-size:14px">&times;</span>
    </span>`).join('');
}

function populateBatchSelects(){
  const assignSel = document.getElementById('assign-batch-select');
  if(assignSel){
    const available = _allBatchesCache.filter(b => !_examBatches.includes(b));
    assignSel.innerHTML = '<option value="">Select a batch to restrict access...</option>' +
      available.map(b=>`<option value="${escAttr(b)}">${_escHtml(b)}</option>`).join('');
  }
  const cohortSel = document.getElementById('cohort-link-batch-select');
  if(cohortSel){
    const cur = cohortSel.value;
    cohortSel.innerHTML = '<option value="">Select a batch…</option>' +
      _allBatchesCache.map(b=>`<option value="${escAttr(b)}"${b===cur?' selected':''}>${_escHtml(b)}</option>`).join('');
  }
  const resultsBatch = document.getElementById('results-batch-filter');
  if(resultsBatch){
    const cur = resultsBatch.value;
    resultsBatch.innerHTML = '<option value="">All batches</option>' +
      _allBatchesCache.map(b=>`<option value="${escAttr(b)}"${b===cur?' selected':''}>${_escHtml(b)}</option>`).join('');
  }
}

async function assignBatchToExam(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  const b = document.getElementById('assign-batch-select').value;
  if(!b) return;
  const next = Array.from(new Set([..._examBatches, b]));
  const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/batches`,
    {method:'POST', body:JSON.stringify({batches: next})});
  if(!r.ok){ const d=await r.json().catch(()=>({})); showModal('Error', _detailText(d, 'Failed to assign batch')); return; }
  loadExamBatches();
}

async function unassignBatch(b){
  const eid = currentExamId;
  if(!eid) return;
  const next = _examBatches.filter(x=>x!==b);
  await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/batches`,
    {method:'POST', body:JSON.stringify({batches: next})});
  loadExamBatches();
}

async function copyCohortLink(){
  const b = document.getElementById('cohort-link-batch-select').value;
  if(!b){ showModal('Pick a batch first.'); return; }
  if(!_shareLinkTeacherId){ showModal('Teacher link unavailable — reload and try again.'); return; }
  const url = `${location.origin}/register?t=${encodeURIComponent(_shareLinkTeacherId)}&b=${encodeURIComponent(b)}`;
  try{ await navigator.clipboard.writeText(url); }catch(_){}
  const pv = document.getElementById('cohort-link-preview');
  if(pv) pv.textContent = 'Copied: ' + url;
}

async function emailCohortLink(){
  const b = document.getElementById('cohort-link-batch-select').value;
  if(!b){ showModal('Pick a batch first.'); return; }
  if(!(await appConfirm(`Email the cohort enrolment link to every student in "${b}"? They'll get a join-link + download.`, 'Email cohort link', {okText:'Send'}))) return;
  const pv = document.getElementById('cohort-link-preview');
  if(pv) pv.textContent = 'Sending…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/batches/email-cohort-link`, {
      method:'POST', body: JSON.stringify({batch: b}),
    });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(_detailText(d,`HTTP ${r.status}`)); }
    const d = await r.json();
    let msg = `Sent to ${d.sent} student(s) in "${b}"`;
    if(d.skipped_no_email) msg += ` (${d.skipped_no_email} skipped — no email)`;
    msg += '.';
    if(pv) pv.textContent = msg;
  }catch(e){ if(pv) pv.textContent = 'Failed: ' + e.message; }
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
    group_id: currentGroupFilter || null,
    batch: currentBatchFilter || null,
  };
  const exp = document.getElementById('invite-expires').value;
  if(exp) payload.expires_at = new Date(exp).toISOString();
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/send`, {method:'POST', body:JSON.stringify(payload)});
    const d = await r.json();
    if(!r.ok){ st.style.color='var(--red)'; st.innerHTML = _escHtml(_detailText(d, 'Send failed')); return; }
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

// Refreshes the "Daily cap: N / M used today" line in the Email
// Invites card from the server. Called on tab open, after every send,
// and after a cap reset so the user always sees the truth.
async function refreshInviteCapStatus(){
  const used = document.getElementById('invite-cap-used');
  const total = document.getElementById('invite-cap-total');
  const reset = document.getElementById('btn-invite-reset-cap');
  if (!used || !total) return;
  try {
    const r = await authFetch(`${BASE}/api/v1/admin/invites/cap-status`);
    if (!r.ok) return;
    const d = await r.json();
    used.textContent = d.used;
    total.textContent = d.cap;
    // Show the reset button only when the counter is actually high
    // enough to matter — otherwise it's noise on a fresh install.
    if (reset) reset.style.display = (d.remaining < 10 || d.used > 50) ? '' : 'none';
  } catch(_) {}
}

// ── Time Extensions (Gap #22) ──────────────────────────────────────

async function setTimeExtension(){
  const status = document.getElementById('ext-status');
  const rollEl = document.getElementById('ext-roll');
  const minsEl = document.getElementById('ext-minutes');
  const roll = (rollEl?.value || '').trim().toUpperCase();
  const mins = parseInt(minsEl?.value || '0', 10);
  if(!roll){ status.textContent = 'Enter a roll number.'; return; }
  if(isNaN(mins) || mins < 0 || mins > 600){ status.textContent = 'Minutes must be 0–600.'; return; }
  const eid = typeof currentExamId !== 'undefined' ? currentExamId : null;
  if(!eid){ status.textContent = 'Select an exam first.'; return; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/time-extension`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({roll_number: roll, extra_minutes: mins}),
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      throw new Error(_detailText(d, `HTTP ${r.status}`));
    }
    status.style.color = 'var(--emerald)';
    status.textContent = mins > 0 ? `+${mins} min set for ${roll}.` : `Extension removed for ${roll}.`;
    rollEl.value = '';
    minsEl.value = '';
    reloadExtensions();
  }catch(e){
    status.style.color = 'var(--red)';
    status.textContent = e.message;
  }
}

async function reloadExtensions(){
  const list = document.getElementById('ext-list');
  if(!list) return;
  const eid = typeof currentExamId !== 'undefined' ? currentExamId : null;
  if(!eid){ list.textContent = 'Select an exam to see extensions.'; return; }
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/time-extensions`);
    if(!r.ok){ list.textContent = 'Failed to load.'; return; }
    const data = await r.json();
    const entries = Object.entries(data || {});
    if(!entries.length){
      list.textContent = 'No extensions set for this exam.';
      return;
    }
    list.innerHTML = entries.map(([roll, mins]) =>
      `<div style="display:flex;justify-content:space-between;padding:4px 0">
        <span style="font-family:var(--font-mono);font-size:13px">${_escHtml(roll)}</span>
        <span style="color:var(--accent-light);font-size:13px">+${mins} min</span>
      </div>`
    ).join('');
  }catch(_){
    list.textContent = 'Failed to load extensions.';
  }
}

// Remove student from roster — kept separate from time-extensions above.
async function removeStudentFromRoster(){
  const status = document.getElementById('roster-remove-status');
  const emailEl = document.getElementById('roster-remove-email');
  const rollEl = document.getElementById('roster-remove-roll');
  const email = (emailEl?.value || '').trim().toLowerCase();
  const roll = (rollEl?.value || '').trim().toUpperCase();
  if (!email && !roll) {
    if (status) { status.style.color='var(--red)'; status.textContent = 'Enter an email or roll number'; }
    return;
  }
  // A roster row is teacher-scoped (one enrollment per student under you),
  // not per-exam, so removal always spans all your exams.
  const ident = email ? `email "${email}"` : `roll "${roll}"`;
  if (!await appConfirm(`Remove the student matching ${ident} from your roster (all your exams)? Their LOGIN account is preserved; only the roster row is deleted.`, 'Remove from roster', {okText:'Remove'})) return;
  try {
    const params = new URLSearchParams();
    if (email) params.set('email', email);
    if (roll) params.set('roll_number', roll);
    let r = await authFetch(`${BASE}/api/v1/admin/students/roster?${params.toString()}`, {
      method: 'DELETE'
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if (status) { status.style.color='var(--red)'; status.textContent = _detailText(d, `Failed (HTTP ${r.status})`); }
      return;
    }
    let d = await r.json();
    if (d.needs_confirmation && Array.isArray(d.warnings) && d.warnings.length) {
      const lines = d.warnings.slice(0, 5).map(w => {
        const who = [w.full_name, w.roll_number, w.email].filter(Boolean).join(' / ');
        return `• ${who || 'Matched student'} has an in-progress exam${w.exam_id ? ` (${w.exam_id})` : ''}`;
      }).join('\n');
      const ok = await appConfirm(
        `This removal touches an active exam session:\n\n${lines}\n\nRemoving the roster row will not stop the running exam, but the student can lose future access to this exam/result from their lobby. Continue?`,
        'Student is mid-exam',
        {okText:'Remove anyway'}
      );
      if (!ok) {
        if (status) { status.style.color='var(--amber)'; status.textContent = 'Removal cancelled because the student has an active exam session.'; }
        return;
      }
      params.set('confirm_warnings', 'true');
      r = await authFetch(`${BASE}/api/v1/admin/students/roster?${params.toString()}`, {
        method: 'DELETE'
      });
      if (!r.ok) {
        const retryBody = await r.json().catch(() => ({}));
        if (status) { status.style.color='var(--red)'; status.textContent = _detailText(retryBody, `Failed (HTTP ${r.status})`); }
        return;
      }
      d = await r.json();
    }
    if (d.deleted === 0) {
      if (status) { status.style.color='var(--amber)'; status.textContent = `No matching roster rows found.`; }
    } else {
      const tags = (d.matched || []).map(m => `${m.roll_number || '?'} (${m.email || '—'})`).join(', ');
      if (status) { status.style.color='var(--emerald)'; status.textContent = `✅ Removed ${d.deleted} row${d.deleted>1?'s':''}: ${tags}`; }
      if (emailEl) emailEl.value = '';
      if (rollEl) rollEl.value = '';
    }
  } catch(e) {
    if (status) { status.style.color='var(--red)'; status.textContent = 'Network error'; }
  }
}

// ── Bulk student import (ported from the dropped React BulkImport wizard) ──
// Two input modes share one card: a textarea of "roll, name, email[, phone,
// batch]" lines, or a CSV file. Preview = dry_run; Register = real run.
// Both are scoped to the currently selected exam (currentExamId).

// Parse textarea lines into the {roll_number, full_name, email, phone, batch}
// row shape the JSON register-students-bulk endpoint expects.
function _parseBulkRows(text){
  const rows = [];
  for(const raw of (text||'').split('\n')){
    const line = raw.trim();
    if(!line) continue;
    const parts = line.split(',').map(s=>s.trim());
    rows.push({
      roll_number: parts[0] || '',
      full_name:   parts[1] || '',
      email:       (parts[2] || '').toLowerCase(),
      phone:       parts[3] || '',
      batch:       parts[4] || '',
    });
  }
  return rows;
}

function _renderBulkResult(d, dryRun){
  const st = document.getElementById('bulk-import-status');
  if(!st) return;
  const lines = [];
  if(dryRun){
    lines.push(`Preview: ${d.would_register||0} of ${d.total||0} row(s) ready to register.`);
  } else {
    lines.push(`Registered ${d.registered||0}, skipped ${d.skipped||0} of ${d.total||0}.`);
    if(d.invites) lines.push(`Invites — sent ${d.invites.sent||0}, skipped ${d.invites.skipped||0}, failed ${d.invites.failed||0}.`);
    if(d.invite_note) lines.push(d.invite_note);
  }
  if(Array.isArray(d.invalid) && d.invalid.length){
    lines.push(`${d.invalid.length} invalid row(s):`);
    for(const inv of d.invalid.slice(0,8)){
      lines.push(`  • ${inv.roll_number}: ${(inv.errors||[]).join(', ')}`);
    }
  }
  st.style.color = (Array.isArray(d.invalid) && d.invalid.length) ? 'var(--amber)' : 'var(--emerald)';
  st.textContent = lines.join('\n');
}

async function _bulkImport(dryRun){
  const st = document.getElementById('bulk-import-status');
  const fileEl = document.getElementById('bulk-import-file');
  const textEl = document.getElementById('bulk-import-text');
  const invitesEl = document.getElementById('bulk-import-invites');
  const sendInvites = !!(invitesEl && invitesEl.checked);
  const file = fileEl && fileEl.files && fileEl.files[0];
  try{
    let r;
    if(file){
      // CSV path → multipart import-csv. authFetch drops the JSON
      // Content-Type for FormData so the boundary is set correctly.
      const fd = new FormData();
      fd.append('file', file);
      fd.append('dry_run', dryRun ? 'true' : 'false');
      fd.append('send_invites', sendInvites ? 'true' : 'false');
      if(currentExamId) fd.append('exam_id', currentExamId);
      r = await authFetch(`${BASE}/api/v1/admin/students/import-csv`, { method:'POST', body: fd });
    } else {
      const students = _parseBulkRows(textEl ? textEl.value : '');
      if(!students.length){
        if(st){ st.style.color='var(--red)'; st.textContent = 'Paste at least one row, or choose a CSV file.'; }
        return;
      }
      const body = { exam_id: currentExamId, students, dry_run: dryRun, send_invites: sendInvites };
      r = await authFetch(`${BASE}/api/v1/admin/register-students-bulk`, { method:'POST', body: JSON.stringify(body) });
    }
    const d = await r.json().catch(()=>({}));
    if(!r.ok){
      if(st){ st.style.color='var(--red)'; st.textContent = _detailText(d, `Failed (HTTP ${r.status})`); }
      return;
    }
    _renderBulkResult(d, dryRun);
    if(!dryRun && typeof loadRegisteredCount==='function') loadRegisteredCount();
  }catch(e){
    if(st){ st.style.color='var(--red)'; st.textContent = 'Network error'; }
  }
}

function bulkImportPreview(){ return _bulkImport(true); }

async function bulkImportConfirm(){
  if(!await appConfirm('Register these students for the current exam? Newly added students will receive invite emails if that option is checked.', 'Bulk import', {okText:'Register'})) return;
  return _bulkImport(false);
}

async function downloadImportTemplate(){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/students/csv-template`);
    if(!r.ok) return;
    const text = await r.text();
    const blob = new Blob([text], {type:'text/csv'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'student_import_template.csv';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }catch(e){ /* silent */ }
}

// Reset today's invite cap for the current teacher. Surgical fix for
// dry-runs that exhausted the local counter while no real mail left
// the server. Backend rate-limits to 5/hour.
async function resetInviteCap(){
  const status = document.getElementById('invite-result');
  if (!await appConfirm('Reset today\'s invite cap to 0? This only affects YOUR daily counter — not Resend\'s actual usage.', 'Reset daily cap', {okText:'Reset'})) return;
  try {
    const r = await authFetch(`${BASE}/api/v1/admin/invites/cap-reset`, {method:'POST'});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if (status) { status.style.color='var(--red)'; status.textContent = _detailText(d, 'Reset failed'); }
      return;
    }
    if (status) { status.style.color='var(--emerald)'; status.textContent = '✅ Daily cap reset. You can send invites again.'; }
    await refreshInviteCapStatus();
  } catch(e) {
    if (status) { status.style.color='var(--red)'; status.textContent = 'Network error'; }
  }
}

async function loadInvites(){
  const eid = currentExamId;
  refreshInviteCapStatus();  // piggy-back the cap refresh on every list reload
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
  }).catch(()=>{});
}

async function resendInvite(id){
  if(!(await appConfirm('Resend this invite? A fresh token will be generated and the old link will stop working.', 'Resend invite', {okText:'Resend'}))) return;
  const st = document.getElementById('invite-result');
  st.style.color='var(--muted)'; st.textContent='Resending…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/${id}/resend`, {method:'POST'});
    if(!r.ok){ const d=await r.json(); st.style.color='var(--red)'; st.textContent=_detailText(d, 'Failed'); return; }
    st.style.color='var(--emerald)'; st.textContent='Resent.';
    setTimeout(()=>st.textContent='',2500);
    loadInvites();
  }catch(e){ st.style.color='var(--red)'; st.textContent='Network error'; }
}

async function revokeInvite(id){
  if(!(await appConfirm('Revoke this invite? The student will no longer be able to join using this link.', 'Revoke invite', {okText:'Revoke'}))) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/invites/${id}`, {method:'DELETE'});
    if(!r.ok){ const d=await r.json(); showModal(_detailText(d, 'Failed')); return; }
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
    if(!r.ok){ st.style.color='var(--red)'; st.textContent=_detailText(d, 'Failed'); return; }
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
let historyBatchFilter = '';  // gap #59 — selected cohort/batch filter
let historyDetailData = null;

function _initTabKeyboard(){
  // Existing tab keyboard navigation — already defined above
}

async function refreshStudentList(){
  loadHistoryBatches();  // keep the batch dropdown in sync with the roster
  try{
    const batchParam = historyBatchFilter ? `&batch=${encodeURIComponent(historyBatchFilter)}` : '';
    const r = await authFetch(`${BASE}/api/v1/student-search?q=${encodeURIComponent(historySearchQuery)}${batchParam}${_teacherQuery('&')}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    historyStudents = (data.students || []).sort(_historyCompare);
    renderHistoryList();
  }catch(e){
    document.getElementById('history-body').innerHTML = '<tr><td colspan="9" class="empty-state">Failed to load: '+_escHtml(e.message)+'</td></tr>';
  }
}

function filterHistorySearch(){
  historySearchQuery = document.getElementById('history-search').value.trim();
  refreshStudentList();
}

// Cohort/batch filter (gap #59).
function filterHistoryBatch(){
  const el = document.getElementById('history-batch-filter');
  historyBatchFilter = el ? el.value : '';
  refreshStudentList();
}

// Populate the batch dropdown from the distinct cohorts in scope, preserving
// the current selection. Throttled: refreshStudentList runs on every search
// keystroke, but the cohort list rarely changes and /student-batches is rate-
// limited — so refetch at most once per 15s (and retry on failure).
let _historyBatchesLoadedAt = 0;
async function loadHistoryBatches(){
  if(Date.now() - _historyBatchesLoadedAt < 15000) return;
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/student-batches${_teacherQuery('?')}`);
    if(!r.ok) return;
    const data = await r.json();
    const sel = document.getElementById('history-batch-filter');
    if(!sel) return;
    const current = historyBatchFilter || '';
    const opts = ['<option value="">All batches</option>']
      .concat((data.batches || []).map(b => `<option value="${escAttr(b)}"${b===current?' selected':''}>${_escHtml(b)}</option>`));
    sel.innerHTML = opts.join('');
    // If the previously-selected batch no longer exists, reset the filter.
    if(current && !(data.batches || []).includes(current)){ historyBatchFilter = ''; }
    _historyBatchesLoadedAt = Date.now();
  }catch(_){}
}

function renderHistoryList(){
  const body = document.getElementById('history-body');
  if(!historyStudents.length){
    body.innerHTML = '<tr><td colspan="9" class="empty-state">No students found</td></tr>';
    return;
  }
    body.innerHTML = historyStudents.map(s=>{
    const riskBadge = s.last_exam_risk != null ? _riskBadge(s.last_exam_risk) : '—';
    const guardianHtml = _guardianBadge(s);
    const actionHtml = guardianHtml.actionBtn
      ? `<button class="btn btn-secondary btn-sm" data-action="sendGuardianConsent" data-args='${_jsonArgsForAttr(s.roll_number)}'>${guardianHtml.actionBtn}</button>
         <button class="btn btn-primary btn-sm" data-action="viewStudentHistory" data-args='${_jsonArgsForAttr(s.roll_number)}'>View History</button>`
      : `<button class="btn btn-primary btn-sm" data-action="viewStudentHistory" data-args='${_jsonArgsForAttr(s.roll_number)}'>View History</button>`;
    return `<tr>
      <td style="font-family:var(--font-mono);font-size:13px">${_escHtml(s.roll_number)}</td>
      <td>${_escHtml(s.full_name)}</td>
      <td>${s.batch ? `<span class="badge">${_escHtml(s.batch)}</span>` : '<span style="color:var(--muted)">—</span>'}</td>
      <td>${s.total_exams}</td>
      <td>${s.avg_percentage != null ? s.avg_percentage+'%' : '—'}</td>
      <td>${riskBadge}</td>
      <td style="font-size:13px;color:var(--muted)">${_escHtml(s.last_exam_date || '—')}</td>
      <td style="font-size:13px">${guardianHtml.badge}</td>
      <td style="white-space:nowrap">${actionHtml}</td>
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

function _guardianBadge(s){
  const email = s.guardian_email;
  if(!email) return {badge: '<span style="color:var(--muted)">—</span>', actionBtn: null};
  const granted = s.guardian_consent_granted_at;
  if(granted){
    return {badge: '<span style="color:var(--emerald);font-weight:600">Consented ✓</span>', actionBtn: null};
  }
  const requested = s.guardian_consent_requested_at;  // may not be on the object yet
  if(requested){
    return {badge: '<span style="color:var(--amber);font-weight:600">Pending</span>', actionBtn: 'Re-send Request'};
  }
  return {badge: '<span style="color:var(--amber);font-weight:600">Pending</span>', actionBtn: 'Send Request'};
}

async function sendGuardianConsent(roll){
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/guardian/send-request`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({roll_number: roll}),
    });
    if(!r.ok){
      const err = await r.json().catch(()=>({detail:`HTTP ${r.status}`}));
      showModal('Error', _detailText(err, 'Failed to send consent request'));
      return;
    }
    refreshStudentList();
  }catch(e){
    showModal('Error', 'Failed to send consent request: '+e.message);
  }
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

// Assign / change / clear a student's cohort (gap #59). The fix path for
// students who registered via a link without a batch — and for re-assigning.
async function editStudentBatch(roll){
  const cur = (historyDetailData && historyDetailData.student && historyDetailData.student.batch) || '';
  const val = await appPrompt('Assign a batch / cohort for this student (e.g. 2024-CSE-A). Leave blank to clear.', cur, {title:'Edit batch', okText:'Save'});
  if(val === null) return;  // cancelled
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/students/${encodeURIComponent(roll)}/batch`, {
      method:'POST',
      body: JSON.stringify({batch: val.trim()})
    });
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(_detailText(d, `HTTP ${r.status}`)); }
    _historyBatchesLoadedAt = 0;        // a new cohort may now exist — force dropdown refresh
    await viewStudentHistory(roll);     // re-render the detail with the new batch
    refreshStudentList();               // keep the roster + batch dropdown in sync
  }catch(e){
    showModal('Error', 'Failed to set batch: ' + e.message);
  }
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
    <div class="stat-tile"><div class="stat-tile-label">Batch</div><div class="stat-tile-value" style="font-size:13px">${s.batch ? _escHtml(s.batch) : '<span style="color:var(--muted)">Ungrouped</span>'} <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 6px;margin-left:4px" data-action="editStudentBatch" data-args='${_jsonArgsForAttr(s.roll_number)}'>Edit</button></div></div>
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
  // Open the in-page forensics timeline. The old
  // window.open('/dashboard?session=...') opened a SECOND dashboard tab that
  // never parses ?session= (only location.hash is read) → a blank/login page.
  // Student History's "View Timeline"/"Timeline" now use the same working
  // in-page opener as Results.
  if(!sid) return;
  openTimelineForSession(sid);
}

function viewSessionTimeline(sessionId){
  if(!sessionId) return;
  openTimelineForSession(sessionId);
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
    sel.innerHTML += `<option value="${escAttr(ex.exam_id)}">${_escHtml(ex.exam_title||ex.exam_id)}</option>`;
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
let _alertQueue = [];
let _activeAlertToast = null;
let _activeAlertTimer = null;
const _ALERT_QUEUE_MAX = 50;
const _ALERT_DEDUPE_MS = 30000;
const _recentAlertKeys = new Map();

function _getAlertContainer(){
  let c = document.getElementById('alert-toast-container');
  if(!c){
    c = document.createElement('div');
    c.id = 'alert-toast-container';
    document.body.appendChild(c);
  }
  return c;
}

let _alertAudioCtx = null;
function _playAlertTone(){
  try{
    // Reuse ONE AudioContext. Creating a new one per alert never closed them;
    // browsers cap concurrent contexts (~6) and then throw, so after a handful
    // of alerts the tone silently stopped. A persistent ctx avoids the cap;
    // resume() handles the autoplay-policy suspended state.
    const AC = window.AudioContext || window.webkitAudioContext;
    if(!AC) return;
    if(!_alertAudioCtx || _alertAudioCtx.state === 'closed'){
      _alertAudioCtx = new AC();
    }
    if(_alertAudioCtx.state === 'suspended'){ _alertAudioCtx.resume().catch(()=>{}); }
    const ctx = _alertAudioCtx;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.value = 0.1;
    osc.start(); osc.stop(ctx.currentTime + 0.15);
  }catch(_){}
}

function _alertKey(a){
  return [
    a.session_id || '',
    a.violation_type || '',
    a.severity || '',
    a.roll_number || a.full_name || ''
  ].join('|');
}

function _rememberAlertKey(key){
  const now = Date.now();
  for(const [k, ts] of _recentAlertKeys){
    if(now - ts > _ALERT_DEDUPE_MS) _recentAlertKeys.delete(k);
  }
  const prev = _recentAlertKeys.get(key);
  if(prev && now - prev < _ALERT_DEDUPE_MS) return false;
  _recentAlertKeys.set(key, now);
  return true;
}

function _alertPalette(sev){
  const colors = {
    critical: ['var(--sev-critical-fg)', 'var(--sev-critical-bg)'],
    high: ['var(--sev-warn-fg)', 'var(--sev-warn-bg)'],
    medium: ['var(--accent)', 'var(--accent-subtle)'],
    low: ['var(--text-muted)', 'var(--surface-2)']
  };
  return colors[sev] || colors.medium;
}

function _alertQueueLabel(){
  const count = _alertQueue.length;
  if(count <= 0) return '';
  return count === 1 ? '1 more queued' : `${count} more queued`;
}

function _syncActiveAlertQueueLabel(){
  if(!_activeAlertToast) return;
  const meta = _activeAlertToast.querySelector('[data-alert-queue-meta]');
  if(!meta) return;
  meta.textContent = _alertQueueLabel();
  meta.style.display = _alertQueue.length ? '' : 'none';
}

function _finishActiveAlert(){
  if(_activeAlertTimer){
    clearTimeout(_activeAlertTimer);
    _activeAlertTimer = null;
  }
  if(_activeAlertToast?.parentElement) _activeAlertToast.remove();
  _activeAlertToast = null;
  setTimeout(_showNextQueuedAlert, 80);
}

function _showNextQueuedAlert(){
  if(_activeAlertToast || _alertMuted) return;
  const a = _alertQueue.shift();
  if(!a) return;

  _playAlertTone();

  const sev = (a.severity||'medium').toLowerCase();
  const [color, bg] = _alertPalette(sev);
  const container = _getAlertContainer();
  const toast = document.createElement('div');
  const studentLabel = _escHtml(a.full_name||a.roll_number||'Student');
  const violationType = _escHtml((a.violation_type||'').replace(/_/g,' '));
  const details = a.details ? String(a.details) : '';
  const detailsPreview = details.length > 120 ? details.slice(0, 120) + '…' : details;
  const safeSessionId = _escGrp(a.session_id || '');

  toast.className = 'live-alert-toast';
  toast.style.setProperty('--alert-color', color);
  toast.style.setProperty('--alert-bg', bg);
  toast.innerHTML = `
    <div class="live-alert-head">
      <span class="live-alert-icon">⚠️</span>
      <span class="live-alert-student">${studentLabel}</span>
      <span class="live-alert-sev">${_escHtml(sev)}</span>
      <button type="button" class="live-alert-dismiss" data-action="_dismissCurrentAlertToast" aria-label="Dismiss alert">×</button>
    </div>
    <div class="live-alert-type">${violationType}</div>
    ${details?'<div class="live-alert-detail">'+_escHtml(detailsPreview)+'</div>':''}
    <div class="live-alert-actions">
      <button class="btn btn-secondary btn-sm" data-action="viewSession" data-args='${_jsonArgsForAttr(safeSessionId)}'>View Timeline</button>
      <span class="live-alert-meta" data-alert-queue-meta></span>
    </div>
  `;
  container.replaceChildren(toast);
  _activeAlertToast = toast;
  _syncActiveAlertQueueLabel();
  _activeAlertTimer = setTimeout(_finishActiveAlert, 8000);
}

function handleRealtimeAlert(a){
  _alertCount++;
  const badge = document.getElementById('live-alert-badge');
  if(badge){
    badge.textContent = _alertCount;
    badge.style.display = _alertMuted ? 'none' : '';
  }
  if(_alertMuted) return;

  if(!_rememberAlertKey(_alertKey(a))) return;
  if(_alertQueue.length >= _ALERT_QUEUE_MAX) _alertQueue.shift();
  _alertQueue.push(a);
  _syncActiveAlertQueueLabel();
  _showNextQueuedAlert();
}

function toggleAlertMute(){
  _alertMuted = !_alertMuted;
  const btn = document.getElementById('alert-mute-btn');
  const badge = document.getElementById('live-alert-badge');
  if(btn) btn.textContent = _alertMuted ? '🔔 Muted' : '🔔';
  if(badge) badge.style.display = _alertMuted ? 'none' : (_alertCount > 0 ? '' : 'none');
  if(_alertMuted){
    _alertQueue = [];
    _finishActiveAlert();
  }else{
    _showNextQueuedAlert();
  }
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
function _dismissCurrentAlertToast(){ _finishActiveAlert(); }
function _closeToastParent(){
  const toast = this.closest('.live-alert-toast') || this.closest('div')?.parentElement;
  if(toast === _activeAlertToast){ _finishActiveAlert(); return; }
  toast?.remove();
}
function _focusLoginPwd(){ document.getElementById('login-pwd')?.focus(); }

// ── Question version history ───────────────────────────────────
function showQHistory(qid){
  authFetch(`${BASE}/api/v1/admin/question-bank/${qid}/versions`).then(r=>{
    if(!r.ok) throw new Error('Failed to load versions');
    return r.json();
  }).then(versions=>{
    if(!versions.length){
      showModal('No version history for this question.');
      return;
    }
    const rows = versions.map((v,i)=>{
      const ts = v.changed_at ? new Date(v.changed_at).toLocaleString() : '-';
      const typeBadge = v.change_type === 'create' ? 'color:var(--emerald)' :
                        v.change_type === 'delete' ? 'color:var(--red)' : '';
      const restoreBtn = v.change_type === 'delete' ? '' :
        `<button class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:10px"
                 onclick="restoreQVersion('${escAttr(qid)}',${v.version_number})">Restore</button>`;
      return `<div style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
        <div style="flex:1">
          <span style="font-weight:600">v${v.version_number}</span>
          <span style="${typeBadge};margin-left:6px;font-size:11px">${v.change_type}</span>
          <span style="font-size:10px;color:var(--text-muted);margin-left:8px">${ts}</span>
          ${v.changed_by ? `<span style="font-size:10px;color:var(--text-muted)">by ${_escHtml(v.changed_by)}</span>` : ''}
        </div>
        ${restoreBtn}
      </div>`;
    }).join('');
    _openAppDialog({
      title: 'Version History',
      body: `<div style="max-height:60vh;overflow-y:auto">${rows}</div>`,
      mode: 'alert',
      okText: 'Close',
    });
  }).catch(e=>{
    console.error('showQHistory:', e);
    showModal('Failed to load version history.');
  });
}

function restoreQVersion(qid, version){
  appConfirm(`Restore version ${version}? This creates a new update version.`, 'Restore Question').then(confirmed=>{
    if(!confirmed) return;
    authFetch(`${BASE}/api/v1/admin/question-bank/${qid}/versions/${version}/restore`, {method:'POST'}).then(r=>{
      if(!r.ok) throw new Error('Restore failed');
      showModal(`Restored to version ${version}.`);
      loadBank();
    }).catch(e=>{
      console.error('restoreQVersion:', e);
      showModal('Failed to restore question.');
    });
  });
}

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

// Keyboard shortcuts for the ID review queue. A focused .id-review-card
// + A/R/X triggers Approve / Retake / Reject. Skips if an input/textarea
// is focused (so typing into the reason modal doesn't fire). Skips if
// the reason picker or any other modal is already open.
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const card = e.target.closest && e.target.closest('.id-review-card');
  if (!card) return;
  // Don't hijack typing inside an embedded input/textarea.
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  // Don't fire if a modal is open over the dashboard.
  const ovEl = document.getElementById('app-modal-overlay');
  if (ovEl && ovEl.style.display && ovEl.style.display !== 'none') return;
  if (document.getElementById('id-compare-overlay')) return;
  // The focus modal owns the keyboard while open (the clicked card may
  // still hold focus underneath it — avoid a double decide).
  if (document.getElementById('id-review-modal')) return;
  const key = e.key.toLowerCase();
  const map = { a: 'approved', r: 'retake', x: 'rejected' };
  const decision = map[key];
  if (!decision) return;
  e.preventDefault();
  const vid = parseInt(card.dataset.violationId || '0', 10);
  const sk  = card.dataset.sessionKey || '';
  const fn  = card.dataset.fullName || '';
  if (!vid || !sk) return;
  decideIdReview(vid, sk, fn, decision);
});

// ── Wrappers for onchange handlers ────────────────────────────────
// Called via delegated change listener with this=el.
function _onExamSwitchWrap(){ onExamSwitch(this.value); }
function _loadBankFileWrap(){ loadBankFile(this); }
function _importInviteCsvWrap(){ importInviteCsv({target: this}); }
function _toggleGoogleCourseWrap(){ toggleGoogleCourse(this.dataset.courseId, this.checked); }
function _setQTypeWrap(){ setQType(parseInt(this.dataset.qidx, 10), this.value); }
function _handleQImageUploadWrap(){ handleQImageUpload(parseInt(this.dataset.qidx, 10), this.files[0]); }
function _bankSelectAllWrap(){ _bankSelectAll(this.checked); }
function _bankToggleWrap(){ _bankToggle(this.dataset.qid, this.checked); }

// ── Wrappers for oninput handlers (compound DOM updates) ─────────
function _setQQuestion(){ var i=parseInt(this.dataset.qidx, 10); if(isNaN(i))return; qData[i].question=this.value; markQDirty(); }
function _setQRefAnswer(){ var i=parseInt(this.dataset.qidx, 10); if(isNaN(i))return; qData[i].reference_answer=this.value; markQDirty(); }
function _setQRubric(){ var i=parseInt(this.dataset.qidx, 10); if(isNaN(i))return; qData[i].rubric=this.value; markQDirty(); }
function _setQMaxScore(){ var i=parseInt(this.dataset.qidx, 10); if(isNaN(i))return; qData[i].max_score=parseFloat(this.value)||1; markQDirty(); }
function _setQOption(){ var i=parseInt(this.dataset.qidx, 10); if(isNaN(i)||!this.dataset.okey)return; qData[i].options[this.dataset.okey]=this.value; markQDirty(); }
// Numeric-range editor: rebuild "range:MIN:MAX" from both inputs. Reads the
// sibling field straight from the DOM so a single keystroke doesn't need a
// full re-render (which would steal focus mid-typing).
function _setQRange(){
  var i=parseInt(this.dataset.qidx, 10); if(isNaN(i))return;
  var lo=(document.getElementById('qmin-'+i)?.value||'').trim();
  var hi=(document.getElementById('qmax-'+i)?.value||'').trim();
  qData[i].correct = (lo!=='' || hi!=='') ? ('range:'+lo+':'+hi) : '';
  markQDirty();
}

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
