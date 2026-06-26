// Lean shared client for the student dashboard (separate surface from the teacher
// dashboard — student cookie session, no teacher nav/exam wiring). credentials are
// always included; CSRF added on mutations.
(function () {
  const BASE = "";
  let _csrf = "";
  async function _getCsrf() {
    if (_csrf) return _csrf;
    try { const r = await fetch(BASE + "/api/v1/auth/csrf", { credentials: "include" }); if (r.ok) { const d = await r.json().catch(() => ({})); _csrf = d.csrf_token || ""; } } catch (_) {}
    return _csrf;
  }
  async function authFetch(url, opts) {
    opts = opts || {};
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign({}, opts.headers || {});
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const c = await _getCsrf(); if (c) headers["X-CSRF-Token"] = c;
      if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    }
    const res = await fetch(BASE + url, Object.assign({}, opts, { credentials: "include", headers }));
    // Session expired/absent -> unified login (student role). Guarded against
    // the reauth/login/csrf endpoints so an expected 401 never loops.
    if (res.status === 401 && !opts.noAuthRedirect && !/\/(reauth|login|csrf)\b/.test(url)) {
      const nx = encodeURIComponent(location.pathname + location.search);
      location.href = "/login?role=student&next=" + nx;
    }
    return res;
  }
  const _actions = {};
  function onAction(name, fn) { _actions[name] = fn; }
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-action]"); if (!el) return;
    const fn = _actions[el.getAttribute("data-action")]; if (fn) { e.preventDefault(); fn(el, e); }
  });
  window.StudentAPI = { BASE, authFetch, onAction };
})();
