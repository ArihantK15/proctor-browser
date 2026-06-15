const BASE = '';
let _csrfMemory = '';

function _getCsrf() {
  return _csrfMemory || '';
}
function _esc(s){ var d=document.createElement('div'); d.appendChild(document.createTextNode(s||'')); return d.innerHTML; }
// Local copy — this standalone page does not load _safe.js. Keep in sync with
// _safe.js::_detailText. Renders FastAPI 422 detail arrays (which would
// otherwise show "[object Object]") as readable text.
function _detailText(d, fallback) {
  var det = d && d.detail;
  if (typeof det === 'string' && det) return det;
  if (Array.isArray(det)) {
    var msgs = det.map(function (x) { return (x && x.msg) ? x.msg : ''; }).filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  if (det && typeof det === 'object' && typeof det.msg === 'string' && det.msg) return det.msg;
  if (d && typeof d.message === 'string' && d.message) return d.message;
  return fallback;
}
const headers = {'Content-Type':'application/json'};

function fetchWithTimeout(url, opts={}, timeoutMs=30000){
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
  return fetch(url, {...opts, signal: opts.signal || ctrl.signal}).finally(()=>clearTimeout(timer));
}

async function authFetch(url, opts){
  const method = ((opts && opts.method) || 'GET').toUpperCase();
  const nextHeaders = {...headers,...(opts?.headers||{})};
  let csrf = _getCsrf();
  if (!['GET','HEAD','OPTIONS'].includes(method)) {
    if (!csrf) {
      const r = await fetchWithTimeout(BASE + '/api/v1/auth/csrf', {credentials:'include'});
      if (r.ok) {
        const d = await r.json().catch(()=>({}));
        csrf = d.csrf_token || '';
        if (csrf) _csrfMemory = csrf;
      }
    }
    if (csrf) nextHeaders['X-CSRF-Token'] = csrf;
  }
  return fetchWithTimeout(BASE+url, {...opts, credentials:'include', headers:nextHeaders});
}

// Consent list
async function loadConsent(){
  try{
    const r = await authFetch('/api/v1/privacy/export');
    if(!r.ok) return;
    const d = await r.json();
    const list = document.getElementById('consent-list');
    const recs = d.consent_records || [];
    if(recs.length){
      list.innerHTML = recs.map(c =>
        '<li>' + c.consent_type + ' &mdash; ' + new Date(c.created_at).toLocaleDateString() + '</li>'
      ).join('');
    }else{
      list.innerHTML = '<li>No consent records yet.</li>';
    }
  }catch(e){}
}
loadConsent();

// Export
async function doExport(){
  const btn = document.getElementById('export-btn');
  const status = document.getElementById('export-status');
  btn.disabled = true;
  status.innerHTML = 'Exporting...';
  try{
    const r = await authFetch('/api/v1/privacy/export');
    if(!r.ok){ status.innerHTML = '<span class="err">Failed: HTTP ' + r.status + '</span>'; btn.disabled = false; return; }
    const d = await r.json();
    const blob = new Blob([JSON.stringify(d, null, 2)], {type:'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'procta-data-export.json'; a.click();
    URL.revokeObjectURL(url);
    status.innerHTML = '<span class="ok">Downloaded successfully.</span>';
  }catch(e){
    status.innerHTML = '<span class="err">Error: ' + _esc(e.message) + '</span>';
  }
  btn.disabled = false;
}

// Delete
function showDeleteConfirm(){
  document.getElementById('delete-modal').classList.add('active');
}
function closeDeleteConfirm(){
  document.getElementById('delete-modal').classList.remove('active');
  document.getElementById('delete-modal-err').textContent = '';
}
async function _getPrivacyReauthToken(){
  const password = window.prompt('Enter your password to delete this account:');
  if(!password) return '';
  const body = JSON.stringify({password});
  for(const path of ['/api/v1/auth/reauth', '/api/v1/student/auth/reauth']){
    const r = await authFetch(path, {method:'POST', body});
    if(r.ok){
      const d = await r.json().catch(()=>({}));
      return d.reauth_token || '';
    }
    if(r.status !== 401 && r.status !== 403) break;
  }
  throw new Error('Password verification failed.');
}
// Objection
async function submitObjection(){
  const btn = document.getElementById('objection-btn');
  const status = document.getElementById('objection-status');
  btn.disabled = true;
  status.innerHTML = 'Submitting objection...';
  try{
    const grounds = document.getElementById('objection-grounds').value;
    const scope = document.getElementById('objection-scope').value;
    const r = await authFetch('/api/v1/privacy/object', {
      method:'POST',
      body:JSON.stringify({grounds, scope}),
    });
    const d = await r.json();
    if(r.ok){
      status.innerHTML = '<span class="ok">Objection submitted. You will be contacted by the controller.</span>';
      document.getElementById('objection-grounds').value = '';
      document.getElementById('objection-scope').value = 'all';
    }else{
      status.innerHTML = '<span class="err">' + _esc(_detailText(d, 'Failed to submit objection.')) + '</span>';
    }
  }catch(e){
    status.innerHTML = '<span class="err">Error: ' + _esc(e.message) + '</span>';
  }
  btn.disabled = false;
}

async function confirmDelete(){
  const btn = document.querySelector('#delete-modal .btn-danger');
  const err = document.getElementById('delete-modal-err');
  btn.disabled = true;
  err.textContent = '';
  try{
    const reauth_token = await _getPrivacyReauthToken();
    if(!reauth_token) throw new Error('Password verification is required.');
    const r = await authFetch('/api/v1/privacy/delete', {
      method:'POST',
      body:JSON.stringify({reauth_token}),
    });
    const d = await r.json();
    if(r.ok){
      err.innerHTML = '<span style="color:#16a34a">Account deletion initiated. You will be redirected.</span>';
      setTimeout(() => { localStorage.clear(); window.location.href = '/'; }, 2000);
    }else{
      err.textContent = _detailText(d, 'Deletion failed.');
    }
  }catch(e){
    err.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
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
  if (e.target.closest('a') === el) e.preventDefault();
  const fn = _resolveDelegatedAction(el.dataset.action);
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.args));
});
