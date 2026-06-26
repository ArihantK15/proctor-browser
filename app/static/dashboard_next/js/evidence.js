// Evidence Review = Appeals Review Queue (owner decision: option A). Privacy-correct:
// frames come ONLY from appeal-attached evidence (the backend gates per-flag frames
// behind appeals + the audit PDF — there is no general frame-scrub API).
//   GET  /api/v1/admin/appeals                  -> {appeals:[{id,roll_number,session_key,
//          exam_id,status,description,appeal_type,created_at,evidence_primary,evidence_context[]}]}
//   POST /api/v1/admin/appeals/{id}/resolve     -> {status:accepted|rejected, teacher_note}
//   GET  /api/v1/export-pdf/{session_key}       -> Audit Report PDF
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hhmm = (v) => { try { return new Date(v).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } };

  let appeals = [], sel = null;
  const STATUS = {
    pending: ["tertiary", "PENDING"], accepted: ["secondary", "ACCEPTED"], rejected: ["error", "REJECTED"],
  };
  const rank = (a) => (a.status === "pending" ? 0 : 1);

  function renderList() {
    const box = $("appeals-list"); if (!box) return;
    if (!appeals.length) { box.innerHTML = '<div class="p-md text-on-surface-variant text-body-sm">No appeals to review.</div>'; return; }
    box.innerHTML = appeals.map((a, i) => {
      const [tone, label] = STATUS[a.status] || ["outline", (a.status || "").toUpperCase()];
      const on = sel === i;
      return `<div class="p-md cursor-pointer transition-all border-b border-outline-variant ${on ? "bg-primary-container/10 border-l-4 border-l-primary" : "hover:bg-surface-container-high"}" data-action="selectAppeal" data-idx="${i}">
        <div class="flex justify-between mb-xs"><span class="font-data-mono ${on ? "text-primary" : "text-on-surface-variant"} text-[11px]">${esc(a.roll_number || a.session_key || "—")}</span>
        <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-${tone}"></span><span class="font-label-caps text-${tone} text-[10px]">${label}</span></span></div>
        <p class="font-body-sm text-on-surface ${on ? "font-semibold" : ""} mb-1 truncate">${esc(a.appeal_type || "Appeal")}${a.evidence_primary ? '' : '  '}</p>
        <p class="text-[11px] text-on-surface-variant truncate">${esc((a.description || "").slice(0, 80))}</p>
        <span class="font-data-mono text-[10px] text-outline">${esc(hhmm(a.created_at))}</span></div>`;
    }).join("");
  }

  function frame(url, badge, primary) {
    return `<div class="relative aspect-video rounded-xl border ${primary ? "border-2 border-primary shadow-lg shadow-primary/20" : "border-outline-variant"} overflow-hidden bg-surface-container-lowest">
      <div class="w-full h-full bg-cover bg-center" style="background-image:url('${esc(url)}')"></div>
      <div class="absolute top-2 left-2 ${primary ? "bg-primary font-bold" : "bg-black/40"} px-xs rounded text-[10px] font-data-mono">${esc(badge)}</div></div>`;
  }

  function renderDetail() {
    const a = sel != null ? appeals[sel] : null;
    const setText = (id, v) => { const e = $(id); if (e) e.textContent = v; };
    const prim = $("ev-primary"), ctx = $("ev-context"), risk = $("ev-risk");
    if (!a) { setText("ev-student", "Select an appeal"); return; }
    setText("ev-student", a.roll_number || a.session_key || "Appeal");
    setText("ev-exam", a.exam_id || "—");
    const [tone, label] = STATUS[a.status] || ["outline", a.status];
    if (risk) { risk.textContent = label; risk.className = `text-[28px] font-bold text-${tone} leading-none`; }
    setText("ev-appeal-msg", a.description || "No message provided.");

    const primary = a.evidence_primary || null;
    const context = Array.isArray(a.evidence_context) ? a.evidence_context : [];
    if (prim) prim.style.backgroundImage = primary ? `url('${primary}')` : "";
    if (ctx) {
      if (!primary && !context.length) {
        ctx.innerHTML = '<div class="col-span-3 p-md text-center text-on-surface-variant text-body-sm border border-dashed border-outline-variant rounded-xl">No frame evidence attached to this appeal (session-level appeal). Use the Audit Report PDF for the full log.</div>';
      } else {
        const items = [];
        if (context[0]) items.push(frame(context[0], "-2.0s", false));
        items.push(frame(primary || context[1] || context[0], "EVENT", true));
        if (context[1]) items.push(frame(context[1], "+2.0s", false));
        ctx.innerHTML = items.join("");
      }
    }
    renderList();
  }

  async function load() {
    try {
      const r = await authFetch("/api/v1/admin/appeals");
      if (!r.ok) return;
      const d = await r.json().catch(() => ({}));
      appeals = Array.isArray(d.appeals) ? d.appeals.slice() : [];
      appeals.sort((a, b) => (rank(a) - rank(b)) || (new Date(b.created_at || 0) - new Date(a.created_at || 0)));
      const pend = appeals.filter((a) => a.status === "pending").length;
      const c = $("appeals-count"); if (c) c.textContent = `${pend} pending`;
      sel = appeals.length ? 0 : null;
      renderList(); renderDetail();
    } catch (_) {}
  }

  onAction("selectAppeal", (el) => { sel = parseInt(el.getAttribute("data-idx"), 10); renderDetail(); });

  async function resolve(status) {
    const a = sel != null ? appeals[sel] : null; if (!a) return;
    if (!window.confirm(`${status === "accepted" ? "Accept" : "Reject"} this appeal${status === "accepted" ? " (this dismisses the disputed flag and lowers the risk score)" : ""}?`)) return;
    const note = ($("ev-note") && $("ev-note").value) || "";
    try {
      const r = await authFetch(`/api/v1/admin/appeals/${encodeURIComponent(a.id)}/resolve`, { method: "POST", body: JSON.stringify({ status, teacher_note: note }) });
      if (r.ok) { const nt = $("ev-note"); if (nt) nt.value = ""; load(); }
      else { const d = await r.json().catch(() => ({})); alert("Failed: " + (d.detail || ("HTTP " + r.status))); }
    } catch (_) { alert("Action failed."); }
  }
  onAction("acceptAppeal", () => resolve("accepted"));
  onAction("rejectAppeal", () => resolve("rejected"));

  onAction("auditPdf", async () => {
    const a = sel != null ? appeals[sel] : null;
    if (!a || !a.session_key) { alert("Select an appeal first."); return; }
    try {
      const r = await authFetch(`/api/v1/export-pdf/${encodeURIComponent(a.session_key)}`);
      if (!r.ok) { alert("Audit report unavailable (HTTP " + r.status + ")."); return; }
      const blob = await r.blob(); const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `audit-report_${String(a.session_key).split("_")[0]}.pdf`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (_) { alert("Audit report download failed."); }
  });

  load();
})();
