// Shared client for dashboard_next (vanilla, CSP-safe). Cookie-session + CSRF,
// matching the legacy dashboard's auth model. Loaded before each section's JS.
(function () {
  const BASE = "";
  let _csrf = "";
  async function _getCsrf() {
    if (_csrf) return _csrf;
    try {
      const r = await fetch(BASE + "/api/v1/auth/csrf", { credentials: "include" });
      if (r.ok) { const d = await r.json().catch(() => ({})); _csrf = d.csrf_token || ""; }
    } catch (_) {}
    return _csrf;
  }
  // authFetch: credentials always included; CSRF header on mutations.
  async function authFetch(url, opts) {
    opts = opts || {};
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign({}, opts.headers || {});
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const c = await _getCsrf();
      if (c) headers["X-CSRF-Token"] = c;
      // Don't force JSON on FormData — the browser must set the multipart boundary itself.
      if (opts.body && !headers["Content-Type"] && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
    }
    const res = await fetch(BASE + url, Object.assign({}, opts, { credentials: "include", headers }));
    // Session expired/absent -> bounce to the unified login. Guarded so an
    // expected 401 from the in-app reauth password check (or the login/csrf
    // endpoints themselves) never triggers a redirect loop, and opt-out via
    // opts.noAuthRedirect for callers that handle 401 inline.
    if (res.status === 401 && !opts.noAuthRedirect && !/\/(reauth|login|csrf)\b/.test(url)) {
      const nx = encodeURIComponent(location.pathname + location.search);
      location.href = "/login?role=teacher&next=" + nx;
    }
    return res;
  }
  // Delegated data-action dispatch — replaces inline onclick (CSP: script-src 'self').
  const _actions = {};
  function onAction(name, fn) { _actions[name] = fn; }
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const fn = _actions[el.getAttribute("data-action")];
    if (fn) { e.preventDefault(); fn(el, e); }
  });
  // Shared selected-exam state (topbar selector). Persisted in localStorage so the
  // choice follows you across sections; consumers (live/results/questions) read
  // examId() and re-fetch on the "procta:examchange" event. "" = all exams.
  var EXAM_KEY = "procta_next_exam_id";
  function examId() { try { return localStorage.getItem(EXAM_KEY) || ""; } catch (_) { return ""; } }
  function onExamChange(cb) { window.addEventListener("procta:examchange", function (e) { cb(e.detail && e.detail.examId); }); }

  window.ProctaAPI = { BASE, authFetch, onAction, examId: examId, onExamChange: onExamChange };

  // Populate every topbar <select id="exam-select"> from the exam list (only when one
  // is present, so other sections don't pay for the fetch). Keeps multiple selects +
  // localStorage in sync and broadcasts changes.
  function wireExamSelect() {
    var selects = document.querySelectorAll("#exam-select");
    if (!selects.length) return;
    var stored = examId();
    authFetch("/api/v1/admin/exams").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var exams = (d && d.exams) ? d.exams : [];
      var opts = '<option value="">All exams</option>' + exams.map(function (e) {
        var t = String(e.exam_title || "Exam").replace(/[<>&"]/g, "");
        return '<option value="' + String(e.exam_id || "").replace(/"/g, "") + '">' + t + "</option>";
      }).join("");
      selects.forEach(function (sel) {
        sel.innerHTML = opts; sel.value = stored;
        sel.addEventListener("change", function () {
          try { localStorage.setItem(EXAM_KEY, sel.value); } catch (_) {}
          selects.forEach(function (s2) { if (s2 !== sel) s2.value = sel.value; });
          window.dispatchEvent(new CustomEvent("procta:examchange", { detail: { examId: sel.value } }));
        });
      });
    }).catch(function () {});
  }

  // Sidebar nav routing — every screen's sidebar ships `<a href="#">` placeholders.
  // Match each item by its `span.font-body-base` label and point it at the real
  // /dashboard-next route, so the built sections link together. Idempotent + label-
  // driven (labels are identical across all Stitch screens). The static "active"
  // highlight each screen bakes in for its own item is left as-is.
  // Order matters: check "live monitor" before "live" would, etc. Labels don't nest,
  // so a plain includes() against the anchor's text is safe (the icon ligature in the
  // text — "dashboard", "analytics", … — never collides with a different section).
  var NAV_ROUTES = [
    ["overview", "/dashboard-next/overview"],
    ["live monitor", "/dashboard-next"],
    ["questions", "/dashboard-next/questions"],
    ["exams", "/dashboard-next/exams"],
    ["students", "/dashboard-next/students"],
    ["results", "/dashboard-next/results"],
    ["integrations", "/dashboard-next/integrations"],
    ["settings", "/dashboard-next/settings"]
  ];
  function wireNav() {
    // Two Stitch markups: a label <span class="font-body-base">, or raw text after the
    // icon span. Match on the anchor's whole text so both work; only rewrite the known
    // nav items (other href="#" anchors like "Create Exam" won't match a section label).
    var anchors = document.querySelectorAll('a[href="#"]');
    for (var i = 0; i < anchors.length; i++) {
      var txt = (anchors[i].textContent || "").trim().toLowerCase();
      if (txt.indexOf("create exam") !== -1) continue; // guard: contains "exam"
      for (var j = 0; j < NAV_ROUTES.length; j++) {
        if (txt.indexOf(NAV_ROUTES[j][0]) !== -1) { anchors[i].setAttribute("href", NAV_ROUTES[j][1]); break; }
      }
    }
  }
  // ---- Create Exam (shared) — the sidebar "Create Exam" button ships on every screen
  // but no modal was designed, so inject one once + wire every trigger by text. POSTs
  // /admin/exams (+ optional /exam-schedule), selects the new exam, lands on Exams. ----
  var CE_HTML =
    '<div id="createExamModal" class="fixed inset-0 z-[100] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface-container border border-outline-variant w-full max-w-lg rounded-2xl p-xl shadow-2xl">' +
    '<div class="flex items-center justify-between mb-lg"><h2 class="font-bold text-lg text-on-surface">Create Exam</h2>' +
    '<button data-action="closeCreateExam" class="text-on-surface-variant hover:text-on-surface"><span class="material-symbols-outlined">close</span></button></div>' +
    '<div class="space-y-md">' +
    '<div><label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Exam Title</label>' +
    '<input id="ce-title" type="text" placeholder="e.g. Physics Midterm" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 text-body-base focus:border-primary"/></div>' +
    '<div class="grid grid-cols-2 gap-md">' +
    '<div><label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Duration (min)</label>' +
    '<input id="ce-duration" type="number" min="1" value="60" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 font-data-mono text-primary focus:border-primary"/></div>' +
    '<div><label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Proctoring Camera</label>' +
    '<select id="ce-phone" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 font-semibold focus:border-primary [&>option]:bg-surface-container"><option value="false">Webcam only</option><option value="true">+ Phone camera</option></select></div></div>' +
    '<div class="grid grid-cols-2 gap-md">' +
    '<div><label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Opens (optional)</label>' +
    '<input id="ce-start" type="datetime-local" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 text-body-sm focus:border-primary [color-scheme:dark]"/></div>' +
    '<div><label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Closes (optional)</label>' +
    '<input id="ce-end" type="datetime-local" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 text-body-sm focus:border-primary [color-scheme:dark]"/></div></div>' +
    '<p id="ce-err" class="text-error text-body-sm hidden"></p></div>' +
    '<div class="flex justify-end gap-md mt-xl"><button data-action="closeCreateExam" class="px-lg py-md border border-outline-variant rounded-lg font-bold text-body-sm hover:bg-surface-container-high">Cancel</button>' +
    '<button id="ce-submit" data-action="submitCreateExam" class="px-lg py-md bg-primary text-on-primary rounded-lg font-bold text-body-sm hover:opacity-90">Create Exam</button></div></div></div>';

  function ceModal() { return document.getElementById("createExamModal"); }
  function ceOpen() { var m = ceModal(); if (m) { m.classList.remove("hidden"); m.classList.add("flex"); var t = document.getElementById("ce-title"); if (t) { t.value = ""; t.focus(); } } }
  function ceClose() { var m = ceModal(); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } }
  function ceIso(v) { try { return v ? new Date(v).toISOString() : null; } catch (_) { return null; } }
  onAction("openCreateExam", ceOpen);
  onAction("closeCreateExam", ceClose);
  onAction("submitCreateExam", async function (el) {
    var title = (document.getElementById("ce-title") || {}).value || "";
    var err = document.getElementById("ce-err");
    if (!title.trim()) { if (err) { err.textContent = "Title is required."; err.classList.remove("hidden"); } return; }
    if (err) err.classList.add("hidden");
    el.disabled = true; el.textContent = "Creating…";
    try {
      var dur = parseInt((document.getElementById("ce-duration") || {}).value, 10) || 60;
      var phone = (document.getElementById("ce-phone") || {}).value === "true";
      var r = await authFetch("/api/v1/admin/exams", { method: "POST", body: JSON.stringify({ exam_title: title.trim(), duration_minutes: dur, phone_camera: phone }) });
      if (!r.ok) { var d = await r.json().catch(function () { return {}; }); throw new Error(d.detail || ("HTTP " + r.status)); }
      var created = await r.json();
      var eid = created.exam_id;
      var start = ceIso((document.getElementById("ce-start") || {}).value);
      var end = ceIso((document.getElementById("ce-end") || {}).value);
      if (eid && (start || end)) {
        try { await authFetch("/api/v1/admin/exam-schedule", { method: "POST", body: JSON.stringify({ exam_id: eid, starts_at: start, ends_at: end }) }); } catch (_) {}
      }
      if (eid) { try { localStorage.setItem(EXAM_KEY, eid); } catch (_) {} }
      window.location.href = "/dashboard-next/exams";
    } catch (e) {
      if (err) { err.textContent = "Create failed: " + (e.message || e); err.classList.remove("hidden"); }
      el.disabled = false; el.textContent = "Create Exam";
    }
  });
  function wireCreateExam() {
    if (!document.getElementById("createExamModal")) {
      var holder = document.createElement("div"); holder.innerHTML = CE_HTML;
      document.body.appendChild(holder.firstChild);
    }
    var trigs = document.querySelectorAll("button, a");
    for (var i = 0; i < trigs.length; i++) {
      var el = trigs[i];
      var t = (el.textContent || "").trim().toLowerCase();
      // match "Create Exam"/"Create exam" (text may include an icon ligature); skip the
      // modal's own buttons (they carry data-action / live inside #createExamModal).
      if (t.indexOf("create exam") !== -1 && !el.hasAttribute("data-ce-wired") &&
          !el.hasAttribute("data-action") && !el.closest("#createExamModal")) {
        el.setAttribute("data-ce-wired", "1");
        el.addEventListener("click", function (e) { e.preventDefault(); ceOpen(); });
      }
    }
  }

  // ---- Reauth (shared) — destructive actions (hard-delete exam / account) need a
  // short-lived token from POST /api/v1/auth/reauth {password}. reauth(label) opens a
  // password modal and resolves the token (or null if cancelled/failed). ----
  var RA_HTML =
    '<div id="reauthModal" class="fixed inset-0 z-[110] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface-container border border-error/30 w-full max-w-sm rounded-2xl p-xl shadow-2xl">' +
    '<div class="flex items-center gap-md mb-md text-error"><span class="material-symbols-outlined">lock</span><h2 class="font-bold text-lg text-on-surface">Confirm it\'s you</h2></div>' +
    '<p class="text-on-surface-variant text-body-sm mb-md">Re-enter your password to <span id="reauth-action" class="text-on-surface font-semibold">continue</span>.</p>' +
    '<input id="reauth-pwd" type="password" placeholder="Password" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 text-body-base focus:border-primary"/>' +
    '<p id="reauth-err" class="text-error text-body-sm mt-2 hidden"></p>' +
    '<div class="flex justify-end gap-md mt-lg"><button data-action="reauthCancel" class="px-lg py-md border border-outline-variant rounded-lg font-bold text-body-sm hover:bg-surface-container-high">Cancel</button>' +
    '<button data-action="reauthSubmit" id="reauth-go" class="px-lg py-md bg-error text-on-error rounded-lg font-bold text-body-sm hover:opacity-90">Confirm</button></div></div></div>';
  var _reauthResolve = null;
  function raEnsure() { if (!document.getElementById("reauthModal")) { var h = document.createElement("div"); h.innerHTML = RA_HTML; document.body.appendChild(h.firstChild); } }
  function raClose(tok) { var m = document.getElementById("reauthModal"); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } var r = _reauthResolve; _reauthResolve = null; if (r) r(tok); }
  function reauth(label) {
    return new Promise(function (resolve) {
      raEnsure(); _reauthResolve = resolve;
      var a = document.getElementById("reauth-action"); if (a) a.textContent = label || "continue";
      var p = document.getElementById("reauth-pwd"); if (p) p.value = "";
      var e = document.getElementById("reauth-err"); if (e) e.classList.add("hidden");
      var m = document.getElementById("reauthModal"); m.classList.remove("hidden"); m.classList.add("flex"); if (p) p.focus();
    });
  }
  onAction("reauthCancel", function () { raClose(null); });
  onAction("reauthSubmit", async function (btn) {
    var pwd = (document.getElementById("reauth-pwd") || {}).value || "";
    var err = document.getElementById("reauth-err");
    if (!pwd) { if (err) { err.textContent = "Enter your password."; err.classList.remove("hidden"); } return; }
    btn.disabled = true; btn.textContent = "Verifying…";
    try {
      var r = await authFetch("/api/v1/auth/reauth", { method: "POST", body: JSON.stringify({ password: pwd }) });
      if (r.ok) { var d = await r.json().catch(function () { return {}; }); raClose(d.reauth_token || null); }
      else { if (err) { err.textContent = "Invalid password."; err.classList.remove("hidden"); } }
    } catch (_) { if (err) { err.textContent = "Verification failed."; err.classList.remove("hidden"); } }
    finally { btn.disabled = false; btn.textContent = "Confirm"; }
  });
  window.ProctaAPI.reauth = reauth;

  // Wire any "Log Out" control in the sidebar/topbar to the real logout. The
  // Stitch markup ships plain text buttons with no data-action, so (like
  // wireNav) we match by trimmed text content and attach a handler — avoids
  // editing every screen's HTML. Idempotent via a dataset guard.
  async function doLogout() {
    try { await authFetch("/api/v1/auth/logout", { method: "POST" }); } catch (_) {}
    location.href = "/login?role=teacher";
  }
  function wireLogout() {
    var nodes = document.querySelectorAll("a,button");
    nodes.forEach(function (el) {
      if (el.dataset.logoutWired) return;
      var t = (el.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (t === "log out" || t === "logout" || t === "sign out") {
        el.dataset.logoutWired = "1";
        el.addEventListener("click", function (e) { e.preventDefault(); doLogout(); });
      }
    });
  }

  function initShared() { wireNav(); wireExamSelect(); wireCreateExam(); wireLogout(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initShared);
  else initShared();
})();
