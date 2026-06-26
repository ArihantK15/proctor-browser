// Overview (teacher home) — vanilla, CSP-safe. Populates the KPI cards, Live Now
// strip, Upcoming & Recent Exams table, and Recent Activity from real endpoints:
//   GET /api/v1/auth/me                 (greeting name)
//   GET /api/v1/admin/registered-count  (Total Students)
//   GET /api/v1/admin/exams             (exams-this-month, exams table, Live Now)
//   GET /api/v1/admin/analytics         (Avg Class Score)
//   GET /api/v1/admin/live-monitor      (Open Flags + Recent Activity)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch } = api;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const setText = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  const fmtDate = (v) => { if (!v) return "—"; try { return new Date(v).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }); } catch (_) { return "—"; } };
  const ago = (v) => { try { const d = (Date.now() - new Date(v)) / 1000; if (d < 60) return "just now"; if (d < 3600) return Math.floor(d / 60) + " mins ago"; if (d < 86400) return Math.floor(d / 3600) + " hours ago"; return Math.floor(d / 86400) + " days ago"; } catch (_) { return ""; } };
  const riskOf = (s) => (s && s.risk_score != null ? Math.round(s.risk_score) : 0);

  // Live | Scheduled | Completed | Draft, from starts_at/ends_at vs now.
  function examStatus(ex) {
    const now = Date.now();
    const s = ex.starts_at ? new Date(ex.starts_at).getTime() : null;
    const e = ex.ends_at ? new Date(ex.ends_at).getTime() : null;
    if (s == null) return ["Draft", "on-surface-variant"];
    if (now < s) return ["Scheduled", "primary"];
    if (e != null && now > e) return ["Completed", "on-surface-variant"];
    return ["Live", "secondary"];
  }

  async function getJSON(url) { try { const r = await authFetch(url); return r.ok ? await r.json().catch(() => null) : null; } catch (_) { return null; } }

  async function greeting() {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    const me = await getJSON("/api/v1/auth/me");
    const name = me && (me.full_name || me.email) ? (me.full_name || me.email) : null;
    setText("greet-name", name ? `${part}, ${name}` : part);
  }

  async function kpis(exams) {
    const rc = await getJSON("/api/v1/admin/registered-count");
    setText("kpi-students", rc && rc.count != null ? Number(rc.count).toLocaleString() : "—");

    // exams created this month
    const now = new Date(); const m = now.getMonth(), y = now.getFullYear();
    const thisMonth = exams.filter((ex) => { const d = ex.created_at ? new Date(ex.created_at) : null; return d && d.getMonth() === m && d.getFullYear() === y; }).length;
    setText("kpi-exams", String(thisMonth));

    const an = await getJSON("/api/v1/admin/analytics");
    const ov = an && an.exam_overview;
    setText("kpi-avgscore", ov && ov.count && ov.avg_percentage != null ? Math.round(ov.avg_percentage) + "%" : "—");

    const lm = await getJSON("/api/v1/admin/live-monitor");
    const sessions = lm ? (Array.isArray(lm.all_sessions) ? lm.all_sessions : (Array.isArray(lm.sessions) ? lm.sessions : [])) : [];
    setText("kpi-flags", String(sessions.filter((s) => riskOf(s) >= 70 || s.latest_violation).length));
    activity(sessions);
  }

  function liveNow(exams) {
    const strip = $("live-now-strip"); if (!strip) return;
    const live = exams.filter((ex) => examStatus(ex)[0] === "Live");
    if (!live.length) { strip.innerHTML = '<div class="min-w-[280px] glass-card p-md rounded-xl text-on-surface-variant text-body-sm flex items-center gap-2"><span class="material-symbols-outlined text-outline">bedtime</span> No exams are live right now.</div>'; return; }
    strip.innerHTML = live.map((ex) => `
      <div class="min-w-[280px] glass-card p-md rounded-xl flex flex-col gap-sm relative group cursor-pointer hover:bg-surface-container-high transition-all" data-action="openExam" data-eid="${esc(ex.exam_id)}">
        <div class="absolute top-3 right-3 flex items-center gap-xs">
          <span class="w-2 h-2 bg-secondary rounded-full relative"><span class="absolute inset-0 bg-secondary rounded-full pulse-green"></span></span>
          <span class="text-[10px] font-bold text-secondary uppercase tracking-tight">Active</span></div>
        <p class="text-on-surface-variant text-[11px] font-bold uppercase">Live exam</p>
        <h4 class="font-body-base font-bold truncate pr-16">${esc(ex.exam_title || "Exam")}</h4>
        <div class="flex items-center justify-between mt-sm">
          <p class="text-on-surface-variant text-body-sm flex items-center gap-1"><span class="material-symbols-outlined text-[16px]">groups</span> ${esc(ex.session_count || 0)} in session</p>
          <p class="font-data-mono text-data-mono text-on-surface-variant">${esc(ex.duration_minutes || 0)}m</p></div>
      </div>`).join("");
  }

  function examsTable(exams) {
    const tb = $("exams-tbody"); if (!tb) return;
    const rows = exams.slice(0, 6);
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="5" class="px-lg py-md text-on-surface-variant text-body-sm">No exams yet.</td></tr>'; return; }
    tb.innerHTML = rows.map((ex) => {
      const [label, tone] = examStatus(ex);
      return `<tr class="hover:bg-surface-container-high/50 transition-colors group">
        <td class="px-lg py-md font-bold text-on-surface">${esc(ex.exam_title || "Exam")}</td>
        <td class="px-lg py-md font-data-mono text-on-surface-variant">${esc(fmtDate(ex.starts_at))}</td>
        <td class="px-lg py-md text-on-surface-variant">${esc(ex.session_count || 0)}</td>
        <td class="px-lg py-md"><span class="px-sm py-1 rounded-full bg-${tone}/10 text-${tone} text-[10px] font-bold uppercase border border-${tone}/20">${esc(label)}</span></td>
        <td class="px-lg py-md text-right"><button class="text-primary font-bold text-body-sm group-hover:scale-105 transition-transform" data-action="openExam" data-eid="${esc(ex.exam_id)}">Open</button></td>
      </tr>`;
    }).join("");
  }

  function activity(sessions) {
    const feed = $("activity-feed"); if (!feed) return;
    const line = '<div class="absolute left-[39px] top-lg bottom-lg w-px bg-outline-variant opacity-30"></div>';
    const items = (sessions || []).filter((s) => s.latest_violation)
      .sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0)).slice(0, 8);
    if (!items.length) { feed.innerHTML = line + '<div class="text-on-surface-variant text-body-sm flex items-center gap-2"><span class="material-symbols-outlined text-secondary">check_circle</span> No recent flags — all clear.</div>'; return; }
    feed.innerHTML = line + items.map((s) => {
      const t = riskOf(s) >= 70 ? "error" : "tertiary";
      return `<div class="flex gap-lg relative">
        <div class="z-10 w-8 h-8 rounded-full bg-${t}/10 flex items-center justify-center border border-${t}/20"><span class="material-symbols-outlined text-[18px] text-${t}">warning</span></div>
        <div><p class="text-body-sm font-bold text-on-surface">${esc(s.latest_violation)}</p>
        <p class="text-[12px] text-on-surface-variant mt-0.5">${esc(s.full_name || "Student")} (${esc(s.roll_number || "")})</p>
        <p class="text-[10px] font-data-mono text-outline mt-xs uppercase">${esc(ago(s.started_at))}</p></div></div>`;
    }).join("");
  }

  api.onAction("openExam", () => { /* TODO: route to Exams detail once that section lands */ });

  async function load() {
    greeting();
    const ex = await getJSON("/api/v1/admin/exams");
    const exams = ex && Array.isArray(ex.exams) ? ex.exams : [];
    liveNow(exams); examsTable(exams); kpis(exams);
  }
  load();
})();
