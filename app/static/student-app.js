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
let _inviteMalformedTimer = null;
let _pendingSignupEmail = '';

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
  const otp = document.getElementById('signup-otp-view');
  if (otp) otp.style.display = 'none';
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
  document.getElementById('auth-subheading').textContent = 'We\'ll email you a 6-digit reset code';
  _initTurnstile();
}

function cancelReset() {
  document.getElementById('reset-view').style.display = 'none';
  document.getElementById('reset-err').textContent = '';
  document.getElementById('reset-ok').style.display = 'none';
  document.getElementById('reset-email').disabled = false;
  document.getElementById('reset-btn').style.display = '';
  document.getElementById('reset-btn').textContent = 'Send reset code';
  document.getElementById('reset-confirm-wrap').style.display = 'none';
  document.getElementById('reset-code').value = '';
  document.getElementById('reset-new-password').value = '';
  // Restore login form
  document.getElementById('inp-email').closest('.fg').style.display = '';
  document.getElementById('inp-password').closest('.fg').style.display = '';
  document.getElementById('auth-btn').style.display = '';
  document.querySelector('.auth-tabs').style.display = '';
  document.getElementById('forgot-link').style.display = '';
  setAuthMode('login');
}

function _showSignupOtp(email) {
  _pendingSignupEmail = email || _pendingSignupEmail;
  document.getElementById('fg-name').style.display = 'none';
  document.getElementById('inp-email').closest('.fg').style.display = 'none';
  document.getElementById('inp-password').closest('.fg').style.display = 'none';
  document.getElementById('auth-btn').style.display = 'none';
  document.querySelector('.auth-tabs').style.display = 'none';
  document.getElementById('forgot-link').style.display = 'none';
  document.getElementById('reset-view').style.display = 'none';
  document.getElementById('signup-otp-view').style.display = 'block';
  document.getElementById('signup-otp-err').textContent = '';
  document.getElementById('signup-otp-ok').style.display = 'none';
  document.getElementById('auth-heading').textContent = 'Check your email';
  document.getElementById('auth-subheading').textContent = `Enter the 6-digit code sent to ${_pendingSignupEmail}`;
}

function cancelSignupOtp() {
  document.getElementById('signup-otp-view').style.display = 'none';
  document.getElementById('signup-otp-code').value = '';
  document.getElementById('inp-email').closest('.fg').style.display = '';
  document.getElementById('inp-password').closest('.fg').style.display = '';
  document.querySelector('.auth-tabs').style.display = '';
  document.getElementById('forgot-link').style.display = '';
  document.getElementById('auth-btn').style.display = '';
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
    const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/reset-request'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail || 'Failed to send reset code');
    }
    document.getElementById('reset-ok').style.display = 'block';
    document.getElementById('reset-btn').style.display = 'none';
    document.getElementById('reset-email').disabled = true;
    document.getElementById('reset-confirm-wrap').style.display = 'block';
  } catch(e) {
    errEl.textContent = e.message || 'Something went wrong, try again.';
    _resetTurnstile();
  } finally {
    btn.disabled = false; btn.textContent = 'Send reset code';
  }
}

async function confirmResetOtp() {
  const email = document.getElementById('reset-email').value.trim().toLowerCase();
  const code = (document.getElementById('reset-code').value || '').trim();
  const newPassword = document.getElementById('reset-new-password').value || '';
  const errEl = document.getElementById('reset-err');
  const okEl = document.getElementById('reset-ok');
  errEl.textContent = '';
  if (!/^\d{6}$/.test(code)) { errEl.textContent = 'Enter the 6-digit code'; return; }
  if (!isStrongPassword(newPassword)) {
    errEl.textContent = 'Password must be at least 10 characters and include uppercase, lowercase, a number, and a symbol.';
    return;
  }
  const btn = document.getElementById('reset-confirm-btn');
  btn.disabled = true; btn.textContent = 'Updating...';
  try {
    const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/reset-confirm'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, code, new_password: newPassword}),
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail || 'Could not update password');
    }
    document.getElementById('reset-code').value = '';
    document.getElementById('reset-new-password').value = '';
    document.getElementById('reset-confirm-wrap').style.display = 'none';
    okEl.textContent = 'Password updated. You can log in now.';
    okEl.style.display = 'block';
    document.getElementById('reset-email').disabled = false;
  } catch(e) {
    errEl.textContent = e.message || 'Something went wrong, try again.';
  } finally {
    btn.disabled = false; btn.textContent = 'Update password';
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
      if (!r.ok) {
        _resetTurnstile();
        // A returning student re-registering for a new exam doesn't need a
        // new account — exams auto-link to their existing one after login.
        // The backend signals "already exists" with 409; instead of a
        // dead-end error, drop them straight into the sign-in flow.
        if (r.status === 409) {
          setAuthMode('login');
          document.getElementById('inp-email').value = email;
          const pw = document.getElementById('inp-password');
          pw.value = '';
          pw.focus();
          document.getElementById('forgot-link').style.display = '';
          document.getElementById('auth-err').textContent =
            'You already have a Procta account — just enter your password to sign in. New exams appear automatically once you log in. Forgot it? Use "Forgot password" below.';
          return;
        }
        throw new Error((await r.json().catch(()=>({}))).detail || 'Signup failed');
      }
      const sd = await r.json().catch(()=>({}));
      if (sd.verify_required) {
        _showSignupOtp(email);
        return;
      }
      // fall through to login for legacy servers only
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
      const body = await r.json().catch(() => ({}));
      let msg = (typeof body.detail === 'object' && body.detail)
        ? (body.detail.message || 'Login failed')
        : (body.detail || 'Login failed');
      if (body.detail && body.detail.code === 'EMAIL_VERIFICATION_REQUIRED') {
        _pendingSignupEmail = email;
        _showSignupOtp(email);
        throw new Error(msg);
      }
      // After a 401, the backend can't distinguish "no student account
      // for this email" from "password is wrong" — both return the
      // same generic message to prevent account-existence enumeration.
      // A common cause of the user-reported "credentials are correct
      // but it says invalid" is logging into the STUDENT app with a
      // TEACHER dashboard account (separate tables, same person).
      // Surface that as actionable next-step text in the form, while
      // keeping the server's vague message intact.
      if (r.status === 401 && authMode === 'login') {
        msg += '. Check your password using the "Show" button, or click "Sign up" if you don\'t have a STUDENT account yet (teacher dashboard accounts are separate).';
      }
      throw new Error(msg);
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

// Wired by data-action="togglePasswordVisible" on the eye button next
// to the password input. Toggles input type between password and
// text so a stuck user can verify what they're typing. Helps the
// "I'm sure my password is right" case where the actual issue is a
// silent typo (caps lock on, swapped chars on a non-standard keyboard).
function togglePasswordVisible() {
  const inp = document.getElementById('inp-password');
  const btn = document.getElementById('inp-password-toggle');
  if (!inp || !btn) return;
  const showing = inp.type === 'text';
  inp.type = showing ? 'password' : 'text';
  btn.textContent = showing ? 'Show' : 'Hide';
  btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
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
  clearStudentSession();
}

// ─────── TRACK A: signup verify + account delete ───────
function _setTrackAStatus(id, msg, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = msg ? 'block' : 'none';
  el.style.color = ok ? 'var(--emerald)' : 'var(--red)';
  el.textContent = msg || '';
}

async function verifySignupOtp() {
  const btn = document.getElementById('signup-otp-verify-btn');
  const err = document.getElementById('signup-otp-err');
  const email = _pendingSignupEmail || document.getElementById('inp-email').value.trim().toLowerCase();
  const code = (document.getElementById('signup-otp-code')?.value || '').trim();
  err.textContent = '';
  if (!/^\d{6}$/.test(code)) { err.textContent = 'Enter the 6-digit code.'; return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Verifying...'; }
  try {
    const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/verify-signup-otp'), {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, code}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Invalid or expired code');
    document.getElementById('signup-otp-ok').style.display = 'block';
    setTimeout(cancelSignupOtp, 700);
  } catch (e) {
    err.textContent = e.message || 'Could not verify code';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Verify email'; }
  }
}

async function resendSignupOtp() {
  const btn = document.getElementById('signup-otp-resend-btn');
  const err = document.getElementById('signup-otp-err');
  const email = _pendingSignupEmail || document.getElementById('inp-email').value.trim().toLowerCase();
  err.textContent = '';
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    const r = await fetchWithTimeout(apiUrl('/api/v1/student/auth/resend-signup-otp'), {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not resend code');
    err.style.color = 'var(--emerald)';
    err.textContent = 'Code sent again.';
  } catch (e) {
    err.style.color = 'var(--red)';
    err.textContent = e.message || 'Could not resend code';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Resend code'; }
  }
}

async function deleteMyAccount() {
  const btn = document.getElementById('account-delete-request-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending code...'; }
  try {
    const r = await authed('/api/v1/student/account/delete-request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not send deletion code');
    document.getElementById('account-delete-code-wrap').style.display = '';
    document.getElementById('account-delete-confirm-wrap').style.display = '';
    _setTrackAStatus('account-delete-status', 'Deletion code sent to your email. Enter it below only if you are sure.', true);
  } catch (e) {
    _setTrackAStatus('account-delete-status', e.message || 'Could not send deletion code', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Permanently delete my account'; }
  }
}

async function confirmDeleteMyAccount() {
  const btn = document.getElementById('account-delete-confirm-btn');
  const code = (document.getElementById('account-delete-code')?.value || '').trim();
  if (!/^\d{6}$/.test(code)) { _setTrackAStatus('account-delete-status', 'Enter the 6-digit deletion code.', false); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Deleting...'; }
  try {
    const r = await authed('/api/v1/student/account/delete-confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({otp_code: code}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not delete account');
    clearStudentSession();
    showModal('Account deleted', 'Your Procta account has been deleted. Past exam evidence is retained without your personal details.');
  } catch (e) {
    _setTrackAStatus('account-delete-status', e.message || 'Could not delete account', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Confirm permanent deletion'; }
  }
}

// ─────── TRACK B: OTP password reset + email change ───────
let _emailChangeReauthToken = '';
function _setTrackBStatus(id, msg, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = msg ? 'block' : 'none';
  el.style.color = ok ? 'var(--emerald)' : 'var(--red)';
  el.textContent = msg || '';
}

async function requestPasswordResetOtp() {
  const btn = document.getElementById('password-reset-otp-request-btn');
  const email = (_currentAccount && _currentAccount.email) || '';
  if (!email) { _setTrackBStatus('password-reset-otp-status', 'Log in again before resetting your password.', false); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    const r = await authed('/api/v1/student/auth/reset-request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not send reset code');
    _setTrackBStatus('password-reset-otp-status', 'Code sent to your account email.', true);
  } catch (e) {
    _setTrackBStatus('password-reset-otp-status', e.message || 'Could not send reset code', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Send reset code'; }
  }
}

async function confirmPasswordResetOtp() {
  const btn = document.getElementById('password-reset-otp-confirm-btn');
  const email = (_currentAccount && _currentAccount.email) || '';
  const code = (document.getElementById('password-reset-otp-code')?.value || '').trim();
  const newPassword = document.getElementById('password-reset-otp-new')?.value || '';
  if (!/^\d{6}$/.test(code)) { _setTrackBStatus('password-reset-otp-status', 'Enter the 6-digit code.', false); return; }
  if (!isStrongPassword(newPassword)) {
    _setTrackBStatus('password-reset-otp-status', 'Password must be at least 10 characters and include uppercase, lowercase, a number, and a symbol.', false);
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Updating...'; }
  try {
    const r = await authed('/api/v1/student/auth/reset-confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, code, new_password: newPassword}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not update password');
    document.getElementById('password-reset-otp-code').value = '';
    document.getElementById('password-reset-otp-new').value = '';
    _setTrackBStatus('password-reset-otp-status', 'Password updated.', true);
  } catch (e) {
    _setTrackBStatus('password-reset-otp-status', e.message || 'Could not update password', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Update password'; }
  }
}

async function requestEmailChange() {
  const btn = document.getElementById('email-change-request-btn');
  const newEmail = (document.getElementById('account-new-email')?.value || '').trim().toLowerCase();
  const password = document.getElementById('account-email-password')?.value || '';
  if (!newEmail || !newEmail.includes('@')) { _setTrackBStatus('email-change-status', 'Enter a valid new email.', false); return; }
  if (!password) { _setTrackBStatus('email-change-status', 'Enter your current password first.', false); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
  try {
    const rr = await authed('/api/v1/student/auth/reauth', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password}),
    });
    if (!rr.ok) throw new Error((await rr.json().catch(()=>({}))).detail || 'Password confirmation failed');
    const rd = await rr.json();
    _emailChangeReauthToken = rd.reauth_token || '';
    const r = await authed('/api/v1/student/account/email-change-request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({new_email: newEmail, reauth_token: _emailChangeReauthToken}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not send verification code');
    _setTrackBStatus('email-change-status', 'Code sent to the new email. We also notified your old email.', true);
  } catch (e) {
    _setTrackBStatus('email-change-status', e.message || 'Could not start email change', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Send code'; }
  }
}

async function confirmEmailChange() {
  const btn = document.getElementById('email-change-confirm-btn');
  const newEmail = (document.getElementById('account-new-email')?.value || '').trim().toLowerCase();
  const code = (document.getElementById('account-email-code')?.value || '').trim();
  if (!/^\d{6}$/.test(code)) { _setTrackBStatus('email-change-status', 'Enter the 6-digit code.', false); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Confirming...'; }
  try {
    const r = await authed('/api/v1/student/account/email-change-confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({new_email: newEmail, code}),
    });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not change email');
    const d = await r.json();
    if (_currentAccount) _currentAccount.email = d.email || newEmail;
    document.getElementById('account-email-code').value = '';
    document.getElementById('account-email-password').value = '';
    _emailChangeReauthToken = '';
    _setTrackBStatus('email-change-status', 'Email changed. Use the new email the next time you sign in.', true);
  } catch (e) {
    _setTrackBStatus('email-change-status', e.message || 'Could not change email', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Confirm email change'; }
  }
}

// ─────── Exam reminder preferences ───────
function _setReminderStatus(msg, ok) {
  const el = document.getElementById('reminder-pref-status');
  if (!el) return;
  el.style.display = msg ? 'block' : 'none';
  el.style.color = ok ? 'var(--emerald)' : 'var(--red)';
  el.textContent = msg || '';
}

function renderReminderPreference(enabled) {
  const checkbox = document.getElementById('email-reminders-enabled');
  const badge = document.getElementById('reminder-pref-badge');
  const isEnabled = enabled !== false;
  if (checkbox) checkbox.checked = isEnabled;
  if (badge) {
    badge.textContent = isEnabled ? 'Enabled' : 'Off';
    badge.className = 'badge ' + (isEnabled ? 'badge-emerald' : 'badge-muted');
  }
}

async function loadReminderPreference() {
  renderReminderPreference(_currentAccount ? _currentAccount.email_reminders_enabled : true);
  try {
    const r = await authed('/api/v1/student/account/preferences');
    if (r.status === 401) { clearStudentSession(); return; }
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not load reminder setting');
    const d = await r.json();
    if (_currentAccount) _currentAccount.email_reminders_enabled = d.email_reminders_enabled !== false;
    renderReminderPreference(d.email_reminders_enabled);
    _setReminderStatus('', true);
  } catch (e) {
    _setReminderStatus(e.message || 'Could not load reminder setting', false);
  }
}

async function saveReminderPreference() {
  const btn = document.getElementById('save-reminder-pref-btn');
  const checkbox = document.getElementById('email-reminders-enabled');
  const enabled = !!(checkbox && checkbox.checked);
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    const r = await authed('/api/v1/student/account/preferences', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email_reminders_enabled: enabled}),
    });
    if (r.status === 401) { clearStudentSession(); return; }
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || 'Could not save reminder setting');
    const d = await r.json();
    if (_currentAccount) _currentAccount.email_reminders_enabled = d.email_reminders_enabled !== false;
    renderReminderPreference(d.email_reminders_enabled);
    _setReminderStatus(d.email_reminders_enabled === false
      ? 'Exam reminder emails are off.'
      : 'Exam reminder emails are on.',
      true);
  } catch (e) {
    renderReminderPreference(_currentAccount ? _currentAccount.email_reminders_enabled : true);
    _setReminderStatus(e.message || 'Could not save reminder setting', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
}

function clearStudentSession() {
  _studentCsrfMemory = '';
  authToken = '';
  refreshTok = '';
  studentAuthed = false;
  refreshInFlight = null;
  document.getElementById('dashboard').style.display = 'none';
  document.getElementById('auth-view').style.display = '';
  document.getElementById('web-landing').style.display = 'none';
  const err = document.getElementById('auth-err');
  if (err) err.textContent = '';
  const exams = document.getElementById('exams-container');
  if (exams) exams.innerHTML = '';
  _examsCache = [];
  _pendingExam = null;
  _pendingAccessCode = '';
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

// Captures the currently-logged-in account so other render paths
// (notably the empty-state exam list) can surface "logged in as
// <email>" for self-diagnosis when a teacher's roster lookup misses.
let _currentAccount = null;

// ─── dashboard ────────────────────────────────────────────────
async function showDashboard(account) {
  studentAuthed = true;
  _currentAccount = account || null;
  document.getElementById('auth-view').style.display = 'none';
  document.getElementById('dashboard').style.display = 'block';
  document.getElementById('me-name').textContent = account.full_name || account.email;
  renderReminderPreference(account.email_reminders_enabled);
  await loadExams();
  Promise.allSettled([loadHistory(), loadAppeals(), loadReminderPreference()])
    .then((results) => {
      results.forEach((res, idx) => {
        if (res.status === 'rejected') {
          console.warn('[dashboard] secondary load failed', idx, res.reason);
        }
      });
    });
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
    if (r.status === 401) { clearStudentSession(); return; }
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      throw new Error(d.detail || `Failed to load exams (${r.status})`);
    }
    const d = await r.json();
    renderExams(d.exams || []);
    _lastExamsFetch = Date.now();
  } catch (e) {
    if (!silent) {
      const baseHint = API_BASE
        ? `<div style="margin-top:10px;font-size:11px;color:var(--muted);font-family:var(--font-mono,monospace);word-break:break-all">API: <strong style="color:var(--text)">${_escHtml(API_BASE)}</strong></div>`
        : '';
      container.innerHTML = '<div class="exams-empty"><strong>Couldn\'t load exams</strong>'
        + _escHtml(e.message||'') + baseHint + '</div>';
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
    // No exams: surface the actual cause + remediation. The previous
    // "wait for your teacher" framing was correct but left the user
    // unsure what their teacher needed to do. Now we name the
    // mechanism: the teacher's roster must contain THIS exact email.
    const loggedInEmail = (_currentAccount && _currentAccount.email) || '';
    const emailHint = loggedInEmail
      ? `<div style="margin-top:10px;font-size:11px;color:var(--muted);font-family:var(--font-mono,monospace);word-break:break-all">Logged in as: <strong style="color:var(--text)">${_escHtml(loggedInEmail)}</strong></div>`
      : '';
    container.innerHTML = `
      <div class="exams-empty">
        <strong>No exams yet</strong>
        Your teacher needs to add your email to an exam roster (from
        their dashboard → Roster → Add Student). Once they do, the
        exam will appear here the next time you open the app — or
        immediately if it's already open.
        ${emailHint}
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
          <button class="btn btn-sm btn-secondary" data-action="openAppeal" data-args='${_escHtml(JSON.stringify([h.session_key]))}'>Appeal</button>
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
    // Only a CAMERA failure hard-blocks — a student genuinely can't be
    // proctored without a camera. A slow/unreachable network or a browser
    // warning is INFORMATIONAL: the exam tolerates slow connections, so it
    // must never stop a student from starting. (The old gate disabled Start
    // on ANY non-'ok' check, so slow wifi — even a 'warn' — blocked the exam
    // entirely, which is what students were hitting.)
    const cameraBlocked = _preflightResults.camera === 'fail';
    document.getElementById('preflight-start-btn').disabled = cameraBlocked;
    const errEl = document.getElementById('preflight-err');
    if (cameraBlocked) {
      errEl.textContent = 'Camera access is required to start. Allow your camera, then re-check.';
    } else if (Object.values(_preflightResults).some(v => v === 'fail' || v === 'warn')) {
      errEl.textContent = 'Some checks reported issues (e.g. a slow connection) — you can still start the exam.';
    } else {
      errEl.textContent = '';
    }
  });
}

function closePreflight() {
  document.getElementById('preflight-modal').classList.remove('active');
  _pendingExam = null;
  _pendingAccessCode = '';
}

function launchAfterPreflight() {
  const exam = _pendingExam;
  const code = _pendingAccessCode;
  closePreflight();
  launchExam(exam, code);
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

function closeCodeModal(opts) {
  const preservePending = !!(opts && opts.preservePending);
  document.getElementById('code-modal').classList.remove('active');
  if (!preservePending) {
    _pendingExam = null;
    _pendingAccessCode = '';
  }
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
  closeCodeModal({ preservePending: true });
  showPreflight();
}

async function launchExam(exam, accessCode) {
  if (!INSIDE_PROCTA_APP) {
    showModal('Open this page inside the Procta app to start your exam.');
    return;
  }
  // The preflight modal is the visible surface by the time launchExam
  // runs, so error feedback must land there. Earlier this set
  // #modal-err (the code-entry modal's error slot, which has already
  // been closed by confirmStartExam), so IPC failures were silently
  // invisible to the student.
  const preflightErr = document.getElementById('preflight-err');
  const btn = document.getElementById('preflight-start-btn') || document.getElementById('modal-start-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  if (preflightErr) preflightErr.textContent = '';
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
    if (preflightErr) {
      preflightErr.textContent = msg;
    } else {
      // Fall back to a modal so the student at least sees the error
      // if the preflight surface is gone for any reason.
      showModal(msg);
    }
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
function _showInviteMalformedBanner(){
  if (_inviteMalformedTimer) clearTimeout(_inviteMalformedTimer);
  let banner = document.getElementById('invite-malformed-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'invite-malformed-banner';
    banner.style.cssText = 'background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.35);'
      + 'border-radius:10px;padding:12px 14px;margin:0 0 14px 0;color:#fbbf24;font-size:13px;line-height:1.5';
    const card = document.querySelector('.auth-card');
    if (card) card.insertBefore(banner, card.firstChild.nextSibling);
  }
  banner.textContent = "We received a Procta link but couldn't read it. "
    + "Open your invite email and click the link there again.";
  _inviteMalformedTimer = setTimeout(() => {
    _inviteMalformedTimer = null;
    if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
  }, 10000);
}

async function _acceptPendingInvite(){
  if (!_pendingInvite || !studentAuthed) return;
  const token = _pendingInvite.token;
  try {
    const r = await authed('/api/v1/invite/' + encodeURIComponent(token) + '/accept', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      console.warn('[invite] accept failed:', r.status, d.detail || '');
      if ([403, 404, 410].includes(r.status)) {
        _pendingInvite = null;
      }
      const container = document.getElementById('exams-container');
      if (container && r.status >= 500) {
        container.innerHTML = '<div class="exams-empty"><strong>Invite not applied yet</strong>'
          + _escHtml(d.detail || 'Please try opening the invite again in a moment.') + '</div>';
      }
      return;
    }
    _pendingInvite = null; // one-shot after the roster row exists server-side.
    // Do NOT reload here. The Electron lobby is served from the
    // procta-lobby:// origin and keeps auth in memory; a full reload
    // drops the bearer token and can bounce the student back to login
    // right after accepting the invite. Refresh the exam list in-place.
    console.log('[invite] accepted, refreshing exams');
    await loadExams({ silent: false });
    Promise.allSettled([loadHistory(), loadReminderPreference()]).catch(() => {});
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
    if (window.procta_native && window.procta_native.onInviteTokenMalformed) {
      // The OS handed us a procta:// URL but it didn't parse. Tell the
      // student to re-copy the link from their email instead of leaving
      // them to wonder why their click did nothing.
      window.procta_native.onInviteTokenMalformed(() => _showInviteMalformedBanner());
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
      // Stored cookies/tokens are expired or missing. Do not call
      // doLogout() here: the server will also reject logout when the
      // access token is gone, and the old reload-on-401 path caused a
      // tight lobby reload loop that quickly hit /auth/me rate limits.
      clearStudentSession();
    }
  } catch {}
})();

// Re-check auth when page is restored from bfcache (back/forward navigation)
window.addEventListener('pageshow', (e) => {
  if (e.persisted) {
    if (!document.getElementById('web-landing')?.style.display?.includes('flex')) {
      authed('/api/v1/student/auth/me').then(async (r) => {
        if (r.ok) {
          await showDashboard(await r.json());
          if (_pendingInvite) await _acceptPendingInvite();
        } else {
          document.getElementById('auth-view').style.display = '';
        }
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
//
// Defense in depth: three triggers cover three scenarios.
//   1. Focus / visibilitychange — fires when the student alt-tabs back
//      after registering in an external browser. Fastest path.
//   2. Periodic background poll (BACKGROUND_REFRESH_MS) — guarantees
//      the dashboard becomes consistent even when no focus event ever
//      fires (e.g., the student keeps the Procta app focused while
//      registering in a sibling window).
//   3. Manual refresh button (#exams-refresh-btn, wired in dashboard
//      onload) — explicit user-driven recovery for the long-tail.
//
// A tiny FOCUS_DEBOUNCE_MS gate prevents rapid alt-tab storms from
// triggering N fetches in quick succession. silent mode + _examsInflight
// already prevent flicker and concurrent fetches; this is just a
// hand-of-restraint so we don't post a request per millisecond.
const FOCUS_DEBOUNCE_MS = 1_500;
const BACKGROUND_REFRESH_MS = 30_000;
function _canRefetchExams() {
  if (!studentAuthed) return false;
  if (document.getElementById('dashboard').style.display !== 'block') return false;
  return true;
}
function _maybeRefetchExamsOnFocus() {
  if (!_canRefetchExams()) return;
  if ((Date.now() - _lastExamsFetch) < FOCUS_DEBOUNCE_MS) return;
  loadExams({ silent: true });
}
window.addEventListener('focus', _maybeRefetchExamsOnFocus);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) _maybeRefetchExamsOnFocus();
});
// Background safety net. Cheap: silent + inflight guard means a fast
// path returns immediately when a foreground fetch is already running.
setInterval(() => {
  if (_canRefetchExams()) loadExams({ silent: true });
}, BACKGROUND_REFRESH_MS);

// Manual refresh button. Wired here (idempotent) so it works whether
// the button is present in the HTML or injected later. Bypasses the
// debounce — the user explicitly asked for a refresh.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('#exams-refresh-btn, [data-action="refreshExams"]');
  if (!btn) return;
  e.preventDefault();
  if (_canRefetchExams()) loadExams({ silent: true });
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

const _BLOCKED_DELEGATED_ACTIONS = new Set(['close', 'open', 'name', 'blur', 'focus', 'status', 'print', 'alert', 'confirm', 'prompt', 'eval', 'Function', 'fetch', 'constructor', 'Proxy', 'postMessage']);
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
