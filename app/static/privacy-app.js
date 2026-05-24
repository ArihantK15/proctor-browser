const TOKEN = localStorage.getItem('procta_token') || localStorage.getItem('procta_student_token') || '';
const BASE = '';

function _getCsrf() {
  try { return sessionStorage.getItem('procta_csrf') || localStorage.getItem('procta_csrf') || ''; }
  catch(e) { return ''; }
}
function _esc(s){ var d=document.createElement('div'); d.appendChild(document.createTextNode(s||'')); return d.innerHTML; }
const headers = {'Authorization':'Bearer '+TOKEN, 'Content-Type':'application/json'};

function fetchWithTimeout(url, opts={}, timeoutMs=30000){
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
  return fetch(url, {...opts, signal: opts.signal || ctrl.signal}).finally(()=>clearTimeout(timer));
}

async function authFetch(url, opts){
  const method = ((opts && opts.method) || 'GET').toUpperCase();
  const nextHeaders = {...headers,...(opts?.headers||{})};
  if (!['GET','HEAD','OPTIONS'].includes(method)) {
    let csrf = '';
    if (TOKEN) {
      const r = await fetchWithTimeout(BASE + '/api/v1/auth/csrf', {headers:{'Authorization':'Bearer '+TOKEN}});
      if (r.ok) {
        const d = await r.json().catch(()=>({}));
        csrf = d.csrf_token || '';
        if (csrf) {
          try {
            sessionStorage.setItem('procta_csrf', csrf);
            localStorage.setItem('procta_csrf', csrf);
          } catch(e) {}
        }
      }
    }
    if (csrf) nextHeaders['X-CSRF-Token'] = csrf;
  }
  return fetchWithTimeout(BASE+url, {...opts, headers:nextHeaders});
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
async function confirmDelete(){
  const btn = document.querySelector('#delete-modal .btn-danger');
  const err = document.getElementById('delete-modal-err');
  btn.disabled = true;
  err.textContent = '';
  try{
    const r = await authFetch('/api/v1/privacy/delete', {method:'POST'});
    const d = await r.json();
    if(r.ok){
      err.innerHTML = '<span style="color:#16a34a">Account deletion initiated. You will be redirected.</span>';
      setTimeout(() => { localStorage.clear(); window.location.href = '/'; }, 2000);
    }else{
      err.textContent = d.detail || 'Deletion failed.';
    }
  }catch(e){
    err.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
}
