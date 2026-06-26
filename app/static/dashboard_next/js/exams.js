// Exams Repository — vanilla, CSP-safe. Renders the exam list with computed
// status, client-side filter tabs + search, and row actions (results/clone/
// archive/delete). Endpoints:
//   GET    /api/v1/admin/exams
//   POST   /api/v1/admin/exams/{id}/duplicate
//   POST   /api/v1/admin/exams/{id}/archive | /unarchive
//   DELETE /api/v1/admin/exams/{id}
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let all = [], filter = "all", query = "";

  function status(ex) {
    const now = Date.now();
    const s = ex.starts_at ? new Date(ex.starts_at).getTime() : null;
    const e = ex.ends_at ? new Date(ex.ends_at).getTime() : null;
    if (s == null) return "draft";
    if (now < s) return "scheduled";
    if (e != null && now > e) return "completed";
    return "live";
  }
  const fmtDay = (v) => { if (!v) return null; try { return new Date(v).toLocaleDateString([], { month: "short", day: "2-digit", year: "numeric" }).toUpperCase(); } catch (_) { return null; } };
  const fmtWin = (a, b) => { const f = (v) => { try { return new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } }; return a && b ? `${f(a)} - ${f(b)}` : (a ? f(a) : ""); };

  const BADGE = {
    live: ['bg-secondary/10 border border-secondary text-secondary', '<span class="w-1.5 h-1.5 bg-secondary rounded-full animate-pulse"></span> Live'],
    scheduled: ['bg-primary/10 border border-primary text-primary', '<span class="material-symbols-outlined text-[12px]">schedule</span> Scheduled'],
    completed: ['bg-outline-variant/20 border border-outline-variant text-outline', 'Completed'],
    draft: ['bg-surface-container-highest border border-outline-variant text-on-surface-variant', 'Draft'],
  };

  function metric(v, label) {
    return `<div class="flex flex-col"><span class="font-data-mono text-on-surface">${esc(v)}</span><span class="text-[10px] uppercase text-outline">${label}</span></div>`;
  }

  function actions(ex, st) {
    const eid = esc(ex.eid_enc);
    const btn = (action, icon, color, title) => `<button class="p-2 hover:bg-surface-container-highest rounded-lg transition-colors ${color}" title="${title}" data-action="${action}" data-eid="${eid}"><span class="material-symbols-outlined">${icon}</span></button>`;
    const out = [];
    if (st === "live") out.push(btn("openMonitor", "monitoring", "text-primary", "Live monitor"));
    else if (st === "completed") out.push(btn("openResults", "analytics", "text-secondary", "View results"));
    else out.push(btn("editExam", "edit", "text-on-surface-variant", "Edit"));
    out.push(btn("cloneExam", "content_copy", "text-on-surface-variant", "Clone"));
    out.push(btn(ex.archived_at ? "unarchiveExam" : "archiveExam", ex.archived_at ? "unarchive" : "archive", "text-on-surface-variant", ex.archived_at ? "Unarchive" : "Archive"));
    // NOTE: hard-delete (DELETE /admin/exams/{id}) requires an X-Reauth-Token; until the
    // reauth modal is ported, Archive is the safe/reversible path and we omit delete here.
    return `<div class="flex justify-end gap-sm">${out.join("")}</div>`;
  }

  function row(ex) {
    const st = status(ex);
    const [cls, inner] = BADGE[st];
    const day = fmtDay(ex.starts_at);
    const win = fmtWin(ex.starts_at, ex.ends_at);
    const access = day
      ? `<div class="flex flex-col"><span class="font-data-mono text-data-mono ${st === "live" ? "text-primary" : "text-on-surface"}">${esc(day)}</span><span class="text-[12px] text-on-surface-variant">${esc(win)}</span></div>`
      : `<span class="text-[12px] italic text-on-surface-variant">Not set</span>`;
    return `<tr class="table-row-hover transition-colors">
      <td class="px-lg py-lg"><div class="flex flex-col"><span class="font-bold text-on-surface">${esc(ex.exam_title || "Untitled exam")}</span><span class="text-body-sm text-on-surface-variant font-data-mono">${esc((ex.access_code || ex.exam_id || "").toString().slice(0, 12))}</span></div></td>
      <td class="px-lg py-lg">${access}</td>
      <td class="px-lg py-lg"><div class="flex gap-md">${metric(ex.session_count != null ? ex.session_count : "--", "Students")}${metric(ex.question_count != null ? ex.question_count : 0, "Qs")}${metric(ex.pass_mark != null ? ex.pass_mark + "%" : "--", "Pass")}</div></td>
      <td class="px-lg py-lg"><span class="px-sm py-1 ${cls} text-[11px] font-bold rounded-full uppercase flex items-center gap-1 w-fit">${inner}</span></td>
      <td class="px-lg py-lg text-right">${actions(ex, st)}</td>
    </tr>`;
  }

  function render() {
    const tb = $("exams-tbody"); if (!tb) return;
    const q = query.trim().toLowerCase();
    const rows = all.filter((ex) => {
      if (filter !== "all" && status(ex) !== filter) return false;
      if (q && !((ex.exam_title || "").toLowerCase().includes(q) || (ex.access_code || "").toLowerCase().includes(q) || (ex.exam_id || "").toLowerCase().includes(q))) return false;
      return true;
    });
    const sub = $("exams-subtitle"); if (sub) sub.textContent = `Managing ${all.length} examination${all.length === 1 ? "" : "s"}`;
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="5" class="px-lg py-xl text-center text-on-surface-variant text-body-sm">No exams match.</td></tr>'; return; }
    tb.innerHTML = rows.map(row).join("");
  }

  async function load() {
    try {
      const r = await authFetch("/api/v1/admin/exams?include_archived=1");
      if (!r.ok) return;
      const d = await r.json().catch(() => ({}));
      all = Array.isArray(d.exams) ? d.exams : [];
      all.forEach((ex) => { ex.eid_enc = encodeURIComponent(ex.exam_id || ""); });
      render();
    } catch (_) {}
  }

  // ---- filter tabs ----
  onAction("filterExams", (el) => {
    filter = el.getAttribute("data-filter") || "all";
    document.querySelectorAll('[data-action="filterExams"]').forEach((b) => {
      const on = b === el;
      b.classList.toggle("bg-primary-container", on);
      b.classList.toggle("text-on-primary-container", on);
      b.classList.toggle("font-bold", on);
      b.classList.toggle("text-on-surface-variant", !on);
      b.classList.toggle("font-medium", !on);
    });
    render();
  });

  // ---- search ----
  const search = $("exams-search");
  if (search) search.addEventListener("input", (e) => { query = e.target.value || ""; render(); });

  // ---- row actions ----
  const eidOf = (el) => decodeURIComponent(el.getAttribute("data-eid") || "");
  async function mutate(eid, path, method, confirmMsg) {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    try { const r = await authFetch(`/api/v1/admin/exams/${encodeURIComponent(eid)}${path}`, { method }); if (r.ok) load(); else alert("Action failed."); } catch (_) { alert("Action failed."); }
  }
  onAction("cloneExam", (el) => mutate(eidOf(el), "/duplicate", "POST"));
  onAction("archiveExam", (el) => mutate(eidOf(el), "/archive", "POST", "Archive this exam? Students can no longer join."));
  onAction("unarchiveExam", (el) => mutate(eidOf(el), "/unarchive", "POST"));
  // navigation stubs until those sections route in
  onAction("openMonitor", () => { window.location.href = "/dashboard-next"; });
  onAction("openResults", () => { /* TODO: route to Results detail */ });
  // ---- exam settings editor (schedule / pass-mark / access-code / sensitivity) ----
  const eLabel = "block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1";
  const eField = "w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 text-body-base focus:border-primary";
  const EE_HTML =
    '<div id="examEditModal" class="fixed inset-0 z-[100] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface-container border border-outline-variant w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl p-xl shadow-2xl">' +
    '<div class="flex items-center justify-between mb-lg"><h2 id="ee-title" class="font-bold text-lg">Exam Settings</h2>' +
    '<button data-action="eeClose" class="text-on-surface-variant hover:text-on-surface"><span class="material-symbols-outlined">close</span></button></div>' +
    '<div class="space-y-md">' +
    '<div class="grid grid-cols-2 gap-md"><div><label class="' + eLabel + '">Opens</label><input id="ee-start" type="datetime-local" class="' + eField + ' text-body-sm [color-scheme:dark]"/></div>' +
    '<div><label class="' + eLabel + '">Closes</label><input id="ee-end" type="datetime-local" class="' + eField + ' text-body-sm [color-scheme:dark]"/></div></div>' +
    '<div class="grid grid-cols-2 gap-md"><div><label class="' + eLabel + '">Early join (min)</label><input id="ee-early" type="number" min="0" max="240" class="' + eField + ' font-data-mono"/></div>' +
    '<div><label class="' + eLabel + '">Pass mark (%)</label><input id="ee-pass" type="number" min="0" max="100" class="' + eField + ' font-data-mono text-primary"/></div></div>' +
    '<div class="grid grid-cols-2 gap-md"><div><label class="' + eLabel + '">Access code</label><input id="ee-code" type="text" placeholder="(none)" class="' + eField + ' font-data-mono uppercase"/></div>' +
    '<div><label class="' + eLabel + '">Proctoring</label><select id="ee-sens" class="' + eField + ' font-semibold [&>option]:bg-surface-container"><option value="lenient">Lenient</option><option value="balanced">Balanced</option><option value="strict">Strict</option></select></div></div>' +
    '<p id="ee-err" class="text-error text-body-sm hidden"></p></div>' +
    '<div class="flex justify-end gap-md mt-xl"><button data-action="eeClose" class="px-lg py-md border border-outline-variant rounded-lg font-bold text-body-sm hover:bg-surface-container-high">Cancel</button>' +
    '<button id="ee-save" data-action="eeSave" class="px-lg py-md bg-primary text-on-primary rounded-lg font-bold text-body-sm hover:opacity-90">Save Settings</button></div></div></div>';
  const toLocal = (iso) => { if (!iso) return ""; try { const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); } catch (_) { return ""; } };
  const toIso = (v) => { try { return v ? new Date(v).toISOString() : null; } catch (_) { return null; } };
  let _eeExam = null;
  function eeEnsure() { if (!$("examEditModal")) { const h = document.createElement("div"); h.innerHTML = EE_HTML; document.body.appendChild(h.firstChild); } }
  onAction("editExam", (el) => {
    eeEnsure();
    const eid = eidOf(el); _eeExam = all.find((x) => String(x.exam_id) === String(eid)); if (!_eeExam) return;
    $("ee-title").textContent = `Settings — ${_eeExam.exam_title || "Exam"}`;
    $("ee-start").value = toLocal(_eeExam.starts_at); $("ee-end").value = toLocal(_eeExam.ends_at);
    $("ee-early").value = _eeExam.early_join_minutes != null ? _eeExam.early_join_minutes : 15;
    $("ee-pass").value = _eeExam.pass_mark != null ? _eeExam.pass_mark : 40;
    $("ee-code").value = _eeExam.access_code || "";
    $("ee-sens").value = (_eeExam.proctoring_sensitivity || "balanced");
    $("ee-err").classList.add("hidden");
    const m = $("examEditModal"); m.classList.remove("hidden"); m.classList.add("flex");
  });
  onAction("eeClose", () => { const m = $("examEditModal"); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } });
  onAction("eeSave", async (btn) => {
    if (!_eeExam) return;
    const eid = _eeExam.exam_id;
    const pm = parseInt($("ee-pass").value, 10);
    if (isNaN(pm) || pm < 0 || pm > 100) { $("ee-err").textContent = "Pass mark must be 0–100."; $("ee-err").classList.remove("hidden"); return; }
    btn.disabled = true; btn.textContent = "Saving…";
    const calls = [
      authFetch("/api/v1/admin/exam-schedule", { method: "POST", body: JSON.stringify({ exam_id: eid, starts_at: toIso($("ee-start").value), ends_at: toIso($("ee-end").value), early_join_minutes: parseInt($("ee-early").value, 10) || 0 }) }),
      authFetch("/api/v1/admin/exams/pass-mark", { method: "POST", body: JSON.stringify({ exam_id: eid, pass_mark: pm }) }),
      authFetch("/api/v1/admin/access-code", { method: "POST", body: JSON.stringify({ exam_id: eid, access_code: $("ee-code").value.trim() }) }),
      authFetch("/api/v1/admin/proctoring-sensitivity", { method: "POST", body: JSON.stringify({ exam_id: eid, proctoring_sensitivity: $("ee-sens").value }) }),
    ];
    try {
      const rs = await Promise.all(calls);
      if (rs.every((r) => r.ok)) { const m = $("examEditModal"); m.classList.add("hidden"); m.classList.remove("flex"); load(); }
      else { $("ee-err").textContent = "Some settings failed to save."; $("ee-err").classList.remove("hidden"); }
    } catch (_) { $("ee-err").textContent = "Save failed."; $("ee-err").classList.remove("hidden"); }
    finally { btn.disabled = false; btn.textContent = "Save Settings"; }
  });

  load();
})();
