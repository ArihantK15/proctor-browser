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
      if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    }
    return fetch(BASE + url, Object.assign({}, opts, { credentials: "include", headers }));
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
  function initShared() { wireNav(); wireExamSelect(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initShared);
  else initShared();
})();
