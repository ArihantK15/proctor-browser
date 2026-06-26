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
    out.push(btn("deleteExam", "delete", "text-error", "Delete"));
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
  onAction("deleteExam", (el) => mutate(eidOf(el), "", "DELETE", "Permanently delete this exam and its data? This cannot be undone."));
  // navigation stubs until those sections route in
  onAction("openMonitor", () => { window.location.href = "/dashboard-next"; });
  onAction("openResults", () => { /* TODO: route to Results detail */ });
  onAction("editExam", () => { /* TODO: open exam editor */ });

  load();
})();
