// Student Roster — vanilla, CSP-safe. Directory + self-registration link + counts
// + remove-from-roster (delete modal). NOTE: the only list endpoint is the
// session-derived directory (/admin/student-history), so it surfaces students who
// have attempted at least one exam; never-tested registrants show only in the
// total count (no full students-table GET exists). Endpoints:
//   GET    /api/v1/auth/me                       (teacher id -> reg link)
//   GET    /api/v1/admin/student-history         (directory rows)
//   GET    /api/v1/admin/student-batches         (group filter)
//   GET    /api/v1/admin/registered-count        (Active total)
//   DELETE /api/v1/admin/students/roster         (remove; confirm_warnings flow)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let all = [], group = "", query = "", pendingRoll = null;

  const initials = (name) => { const p = String(name || "").trim().split(/\s+/); return ((p[0] || "")[0] || "") + ((p[1] || "")[0] || "") || "?"; };
  const TONES = ["primary", "tertiary", "secondary"];
  const riskOf = (s) => (s && s.last_exam_risk != null ? Math.round(s.last_exam_risk) : null);

  function row(s, i) {
    const t = TONES[i % TONES.length];
    const r = riskOf(s);
    const flagged = r != null && r >= 70;
    const stTone = flagged ? "error" : "secondary";
    const stLabel = flagged ? "Flagged" : "Active";
    const grp = (s.batch || "").trim();
    return `<tr class="hover:bg-surface-container-high/50 transition-colors group">
      <td class="px-lg py-md"><div class="flex items-center gap-md">
        <div class="w-10 h-10 rounded-lg bg-${t}/10 flex items-center justify-center text-${t} font-bold">${esc(initials(s.full_name).toUpperCase())}</div>
        <div class="flex flex-col"><span class="font-bold text-on-surface">${esc(s.full_name || "Unnamed")}</span>
        <span class="text-[10px] text-on-surface-variant uppercase font-bold">${esc(s.last_exam_at ? "Last active " + s.last_exam_at : "")}</span></div></div></td>
      <td class="px-lg py-md font-data-mono text-primary text-body-sm">${esc(s.roll_number || "")}</td>
      <td class="px-lg py-md"><div class="flex items-center gap-sm text-body-sm text-on-surface-variant"><span class="material-symbols-outlined text-sm">mail</span>${esc(s.email || "—")}</div></td>
      <td class="px-lg py-md">${grp ? `<span class="px-sm py-1 bg-surface-container-highest border border-outline-variant rounded-full text-[11px] font-bold text-on-surface-variant">${esc(grp)}</span>` : '<span class="text-on-surface-variant text-[11px]">—</span>'}</td>
      <td class="px-lg py-md text-center"><span class="font-data-mono text-on-surface">${esc(s.total_exams != null ? s.total_exams : 0)}</span></td>
      <td class="px-lg py-md"><div class="flex items-center gap-xs"><div class="w-2 h-2 rounded-full bg-${stTone}"></div><span class="text-[11px] font-bold uppercase text-${stTone}">${stLabel}</span></div></td>
      <td class="px-lg py-md text-right"><div class="flex items-center justify-end gap-xs opacity-0 group-hover:opacity-100 transition-opacity">
        <button class="p-xs text-on-surface-variant hover:text-error transition-colors" title="Remove" data-action="askRemoveStudent" data-roll="${esc(s.roll_number || "")}" data-name="${esc(s.full_name || s.roll_number || "")}"><span class="material-symbols-outlined text-xl">person_remove</span></button>
      </div></td>
    </tr>`;
  }

  function render() {
    const tb = $("roster-tbody"); if (!tb) return;
    const q = query.trim().toLowerCase();
    const rows = all.filter((s) => {
      if (group && (s.batch || "") !== group) return false;
      if (q && !((s.full_name || "").toLowerCase().includes(q) || (s.roll_number || "").toLowerCase().includes(q) || (s.email || "").toLowerCase().includes(q))) return false;
      return true;
    });
    const cnt = $("roster-count"); if (cnt) cnt.textContent = rows.length;
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="7" class="px-lg py-xl text-center text-on-surface-variant text-body-sm">No students match. Students appear here after their first exam attempt.</td></tr>'; return; }
    tb.innerHTML = rows.map((s, i) => row(s, i)).join("");
  }

  async function getJSON(url) { try { const r = await authFetch(url); return r.ok ? await r.json().catch(() => null) : null; } catch (_) { return null; } }

  async function loadGroups() {
    const d = await getJSON("/api/v1/admin/student-batches");
    const sel = $("roster-group");
    const batches = d && Array.isArray(d.batches) ? d.batches : (Array.isArray(d) ? d : []);
    if (sel && batches.length) {
      sel.innerHTML = '<option value="">All Groups</option>' + batches.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join("");
      sel.addEventListener("change", (e) => { group = e.target.value || ""; render(); });
    }
  }

  async function counts() {
    const rc = await getJSON("/api/v1/admin/registered-count");
    const ca = $("count-active"); if (ca) ca.textContent = rc && rc.count != null ? Number(rc.count).toLocaleString() : String(all.length);
    const cf = $("count-flagged"); if (cf) cf.textContent = String(all.filter((s) => { const r = riskOf(s); return r != null && r >= 70; }).length);
    // Pending = registered total minus those who've attempted an exam (directory size).
    const cp = $("count-pending");
    if (cp && rc && rc.count != null) cp.textContent = String(Math.max(0, Number(rc.count) - all.length));
  }

  async function regLink() {
    const me = await getJSON("/api/v1/auth/me");
    const tid = me && (me.id || me.teacher_id);
    const code = $("reg-link");
    if (code && tid) code.textContent = `${location.origin}/register?t=${encodeURIComponent(tid)}`;
  }

  async function load() {
    regLink(); loadGroups();
    const d = await getJSON("/api/v1/admin/student-history?page=1&page_size=500");
    all = d && Array.isArray(d.students) ? d.students : [];
    render(); counts();
  }

  // ---- search ----
  const search = $("roster-search");
  if (search) search.addEventListener("input", (e) => { query = e.target.value || ""; render(); });

  // ---- copy reg link ----
  onAction("copyRegLink", () => { const c = $("reg-link"); if (c && navigator.clipboard) navigator.clipboard.writeText(c.textContent).catch(() => {}); });

  // ---- remove modal ----
  function openModal() { const m = $("warningModal"); if (m) { m.classList.remove("hidden"); m.classList.add("flex"); } }
  function closeModal() { const m = $("warningModal"); if (m) { m.classList.remove("flex"); m.classList.add("hidden"); } pendingRoll = null; }
  onAction("askRemoveStudent", (el) => {
    pendingRoll = el.getAttribute("data-roll") || "";
    const disp = $("studentNameDisplay"); if (disp) disp.textContent = el.getAttribute("data-name") || pendingRoll;
    openModal();
  });
  onAction("closeStudentModal", closeModal);
  onAction("executeStudentDelete", async () => {
    if (!pendingRoll) { closeModal(); return; }
    const roll = pendingRoll;
    const del = async (confirmWarnings) => {
      const qs = new URLSearchParams({ roll_number: roll });
      if (confirmWarnings) qs.set("confirm_warnings", "true");
      return authFetch(`/api/v1/admin/students/roster?${qs.toString()}`, { method: "DELETE" });
    };
    try {
      let r = await del(false);
      let d = r.ok ? await r.json().catch(() => ({})) : null;
      if (d && d.needs_confirmation) {
        if (!window.confirm("This student has an active exam session. Remove from roster anyway?")) { closeModal(); return; }
        r = await del(true);
      }
      closeModal();
      if (r.ok) load(); else alert("Removal failed.");
    } catch (_) { closeModal(); alert("Removal failed."); }
  });

  load();
})();
