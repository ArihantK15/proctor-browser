// Org Members admin — list + invite + change-role + remove. Endpoints:
//   GET    /api/v1/org/members              {members:[{id,email,full_name,org_role,created_at}]}
//   POST   /api/v1/org/invite               {email[,role]}
//   PATCH  /api/v1/org/members/{id}/role     {role:"admin"|"teacher"}
//   DELETE /api/v1/org/members/{id}
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const initials = (n) => { const p = String(n || "").trim().split(/\s+/); return (((p[0] || "")[0] || "") + ((p[1] || "")[0] || "") || "?").toUpperCase(); };
  let members = [], query = "";

  function roleChip(role) {
    const r = (role || "teacher").toLowerCase();
    if (r === "admin" || r === "superadmin") return `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-DEFAULT bg-inverse-primary/20 border border-inverse-primary/30 text-primary font-label-caps text-label-caps"><span class="material-symbols-outlined text-[14px]">admin_panel_settings</span> ${r === "superadmin" ? "Superadmin" : "Admin"}</span>`;
    return `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-DEFAULT bg-surface-container-highest border border-outline-variant text-on-surface font-label-caps text-label-caps"><span class="material-symbols-outlined text-[14px]">school</span> Teacher</span>`;
  }
  function row(m) {
    const isSuper = (m.org_role || "").toLowerCase() === "superadmin";
    const toggleTo = (m.org_role || "").toLowerCase() === "admin" ? "teacher" : "admin";
    return `<tr class="group hover:bg-surface-container-high transition-colors">
      <td class="py-sm px-sm"><div class="flex items-center gap-md">
        <div class="w-10 h-10 rounded-full border border-outline-variant bg-surface-container-highest flex items-center justify-center font-bold text-on-surface-variant text-sm">${esc(initials(m.full_name))}</div>
        <div class="flex flex-col"><span class="font-body-base text-body-base font-semibold text-on-surface">${esc(m.full_name || "—")}</span><span class="font-body-sm text-body-sm text-on-surface-variant">${esc(m.email || "")}</span></div></div></td>
      <td class="py-sm px-sm">${roleChip(m.org_role)}</td>
      <td class="py-sm px-sm"><div class="flex items-center gap-xs"><div class="w-2 h-2 rounded-full bg-secondary"></div><span class="font-body-sm text-body-sm text-on-surface">Active</span></div></td>
      <td class="py-sm px-sm text-right"><span class="font-data-mono text-data-mono text-on-surface-variant">${esc(m.created_at || "")}</span></td>
      <td class="py-sm px-sm text-right">${isSuper ? "" : `<div class="flex items-center justify-end gap-xs opacity-0 group-hover:opacity-100 transition-opacity">
        <button data-action="memberRole" data-id="${esc(m.id)}" data-to="${toggleTo}" title="Make ${toggleTo}" class="p-1 rounded-DEFAULT text-on-surface-variant hover:text-primary hover:bg-surface-container-highest"><span class="material-symbols-outlined text-[20px]">${toggleTo === "admin" ? "shield_person" : "school"}</span></button>
        <button data-action="memberRemove" data-id="${esc(m.id)}" data-name="${esc(m.full_name || m.email)}" title="Remove" class="p-1 rounded-DEFAULT text-on-surface-variant hover:text-error hover:bg-surface-container-highest"><span class="material-symbols-outlined text-[20px]">person_remove</span></button></div>`}</td></tr>`;
  }
  function render() {
    const tb = $("members-tbody"); if (!tb) return;
    const q = query.trim().toLowerCase();
    const rows = members.filter((m) => !q || (m.full_name || "").toLowerCase().includes(q) || (m.email || "").toLowerCase().includes(q));
    tb.innerHTML = rows.length ? rows.map(row).join("") : '<tr><td colspan="5" class="py-lg px-sm text-center text-on-surface-variant text-body-sm">No members match.</td></tr>';
    const c = $("members-count"); if (c) c.textContent = members.length;
  }
  async function load() {
    try { const r = await authFetch("/api/v1/org/members"); if (!r.ok) return; const d = await r.json().catch(() => ({})); members = Array.isArray(d.members) ? d.members : []; render(); } catch (_) {}
  }

  const search = $("members-search");
  if (search) search.addEventListener("input", (e) => { query = e.target.value || ""; render(); });

  // invite modal
  const INV = '<div id="invModal" class="fixed inset-0 z-[100] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface-container border border-outline-variant w-full max-w-md rounded-2xl p-xl shadow-2xl">' +
    '<div class="flex items-center justify-between mb-lg"><h2 class="font-bold text-lg">Invite Member</h2><button data-action="invClose" class="text-on-surface-variant hover:text-on-surface"><span class="material-symbols-outlined">close</span></button></div>' +
    '<label class="block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-1">Email</label>' +
    '<input id="inv-email" type="email" placeholder="teacher@university.edu" class="w-full bg-surface-container-low border border-outline-variant rounded-lg px-4 py-3 mb-md focus:border-primary"/>' +
    // Invites always join as a teacher (the backend org_invites flow has no role
    // field). To make someone an admin, promote them from the members list after
    // they join (the role toggle = PATCH /org/members/{id}/role). A role picker
    // here would be silently dropped by the API, so we don't show one.
    '<p class="text-body-sm text-on-surface-variant mb-md">They\'ll join as a <strong>teacher</strong>. You can promote them to admin from the members list once they accept.</p>' +
    '<p id="inv-err" class="text-error text-body-sm mt-2 hidden"></p>' +
    '<div class="flex justify-end gap-md mt-xl"><button data-action="invClose" class="px-lg py-md border border-outline-variant rounded-lg font-bold text-body-sm hover:bg-surface-container-high">Cancel</button>' +
    '<button id="inv-go" data-action="invSend" class="px-lg py-md bg-primary text-on-primary rounded-lg font-bold text-body-sm hover:opacity-90">Send Invite</button></div></div></div>';
  function invEnsure() { if (!$("invModal")) { const h = document.createElement("div"); h.innerHTML = INV; document.body.appendChild(h.firstChild); } }
  onAction("inviteMember", () => { invEnsure(); const m = $("invModal"); m.classList.remove("hidden"); m.classList.add("flex"); const e = $("inv-email"); if (e) { e.value = ""; e.focus(); } $("inv-err").classList.add("hidden"); });
  onAction("invClose", () => { const m = $("invModal"); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } });
  onAction("invSend", async (btn) => {
    const email = ($("inv-email") || {}).value || "";
    if (!email.includes("@")) { $("inv-err").textContent = "Enter a valid email."; $("inv-err").classList.remove("hidden"); return; }
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await authFetch("/api/v1/org/invite", { method: "POST", body: JSON.stringify({ email: email.trim() }) });
      if (r.ok) { const m = $("invModal"); m.classList.add("hidden"); m.classList.remove("flex"); load(); }
      else { const d = await r.json().catch(() => ({})); $("inv-err").textContent = d.detail || ("Failed (HTTP " + r.status + ")"); $("inv-err").classList.remove("hidden"); }
    } catch (_) { $("inv-err").textContent = "Invite failed."; $("inv-err").classList.remove("hidden"); }
    finally { btn.disabled = false; btn.textContent = "Send Invite"; }
  });

  onAction("memberRole", async (el) => {
    const id = el.getAttribute("data-id"), to = el.getAttribute("data-to");
    if (!window.confirm(`Change this member's role to ${to}?`)) return;
    try { const r = await authFetch(`/api/v1/org/members/${encodeURIComponent(id)}/role`, { method: "PATCH", body: JSON.stringify({ role: to }) }); if (r.ok) load(); else { const d = await r.json().catch(() => ({})); alert("Failed: " + (d.detail || r.status)); } } catch (_) { alert("Failed."); }
  });
  onAction("memberRemove", async (el) => {
    const id = el.getAttribute("data-id");
    if (!window.confirm(`Remove ${el.getAttribute("data-name") || "this member"} from the organization?`)) return;
    try { const r = await authFetch(`/api/v1/org/members/${encodeURIComponent(id)}`, { method: "DELETE" }); if (r.ok) load(); else { const d = await r.json().catch(() => ({})); alert("Failed: " + (d.detail || r.status)); } } catch (_) { alert("Failed."); }
  });

  load();
})();
