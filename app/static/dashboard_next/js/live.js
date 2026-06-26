// Live Monitor — vanilla, CSP-safe. SSE-driven live feed with the _sseExamId
// exam-switch reconnect (parity must-fix) + poll fallback; real stats, filter
// tabs, violations feed, and Peek (room-cam). Wires:
//   POST /api/v1/sse/connect-token, EventSource /api/v1/sse/sessions
//   GET  /api/v1/admin/live-monitor (fallback / initial)
//   POST /api/v1/admin/sessions/<sid>/room-cam/{start,keepalive,stop}  (Peek)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { BASE, authFetch, onAction } = api;

  let currentExamId = (api.examId && api.examId()) || null;  // topbar exam selector
  let _sseExamId = null, _sse = null, _poll = null, _debounce = null;
  let allSessions = [], filter = "all";

  const $ = (s, r) => (r || document).querySelector(s);
  const grid = $(".grid.content-start");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const tone = (r) => (r >= 70 ? "error" : r >= 30 ? "tertiary" : "secondary");
  const glow = (t) => ({ error: "risk-red-glow", tertiary: "risk-amber-glow", secondary: "risk-emerald-glow" }[t]);
  const hhmm = (v) => { try { return new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } };
  const riskOf = (s) => (s.risk_score != null ? Math.round(s.risk_score) : 0);

  // ---- cards ----
  function card(s) {
    const r = riskOf(s), t = tone(r);
    const el = document.createElement("div");
    el.className = `group bg-surface-container-low border border-[#30363d] rounded-xl p-3 hover:border-${t} transition-all cursor-pointer flex flex-col gap-2 ${glow(t)}`;
    el.setAttribute("data-action", "peekDrawer");
    if (s.session_id) el.setAttribute("data-sid", s.session_id);
    el.dataset.name = s.full_name || "Student"; el.dataset.roll = s.roll_number || "";
    el.innerHTML = `
      <div class="flex items-start gap-3">
        <div class="w-12 h-12 rounded-lg bg-surface-container-high overflow-hidden border border-[#30363d] flex-shrink-0 flex items-center justify-center">
          <span class="material-symbols-outlined text-outline" data-icon="person">person</span></div>
        <div class="flex-1 min-w-0">
          <h3 class="font-bold text-body-sm truncate">${esc(s.full_name || "Student")}</h3>
          <p class="font-data-mono text-outline text-[11px]">${esc(s.roll_number || "")}</p>
          <div class="flex items-center gap-1 mt-1 text-${t}"><span class="text-[10px] font-black bg-${t}/10 px-1.5 py-0.5 rounded border border-${t}/20">${r}% RISK</span></div>
        </div></div>
      <div class="space-y-1">
        <div class="flex justify-between text-[10px] text-on-surface-variant font-medium">
          <span class="truncate">${esc(s.latest_violation || "No flags")}</span>
          <span class="text-${t} font-data-mono">${hhmm(s.started_at)}</span></div>
        <div class="w-full h-1 bg-surface rounded-full overflow-hidden"><div class="h-full bg-${t}" style="width: ${r}%"></div></div></div>
      <div class="flex items-center justify-between mt-auto pt-2 border-t border-[#30363d]/50">
        <div class="flex gap-1 ${s.latest_violation ? "text-" + t : "opacity-20"}"><span class="material-symbols-outlined text-[14px]" data-icon="${s.latest_violation ? "warning" : "check_circle"}">${s.latest_violation ? "warning" : "check_circle"}</span></div>
        <button class="text-[10px] font-bold px-2.5 py-1 bg-primary text-on-primary rounded-lg flex items-center gap-1 hover:opacity-90" data-action="peekDrawer" data-sid="${esc(s.session_id || "")}"><span class="material-symbols-outlined text-[12px]" data-icon="videocam">videocam</span> Peek</button>
      </div>`;
    return el;
  }

  function matchFilter(s) {
    if (filter === "all") return true;
    if (filter === "live") return s.status !== "completed";
    if (filter === "flagged") return riskOf(s) >= 70 || !!s.latest_violation;
    if (filter === "completed") return s.status === "completed";
    return true;
  }

  function render() {
    if (!grid) return;
    const rows = allSessions.filter(matchFilter).sort((a, b) => riskOf(b) - riskOf(a));
    grid.innerHTML = "";
    if (!rows.length) { grid.innerHTML = '<div class="col-span-full p-md text-on-surface-variant text-body-sm">No sessions match.</div>'; return; }
    rows.forEach((s) => grid.appendChild(card(s)));
  }

  function renderStats() {
    const live = allSessions.filter((s) => s.status !== "completed");
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    set("stat-active", live.length);
    const avg = live.length ? Math.round(live.reduce((a, s) => a + riskOf(s), 0) / live.length) : 0;
    set("stat-avgrisk", avg + "/100");
    set("stat-flagged", allSessions.filter((s) => riskOf(s) >= 70 || s.latest_violation).length);
  }

  function renderViolations() {
    const feed = document.getElementById("violations-feed"); if (!feed) return;
    const items = allSessions.filter((s) => s.latest_violation)
      .sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0)).slice(0, 30);
    if (!items.length) { feed.innerHTML = '<div class="p-3 text-on-surface-variant text-body-sm flex items-center gap-2"><span class="material-symbols-outlined text-secondary" data-icon="check_circle">check_circle</span> All clear</div>'; return; }
    feed.innerHTML = items.map((s) => {
      const t = tone(riskOf(s));
      return `<div class="p-3 bg-surface-container-high/30 border border-${t}/30 rounded-lg">
        <div class="flex justify-between items-start mb-1"><span class="font-data-mono text-outline text-[11px]">${hhmm(s.started_at)}</span><span class="w-2 h-2 rounded-full bg-${t}"></span></div>
        <p class="text-body-sm font-bold text-on-surface mb-0.5">${esc(s.latest_violation)}</p>
        <p class="text-[12px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-[14px]" data-icon="person">person</span> ${esc(s.full_name || "")} (${esc(s.roll_number || "")})</p></div>`;
    }).join("");
  }

  function apply(d) {
    allSessions = Array.isArray(d.all_sessions) ? d.all_sessions : (Array.isArray(d.sessions) ? d.sessions : []);
    render(); renderStats(); renderViolations();
  }

  // ---- data: SSE with exam-switch reconnect + poll fallback ----
  async function refreshLive() {
    const ex = currentExamId ? `?exam_id=${encodeURIComponent(currentExamId)}` : "";
    try { const r = await authFetch("/api/v1/admin/live-monitor" + ex); if (r.ok) apply(await r.json().catch(() => ({}))); } catch (_) {}
  }
  function debouncedRefresh() { clearTimeout(_debounce); _debounce = setTimeout(refreshLive, 400); }

  async function connectSSE() {
    if (_sse) { try { _sse.close(); } catch (_) {} _sse = null; }
    if (_poll) { clearInterval(_poll); _poll = null; }
    try {
      const ctr = await authFetch("/api/v1/sse/connect-token", { method: "POST" });
      if (!ctr.ok) throw new Error("connect-token");
      const { connect_token } = await ctr.json();
      _sseExamId = currentExamId;
      const ex = currentExamId ? `&exam_id=${encodeURIComponent(currentExamId)}` : "";
      _sse = new EventSource(`${BASE}/api/v1/sse/sessions?token=${encodeURIComponent(connect_token)}${ex}`);
      const onData = (e) => {
        if (_sseExamId !== currentExamId) { connectSSE(); return; } // stale-scope guard
        try { apply(JSON.parse(e.data)); } catch (_) {}
      };
      _sse.addEventListener("init", onData);
      _sse.addEventListener("refresh", onData);
      _sse.addEventListener("update", debouncedRefresh);
      _sse.onerror = () => { try { _sse.close(); } catch (_) {} _sse = null; _poll = setInterval(refreshLive, 5000); };
    } catch (_) {
      _poll = setInterval(refreshLive, 5000); // fallback: poll
    }
  }

  // ---- filter tabs ----
  onAction("filterLive", (el) => {
    filter = el.getAttribute("data-filter") || "all";
    document.querySelectorAll('[data-action="filterLive"]').forEach((b) => {
      const on = b === el;
      b.classList.toggle("bg-surface-container-highest", on);
      b.classList.toggle("text-on-surface", on); b.classList.toggle("font-bold", on);
      b.classList.toggle("text-on-surface-variant", !on); b.classList.toggle("font-medium", !on);
    });
    render();
  });

  // ---- Peek (evidence drawer + room-cam) ----
  let _camSid = null, _camPoll = null, _camKeep = null;
  async function stopCam() {
    if (_camPoll) { clearInterval(_camPoll); _camPoll = null; }
    if (_camKeep) { clearInterval(_camKeep); _camKeep = null; }
    if (_camSid) { try { await authFetch(`/api/v1/admin/sessions/${encodeURIComponent(_camSid)}/room-cam/stop`, { method: "POST" }); } catch (_) {} _camSid = null; }
  }
  async function startCam(sid) {
    await stopCam(); if (!sid) return;
    _camSid = sid;
    const img = document.querySelector("#evidenceDrawer .aspect-video img");
    try { await authFetch(`/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/start`, { method: "POST" }); } catch (_) {}
    const tick = () => { if (img) img.src = `${BASE}/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/frame?t=${Date.now()}`; };
    tick(); _camPoll = setInterval(tick, 1500);
    _camKeep = setInterval(() => { authFetch(`/api/v1/admin/sessions/${encodeURIComponent(sid)}/room-cam/keepalive`, { method: "POST" }).catch(() => {}); }, 15000);
  }
  function openDrawer(el) {
    const d = document.getElementById("evidenceDrawer"); if (!d) return;
    const card = el.closest("[data-sid]") || el;
    const sid = card.getAttribute("data-sid");
    const title = $("#evidenceDrawer p.text-outline");
    if (title && card.dataset && card.dataset.name) title.textContent = `${card.dataset.name} · ${card.dataset.roll || ""}`;
    d.classList.remove("translate-x-full", "invisible"); d.classList.add("translate-x-0", "visible");
    startCam(sid);
  }
  function closeDrawer() {
    const d = document.getElementById("evidenceDrawer"); if (!d) return;
    d.classList.add("translate-x-full", "invisible"); d.classList.remove("translate-x-0", "visible");
    stopCam();
  }
  onAction("peekDrawer", (el) => {
    const d = document.getElementById("evidenceDrawer");
    if (d && d.classList.contains("translate-x-0")) closeDrawer(); else openDrawer(el);
  });
  onAction("historicalLog", () => { /* TODO: open full violations history view */ });

  // topbar exam selector → re-scope the live feed (reuses the _sseExamId reconnect guard)
  if (api.onExamChange) api.onExamChange((id) => { currentExamId = id || null; refreshLive(); connectSSE(); });

  refreshLive();   // initial paint
  connectSSE();    // then live
})();
