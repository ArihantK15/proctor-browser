let _teacherId = new URLSearchParams(location.search).get('t') || null;
// Optional exam scoping. When the teacher's share-link includes
// &e=<exam_id>, we forward it on registration so the resulting
// students row gets exam_id set — student lobby then surfaces THAT
// specific exam instead of "the teacher's first exam_config."
let _examId = new URLSearchParams(location.search).get('e') || null;
let _teacherName = '';

let _turnstileToken = null;
let _turnstileSiteKey = '';
let _turnstileWidgetId = null;

async function _loadPublicConfig() {
  try {
    const r = await fetchWithTimeout('/api/v1/public-config');
    if (r.ok) {
      const cfg = await r.json();
      _turnstileSiteKey = cfg.turnstile_site_key || '';
    }
  } catch(e) {}
}

function _initTurnstile() {
  if (!_turnstileSiteKey) return;
  const el = document.getElementById('cf-turnstile-register');
  if (!el || el.dataset.rendered) return;
  // Don't render in a hidden container — wait until showRegistrationForm
  const form = document.getElementById('reg-form');
  if (form && (form.style.display === 'none' || getComputedStyle(form).display === 'none')) return;
  const doRender = () => {
    if (!window.turnstile || el.dataset.rendered) return;
    el.dataset.rendered = '1';
    _turnstileWidgetId = window.turnstile.render(el, {
      sitekey: _turnstileSiteKey,
      theme: 'dark',
      callback: (token) => { _turnstileToken = token; },
      'expired-callback': () => { _turnstileToken = null; },
      'error-callback': () => { _turnstileToken = null; },
    });
  };
  if (window.turnstile) { doRender(); return; }
  const check = setInterval(() => {
    if (window.turnstile) { clearInterval(check); doRender(); }
  }, 100);
  setTimeout(() => clearInterval(check), 10000);
}

function _resetTurnstile() {
  _turnstileToken = null;
  if (!_turnstileSiteKey || !window.turnstile || !_turnstileWidgetId) return;
  try { window.turnstile.reset(_turnstileWidgetId); } catch(e) {}
}

// If no teacher_id in URL, show the lookup fallback
if(!_teacherId){
  document.getElementById('teacher-lookup').style.display = '';
  document.getElementById('reg-form').style.display = 'none';
}

function clearLookupErr(){
  document.getElementById('lookup-err').textContent='';
}

function fetchWithTimeout(url, opts={}, timeoutMs=30000){
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
  return fetch(url, {...opts, signal: opts.signal || ctrl.signal}).finally(()=>clearTimeout(timer));
}

async function lookupTeacherByEmail(){
  clearLookupErr();
  const email = document.getElementById('lookup-email').value.trim();
  if(!email||!email.includes('@')){ document.getElementById('lookup-err').textContent='Enter a valid email'; return; }
  const btn = document.getElementById('lookup-btn');
  btn.disabled = true; btn.textContent = 'Searching...';
  try{
    const r = await fetchWithTimeout('/api/v1/lookup-teacher?email=' + encodeURIComponent(email));
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail||'Teacher not found');
    _teacherId = d.teacher_id;
    _teacherName = d.full_name;
    showRegistrationForm();
  }catch(e){ document.getElementById('lookup-err').textContent = e.message; }
  finally{ btn.disabled = false; btn.textContent = 'Find Teacher'; }
}

async function lookupByCode(){
  clearLookupErr();
  const code = document.getElementById('lookup-code').value.trim().toUpperCase();
  if(!code){ document.getElementById('lookup-err').textContent='Enter an access code'; return; }
  const btn = document.getElementById('code-btn');
  btn.disabled = true; btn.textContent = 'Resolving...';
  try{
    const r = await fetchWithTimeout('/api/v1/resolve-access-code', {
      method:'POST', credentials:'omit', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({access_code: code})
    });
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail||'Invalid access code');
    _teacherId = d.teacher_id;
    _teacherName = d.teacher_name || d.exam_title || '';
    showRegistrationForm();
    // Show exam schedule if available
    if(d.starts_at || d.ends_at){
      const banner = document.getElementById('schedule-banner');
      banner.style.display = '';
      document.getElementById('sched-title').textContent = d.exam_title || 'Exam';
      // Browser-local timezone (audit M3 — was hardcoded IST).
      const _tz = (()=>{ try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; } catch(_){ return 'Asia/Kolkata'; } })();
      const _loc = navigator.language || 'en-IN';
      const _opts = {timeZone:_tz, timeZoneName:'short'};
      let times = '';
      if(d.starts_at) times += 'Starts: ' + new Date(d.starts_at).toLocaleString(_loc, _opts) + '\n';
      if(d.ends_at) times += 'Ends: ' + new Date(d.ends_at).toLocaleString(_loc, _opts);
      document.getElementById('sched-times').textContent = times;
    }
  }catch(e){ document.getElementById('lookup-err').textContent = e.message; }
  finally{ btn.disabled = false; btn.textContent = 'Resolve Code'; }
}

function showRegistrationForm(){
  document.getElementById('teacher-lookup').style.display = 'none';
  document.getElementById('reg-form').style.display = '';
  _initTurnstile();  // render captcha now that the form is visible
  if(_teacherName){
    document.querySelector('.subtitle').textContent = 'Registering with ' + _teacherName;
  }
  // Try to load schedule
  loadSchedule();
}

function clearErr(){
  document.getElementById('reg-err').textContent='';
  document.querySelectorAll('.err-border').forEach(e=>e.classList.remove('err-border'));
}

// Returning students re-registering for a new exam already have a Procta
// login — they don't need to invent a throwaway password. This toggle hides
// the password fields so they just enrol (the roster row auto-links to their
// existing account by email) and then sign in with their real password.
let _haveAccount = false;
function toggleExistingAccount(){
  _haveAccount = !_haveAccount;
  const sec  = document.getElementById('pwd-section');
  const div  = document.getElementById('pwd-divider');
  const link = document.getElementById('have-acct-link');
  const btn  = document.getElementById('reg-btn');
  if(_haveAccount){
    sec.style.display = 'none';
    div.style.display = 'none';
    document.getElementById('inp-pwd').value = '';
    document.getElementById('inp-pwd2').value = '';
    link.innerHTML = 'Need to create a new account? Set a password &rarr;';
    btn.textContent = 'Register for this exam';
  } else {
    sec.style.display = '';
    div.style.display = '';
    link.innerHTML = 'Already have a Procta account? Skip the password &rarr;';
    btn.textContent = 'Register & Create Account';
  }
  clearErr();
}

async function doRegister(){
  clearErr();
  const name  = document.getElementById('inp-name').value.trim();
  const roll  = document.getElementById('inp-roll').value.trim().toUpperCase();
  const email = document.getElementById('inp-email').value.trim();
  const phone = document.getElementById('inp-phone').value.trim();
  const pwd   = document.getElementById('inp-pwd').value;
  const pwd2  = document.getElementById('inp-pwd2').value;

  if(!name){
    document.getElementById('inp-name').classList.add('err-border');
    document.getElementById('reg-err').textContent='Full name is required';
    return;
  }
  if(!roll){
    document.getElementById('inp-roll').classList.add('err-border');
    document.getElementById('reg-err').textContent='Roll number is required';
    return;
  }
  if(!email || !email.includes('@')){
    document.getElementById('inp-email').classList.add('err-border');
    document.getElementById('reg-err').textContent='A valid email is required';
    return;
  }
  const btn = document.getElementById('reg-btn');
  btn.disabled = true;
  document.getElementById('reg-ldr').textContent = 'Checking...';

  // Password is only required when creating a NEW account. A returning
  // student who flipped the "I already have an account" toggle skips this
  // entirely — see toggleExistingAccount(). For new accounts we do NOT
  // pre-probe whether the email already exists (that endpoint is an
  // intentional anti-enumeration stub); existence is instead detected from
  // the signup 409 below.
  if(!_haveAccount){
    if(!isStrongPassword(pwd)){
      document.getElementById('inp-pwd').classList.add('err-border');
      document.getElementById('reg-err').textContent='Password must be at least 10 characters and include uppercase, lowercase, a number, and a symbol';
      btn.disabled = false;
      document.getElementById('reg-ldr').textContent = '';
      return;
    }
    if(pwd !== pwd2){
      document.getElementById('inp-pwd2').classList.add('err-border');
      document.getElementById('reg-err').textContent='Passwords do not match';
      btn.disabled = false;
      document.getElementById('reg-ldr').textContent = '';
      return;
    }
  }

  document.getElementById('reg-ldr').textContent = 'Setting up your account...';

  let existingAccount = false;

  try{
    // Step 1: Register (enroll) with teacher
    const r1 = await fetchWithTimeout('/api/v1/register-student', {
      method: 'POST',
      // Public endpoint keyed by teacher_id+email in the body — it does not
      // use the session. Omit credentials so a returning student's stale
      // login cookie isn't sent, which would otherwise trip CSRFMiddleware
      // (cookie present + no X-CSRF-Token header) and 403 the registration.
      credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({full_name:name, roll_number:roll, email:email, phone:phone||null, teacher_id:_teacherId, exam_id:_examId||null})
    });
    if(!r1.ok){
      const err = await r1.json().catch(()=>({detail:'Registration failed'}));
      throw new Error(err.detail || 'Registration failed');
    }
    const d1 = await r1.json();

    // Returning student (toggle on): they already have a login, so skip
    // signup entirely. The enrolment above auto-links to their existing
    // account by email; they just sign in with their real password.
    if(_haveAccount){
      _showSuccessCard(d1, /*existingAccount=*/true);
      return;
    }

    // Step 2: Create the student account. A 409 is the automatic "this email
    // already has an account" signal for a returning student — not an error.
    const signupBody = {email:email, password:pwd, full_name:name};
    if(_turnstileToken) signupBody.captcha_token = _turnstileToken;
    const r2 = await fetchWithTimeout('/api/v1/student/auth/signup', {
      method: 'POST',
      // Public signup keyed by the body email; omit credentials so a stale
      // login cookie doesn't trip CSRFMiddleware (see register-student above).
      credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(signupBody)
    });
    if(r2.status === 409){
      existingAccount = true;            // returning student — already registered
    } else if(!r2.ok){
      const err2 = await r2.json().catch(()=>({}));
      const msg = typeof err2.detail === 'object' ? (err2.detail.message || err2.detail.error) : err2.detail;
      throw new Error(msg || 'Account creation failed');
    }

    // Returning student (409): account already exists, so it's already
    // verified — skip the OTP step and jump straight to the success
    // card with the "sign in to your existing account" copy.
    if(existingAccount){
      _showSuccessCard(d1, /*existingAccount=*/true);
      return;
    }

    // Brand-new account: backend just emailed a 6-digit OTP. Show the
    // OTP card so the student verifies WHILE the code is still fresh
    // (10-minute TTL). Without this, they'd close the tab, download the
    // app, install, open, attempt sign-in, get a 403 EMAIL_VERIFICATION
    // _REQUIRED, and only THEN see an OTP prompt — by which point the
    // code may have already expired and they'd have to request a resend.
    _pendingSignupEmail = email;
    _pendingRegInfo = d1;
    document.getElementById('reg-form').style.display = 'none';
    document.getElementById('otp-card').style.display = 'block';
    document.getElementById('otp-email-display').textContent = email;
    document.getElementById('otp-err').textContent = '';
    document.getElementById('otp-resend-status').textContent = '';
    const otpInput = document.getElementById('otp-code-input');
    if(otpInput){ otpInput.value = ''; setTimeout(()=>otpInput.focus(), 50); }
  }catch(e){
    document.getElementById('reg-err').textContent = e.message;
    btn.disabled = false;
  }
  document.getElementById('reg-ldr').textContent = '';
}

// ── OTP verification (post-signup, inline on the register page) ──────
let _pendingSignupEmail = '';
let _pendingRegInfo = null;

function _otpDigitsOnly(){
  this.value = (this.value || '').replace(/\D/g, '').slice(0, 6);
  const err = document.getElementById('otp-err');
  if(err) err.textContent = '';
}

function _showSuccessCard(regInfo, existingAccount){
  document.getElementById('reg-form').style.display = 'none';
  document.getElementById('otp-card').style.display = 'none';
  document.getElementById('reg-success').style.display = 'block';
  if(regInfo){
    document.getElementById('success-roll').textContent = regInfo.roll_number || '';
    document.getElementById('success-name').textContent = 'Registered as ' + (regInfo.full_name || '');
  }
  // Returning students don't go through the OTP step (they're already
  // verified), so the "Email verified" step should be reframed as
  // "Use your existing password" to match their actual experience.
  if(existingAccount){
    const acctStep = document.getElementById('step-account');
    if(acctStep){
      acctStep.querySelector('.step-title').textContent = 'Existing account detected';
      acctStep.querySelector('.step-desc').textContent = 'You already have a Procta account for this email.';
    }
    const verifyStep = document.getElementById('step-verified');
    if(verifyStep){
      verifyStep.querySelector('.step-title').textContent = 'Sign in with your existing password';
      verifyStep.querySelector('.step-desc').textContent = 'No new verification needed — your account is already active.';
    }
  }
}

async function doVerifyOtp(){
  const code = (document.getElementById('otp-code-input').value || '').replace(/\D/g, '');
  const errEl = document.getElementById('otp-err');
  const ldr = document.getElementById('otp-ldr');
  const btn = document.getElementById('otp-verify-btn');
  errEl.textContent = '';
  if(code.length !== 6){
    errEl.textContent = 'Enter the 6-digit code from your email.';
    return;
  }
  btn.disabled = true; ldr.textContent = 'Verifying...';
  try{
    const r = await fetchWithTimeout('/api/v1/student/auth/verify-signup-otp', {
      method: 'POST',
      credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: _pendingSignupEmail, code}),
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      const msg = (typeof d.detail === 'object' ? d.detail.message : d.detail) || 'Invalid or expired code.';
      throw new Error(msg);
    }
    _showSuccessCard(_pendingRegInfo, /*existingAccount=*/false);
  }catch(e){
    errEl.textContent = e.message || 'Could not verify the code.';
    btn.disabled = false;
  }finally{
    ldr.textContent = '';
  }
}

async function doResendOtp(){
  const status = document.getElementById('otp-resend-status');
  const errEl = document.getElementById('otp-err');
  status.textContent = '';
  errEl.textContent = '';
  if(!_pendingSignupEmail){
    errEl.textContent = 'Cannot resend — please restart registration.';
    return;
  }
  try{
    const r = await fetchWithTimeout('/api/v1/student/auth/resend-signup-otp', {
      method: 'POST',
      credentials: 'omit',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: _pendingSignupEmail}),
    });
    if(!r.ok){
      const d = await r.json().catch(()=>({}));
      const msg = (typeof d.detail === 'object' ? d.detail.message : d.detail) || 'Could not resend the code.';
      throw new Error(msg);
    }
    status.textContent = 'New code sent — check your inbox.';
    setTimeout(()=>{ status.textContent = ''; }, 6000);
  }catch(e){
    errEl.textContent = e.message || 'Could not resend the code.';
  }
}

function isStrongPassword(password) {
  return !!password
    && password.length >= 10
    && /[a-z]/.test(password)
    && /[A-Z]/.test(password)
    && /\d/.test(password)
    && /[^A-Za-z0-9]/.test(password);
}

// Load exam schedule banner
function loadSchedule(){
  fetchWithTimeout('/api/v1/exam-schedule' + (_teacherId ? '?t=' + encodeURIComponent(_teacherId) : '')).then(r=>r.json()).then(d=>{
    if(!d.starts_at && !d.ends_at) return;
    const banner = document.getElementById('schedule-banner');
    banner.style.display = 'block';
    document.getElementById('sched-title').textContent = d.exam_title || 'Exam';
    // Browser-local timezone (audit M3).
    const _tz = (()=>{ try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; } catch(_){ return 'Asia/Kolkata'; } })();
    const _loc = navigator.language || 'en-IN';
    const opts = {timeZone:_tz, year:'numeric', month:'short', day:'numeric',
                  hour:'2-digit', minute:'2-digit', hour12:true, timeZoneName:'short'};
    let times = '';
    if(d.starts_at) times += 'Starts: ' + new Date(d.starts_at).toLocaleString(_loc, opts);
    if(d.ends_at) times += (times ? '  |  ' : '') + 'Ends: ' + new Date(d.ends_at).toLocaleString(_loc, opts);
    document.getElementById('sched-times').textContent = times;
    if(d.duration_minutes) {
      document.getElementById('sched-times').textContent += '  |  Duration: ' + d.duration_minutes + ' min';
    }
  }).catch(()=>{});
}
loadSchedule();

function _parseDataArgs(raw) {
  try { return JSON.parse(raw || '[]'); } catch (err) { console.warn('[delegated] invalid data-args', err); return []; }
}
function _uppercaseTrimInput(){ this.value = this.value.toUpperCase().trim(); }
function _uppercaseTrimAndClearErr(){
  this.value = this.value.toUpperCase().trim();
  clearErr();
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
  if (e.target.closest('a') === el) e.preventDefault();
  const fn = _resolveDelegatedAction(el.dataset.action);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.args));
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

document.addEventListener('focusout', (e) => {
  const el = e.target.closest('[data-blur-action]');
  if (!el || !el.dataset.blurAction) return;
  const fn = _resolveDelegatedAction(el.dataset.blurAction);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.blurArgs));
});

_loadPublicConfig().then(_initTurnstile);
