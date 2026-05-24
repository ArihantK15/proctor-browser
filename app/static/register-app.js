let _teacherId = new URLSearchParams(location.search).get('t') || null;
let _teacherName = '';

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
      method:'POST', headers:{'Content-Type':'application/json'},
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
  if(_teacherName){
    document.querySelector('.subtitle').textContent = 'Registering with ' + _teacherName;
  }
  // Try to load schedule
  loadSchedule();
}

let _createAccount = true;
let _lastCheckedEmail = '';

function clearErr(){
  document.getElementById('reg-err').textContent='';
  document.querySelectorAll('.err-border').forEach(e=>e.classList.remove('err-border'));
}

function _setAccountMode(needsAccount){
  _createAccount = needsAccount;
  document.getElementById('pwd-divider').style.display = needsAccount ? '' : 'none';
  document.getElementById('pwd-section').style.display = needsAccount ? '' : 'none';
  document.getElementById('pwd-skipped').style.display = needsAccount ? 'none' : '';
  document.getElementById('reg-btn').textContent = needsAccount
    ? 'Register & Create Account' : 'Register for Exam';
}

async function checkExistingAccount(){
  const email = document.getElementById('inp-email').value.trim().toLowerCase();
  if(!email || !email.includes('@') || email === _lastCheckedEmail) return;
  _lastCheckedEmail = email;
  try {
    const r = await fetchWithTimeout('/api/v1/student/account-exists?email=' + encodeURIComponent(email));
    if(!r.ok) return;
    const d = await r.json();
    _setAccountMode(!d.exists);
  } catch(e) {
    // Network error — default to showing password fields
  }
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

  // Re-check account existence in case they never blurred the email field
  try {
    const chk = await fetchWithTimeout('/api/v1/student/account-exists?email=' + encodeURIComponent(email));
    if(chk.ok){
      const d = await chk.json();
      _setAccountMode(!d.exists);
    }
  } catch(e){}

  // Re-validate password after the check
  if(_createAccount){
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

  document.getElementById('reg-ldr').textContent = _createAccount
    ? 'Setting up your account...' : 'Registering...';

  let accountCreated = false;

  try{
    // Step 1: Register (enroll) with teacher
    const r1 = await fetchWithTimeout('/api/v1/register-student', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({full_name:name, roll_number:roll, email:email, phone:phone||null, teacher_id:_teacherId})
    });
    if(!r1.ok){
      const err = await r1.json().catch(()=>({detail:'Registration failed'}));
      throw new Error(err.detail || 'Registration failed');
    }
    const d1 = await r1.json();

    // Step 2: Create student account (only if password was provided)
    if(_createAccount && pwd){
      const r2 = await fetchWithTimeout('/api/v1/student/auth/signup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email:email, password:pwd, full_name:name})
      });
      if(r2.ok){
        accountCreated = true;
      } else {
        const err2 = await r2.json().catch(()=>({}));
        if(r2.status === 409){
          // Account already exists — not an error, they just didn't know
          accountCreated = true; // existing account counts
        } else {
          console.warn('Account creation failed:', err2.detail);
        }
      }

      // Step 3: Auto-login
      if(accountCreated){
        try {
          const r3 = await fetchWithTimeout('/api/v1/student/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email:email, password:pwd})
          });
          if(r3.ok){
            const d3 = await r3.json();
            localStorage.setItem('procta_student_token', d3.access_token);
            localStorage.setItem('procta_student_refresh', d3.refresh_token);
          }
        } catch(loginErr) {
          console.warn('Auto-login failed:', loginErr);
        }
      }
    }

    // Show success — adapt stepper based on whether account was created
    document.getElementById('reg-form').style.display = 'none';
    document.getElementById('reg-success').style.display = 'block';
    document.getElementById('success-roll').textContent = d1.roll_number;
    document.getElementById('success-name').textContent = 'Registered as ' + d1.full_name;

    // If they skipped account creation, update the stepper to reflect that
    if(!_createAccount || !accountCreated){
      const acctStep = document.getElementById('step-account');
      if(acctStep){
        acctStep.className = 'step current';
        acctStep.querySelector('.step-icon').innerHTML = '2';
        acctStep.querySelector('.step-title').textContent = 'Sign in with your existing account';
        acctStep.querySelector('.step-desc').textContent = 'Open the Procta app and sign in with your email and password.';
      }
    }
  }catch(e){
    document.getElementById('reg-err').textContent = e.message;
    btn.disabled = false;
  }
  document.getElementById('reg-ldr').textContent = '';
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
(function(){
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
})();

function _parseDataArgs(raw) {
  try { return JSON.parse(raw || '[]'); } catch (_) { return []; }
}
function _uppercaseTrimInput(){ this.value = this.value.toUpperCase().trim(); }
function _uppercaseTrimAndClearErr(){
  this.value = this.value.toUpperCase().trim();
  clearErr();
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || !el.dataset.action) return;
  if (e.target.closest('a') === el) e.preventDefault();
  const fn = window[el.dataset.action];
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.args));
});

document.addEventListener('input', (e) => {
  const el = e.target.closest('[data-input-action]');
  if (!el || !el.dataset.inputAction) return;
  const fn = window[el.dataset.inputAction];
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.inputArgs));
});

document.addEventListener('keydown', (e) => {
  const el = e.target.closest('[data-keydown-action]');
  if (!el || !el.dataset.keydownAction) return;
  const wantKey = el.dataset.keydownKey || '';
  if (wantKey && e.key !== wantKey) return;
  const fn = window[el.dataset.keydownAction];
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.keydownArgs));
});

document.addEventListener('focusout', (e) => {
  const el = e.target.closest('[data-blur-action]');
  if (!el || !el.dataset.blurAction) return;
  const fn = window[el.dataset.blurAction];
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.blurArgs));
});
