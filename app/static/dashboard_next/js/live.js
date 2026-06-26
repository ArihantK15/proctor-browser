// Live Monitor — vanilla, CSP-safe. Wires /api/v1/admin/live-monitor into the
// Stitch grid. Drawer via delegated data-action (was inline onclick).
// TODO (next increment): SSE /api/v1/sse/sessions WITH the _sseExamId exam-switch
// reconnect guard (parity must-fix), Peek -> /admin/sessions/<id>/room-cam/*,
// violations feed + Historical Log, filter tabs + real "Sort by risk".
(function () {
  const { authFetch, onAction } = window.ProctaAPI || {};
  if (!authFetch) return;

  // --- Evidence drawer (replaces inline toggleDrawer) ---
  function toggleDrawer() {
    const d = document.getElementById("evidenceDrawer");
    if (!d) return;
    const closed = d.classList.contains("translate-x-full");
    d.classList.toggle("translate-x-full", !closed);
    d.classList.toggle("invisible", !closed);
    d.classList.toggle("translate-x-0", closed);
    d.classList.toggle("visible", closed);
  }
  onAction("peekDrawer", toggleDrawer);

  // --- Live session grid ---
  const grid = document.querySelector(".grid.content-start");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const tone = (r) => (r >= 70 ? "error" : r >= 30 ? "tertiary" : "secondary");
  const glow = (t) => ({ error: "risk-red-glow", tertiary: "risk-amber-glow", secondary: "risk-emerald-glow" }[t]);
  const hhmm = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } };

  function card(s) {
    const r = s.risk_score != null ? Math.round(s.risk_score) : 0;
    const t = tone(r);
    const el = document.createElement("div");
    el.className = `group bg-surface-container-low border border-[#30363d] rounded-xl p-3 hover:border-${t} transition-all cursor-pointer flex flex-col gap-2 ${glow(t)}`;
    el.setAttribute("data-action", "peekDrawer");
    el.innerHTML = `
      <div class="flex items-start gap-3">
        <div class="w-12 h-12 rounded-lg bg-surface-container-high overflow-hidden border border-[#30363d] flex-shrink-0 flex items-center justify-center">
          <span class="material-symbols-outlined text-outline" data-icon="person">person</span>
        </div>
        <div class="flex-1 min-w-0">
          <h3 class="font-bold text-body-sm truncate">${esc(s.full_name || "Student")}</h3>
          <p class="font-data-mono text-outline text-[11px]">${esc(s.roll_number || "")}</p>
          <div class="flex items-center gap-1 mt-1 text-${t}">
            <span class="text-[10px] font-black bg-${t}/10 px-1.5 py-0.5 rounded border border-${t}/20">${r}% RISK</span>
          </div>
        </div>
      </div>
      <div class="space-y-1">
        <div class="flex justify-between text-[10px] text-on-surface-variant font-medium">
          <span class="truncate">${esc(s.latest_violation || "No flags")}</span>
          <span class="text-${t} font-data-mono">${hhmm(s.started_at)}</span>
        </div>
        <div class="w-full h-1 bg-surface rounded-full overflow-hidden">
          <div class="h-full bg-${t}" style="width: ${r}%"></div>
        </div>
      </div>
      <div class="flex items-center justify-between mt-auto pt-2 border-t border-[#30363d]/50">
        <div class="flex gap-1 ${s.latest_violation ? "text-" + t : "opacity-20"}">
          <span class="material-symbols-outlined text-[14px]" data-icon="${s.latest_violation ? "warning" : "check_circle"}">${s.latest_violation ? "warning" : "check_circle"}</span>
        </div>
        <button class="text-[10px] font-bold px-2.5 py-1 bg-primary text-on-primary rounded-lg flex items-center gap-1 hover:opacity-90" data-action="peekDrawer">
          <span class="material-symbols-outlined text-[12px]" data-icon="videocam">videocam</span> Peek
        </button>
      </div>`;
    return el;
  }

  async function load() {
    if (!grid) return;
    try {
      const res = await authFetch("/api/v1/admin/live-monitor");
      if (!res.ok) return;
      const d = await res.json().catch(() => ({}));
      const sessions = (Array.isArray(d.sessions) ? d.sessions : [])
        .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)); // Sort by risk
      grid.innerHTML = "";
      if (!sessions.length) {
        grid.innerHTML = '<div class="col-span-full p-md text-on-surface-variant text-body-sm">No exams in progress right now.</div>';
        return;
      }
      sessions.forEach((s) => grid.appendChild(card(s)));
    } catch (_) { /* leave last render on transient error */ }
  }

  load();
  setInterval(load, 10000); // interim live-refresh; SSE replaces this next increment
})();
