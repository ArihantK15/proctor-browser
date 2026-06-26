// Student dashboard — greeting + stats + upcoming/active exams + past results with
// scorecard download. Student-scoped endpoints:
//   GET  /api/v1/student/auth/me          {id,email,full_name}
//   GET  /api/student/exams               {exams:[…]}
//   GET  /api/student/history             {history:[{session_key,exam_title,score,total,percentage,submitted_at,pass_mark?,passed?}]}
//   GET  /api/v1/student/scorecard/{sid}  (PDF — own sessions only)
//   POST /api/v1/student/auth/logout
(function () {
  const api = window.StudentAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtDay = (v) => { if (!v) return "—"; try { return new Date(v).toLocaleDateString([], { month: "short", day: "2-digit", year: "numeric" }); } catch (_) { return String(v); } };
  const fmtWin = (a, b) => { const f = (v) => { try { return new Date(v).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } }; return a ? (b ? `${f(a)} – ${new Date(b).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : f(a)) : "Not scheduled"; };
  async function getJSON(u) { try { const r = await authFetch(u); return r.ok ? await r.json().catch(() => null) : null; } catch (_) { return null; } }

  // ---- greeting ----
  async function loadMe() {
    const d = await getJSON("/api/v1/student/auth/me"); if (!d) return;
    const name = String(d.full_name || d.email || "").trim().split(/\s+/)[0] || "there";
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    if ($("st-greet")) $("st-greet").textContent = `${part}, ${name}`;
    if ($("st-sub")) $("st-sub").textContent = d.email || "";
    if ($("st-name")) $("st-name").textContent = d.full_name || d.email || "Student";
    if ($("st-inst")) $("st-inst").textContent = d.email || "";
  }

  // ---- exam status ----
  function statusOf(ex) {
    const raw = String(ex.status || ex.state || "").toLowerCase();
    if (raw.includes("live") || raw.includes("active") || ex.can_join === true) return "live";
    if (raw.includes("complete") || raw.includes("submitted")) return "completed";
    const now = Date.now();
    const s = ex.starts_at ? new Date(ex.starts_at).getTime() : null;
    const e = ex.ends_at ? new Date(ex.ends_at).getTime() : null;
    if (s != null && now >= s && (e == null || now <= e)) return "live";
    if (e != null && now > e) return "completed";
    return "scheduled";
  }

  function examCard(ex) {
    const title = ex.exam_title || ex.title || "Exam";
    const subject = ex.subject || ex.section || ex.exam_id || "";
    const st = statusOf(ex);
    const eid = esc(ex.exam_id || "");
    if (st === "live") {
      return `<div class="bg-surface-container border border-primary rounded-xl p-lg flex flex-col relative overflow-hidden shadow-[0px_10px_30px_rgba(0,0,0,0.4)] ring-1 ring-inset ring-primary/20">
        <div class="absolute top-0 right-0 p-4"><span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-secondary-container/20 text-secondary border border-secondary-container font-label-caps text-label-caps uppercase"><span class="w-2 h-2 rounded-full bg-secondary animate-pulse"></span> LIVE NOW</span></div>
        <div class="mb-6 z-10 pr-24"><h3 class="font-headline-md text-headline-md font-bold text-on-surface mb-1">${esc(title)}</h3><p class="font-body-base text-body-base text-on-surface-variant">${esc(subject)}</p></div>
        <div class="flex items-center gap-2 mb-8 text-on-surface-variant z-10 bg-surface-container-high w-max px-3 py-2 rounded-lg border border-outline-variant"><span class="material-symbols-outlined text-sm">calendar_month</span><span class="font-data-mono text-data-mono">${esc(fmtWin(ex.starts_at, ex.ends_at))}</span></div>
        <div class="mt-auto pt-4 border-t border-outline-variant z-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <span class="font-body-sm text-body-sm text-on-surface-variant flex items-center gap-2"><span class="material-symbols-outlined text-[16px]">info</span> Opens in the Procta desktop app</span>
          <button data-action="stJoin" data-eid="${eid}" class="w-full sm:w-auto px-6 py-3 bg-inverse-primary text-white font-body-base text-body-base font-bold rounded-lg hover:bg-primary transition-colors hover:text-on-primary shadow-[0px_0px_15px_rgba(99,102,241,0.3)]">Join Exam</button></div></div>`;
    }
    const chip = st === "completed"
      ? '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-variant text-on-surface-variant border border-outline-variant font-label-caps text-label-caps uppercase"><span class="material-symbols-outlined text-[14px]">check</span> COMPLETED</span>'
      : '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-variant text-on-surface-variant border border-outline-variant font-label-caps text-label-caps uppercase"><span class="material-symbols-outlined text-[14px]">schedule</span> SCHEDULED</span>';
    return `<div class="bg-surface-container border border-outline-variant rounded-xl p-lg flex flex-col relative shadow-[0px_10px_30px_rgba(0,0,0,0.4)] opacity-90 hover:opacity-100 transition-opacity">
      <div class="absolute top-0 right-0 p-4">${chip}</div>
      <div class="mb-6 pr-32"><h3 class="font-headline-md text-headline-md font-bold text-on-surface mb-1">${esc(title)}</h3><p class="font-body-base text-body-base text-on-surface-variant">${esc(subject)}</p></div>
      <div class="flex items-center gap-2 mb-8 text-on-surface-variant bg-surface-container-high w-max px-3 py-2 rounded-lg border border-outline-variant"><span class="material-symbols-outlined text-sm">calendar_month</span><span class="font-data-mono text-data-mono">${esc(fmtWin(ex.starts_at, ex.ends_at))}</span></div>
      <div class="mt-auto pt-4 border-t border-outline-variant flex items-center justify-between"><span class="font-body-sm text-body-sm text-on-surface-variant">${st === "completed" ? "Result available below" : "Available at start time"}</span></div></div>`;
  }

  let _countdownTimer = null;
  function startCountdown(nextStart) {
    if (_countdownTimer) clearInterval(_countdownTimer);
    const el = $("st-countdown"); if (!el) return;
    if (!nextStart) { el.textContent = "—"; return; }
    const target = new Date(nextStart).getTime();
    const tick = () => {
      let d = Math.floor((target - Date.now()) / 1000);
      if (d <= 0) { el.textContent = "Starting…"; clearInterval(_countdownTimer); return; }
      const hh = String(Math.floor(d / 3600)).padStart(2, "0"); d %= 3600;
      el.textContent = `${hh}:${String(Math.floor(d / 60)).padStart(2, "0")}:${String(d % 60).padStart(2, "0")}`;
    };
    tick(); _countdownTimer = setInterval(tick, 1000);
  }

  async function loadExams() {
    const d = await getJSON("/api/student/exams");
    const list = d && Array.isArray(d.exams) ? d.exams : [];
    const box = $("st-upcoming"); if (!box) return;
    const upcoming = list.filter((ex) => statusOf(ex) !== "completed");
    box.innerHTML = upcoming.length ? upcoming.map(examCard).join("")
      : '<div class="bg-surface-container border border-outline-variant rounded-xl p-lg text-on-surface-variant text-body-base">No upcoming or active exams. You\'re all caught up.</div>';
    // soonest future start → countdown
    const future = list.map((ex) => ex.starts_at ? new Date(ex.starts_at).getTime() : null).filter((t) => t && t > Date.now()).sort((a, b) => a - b);
    startCountdown(future.length ? future[0] : null);
  }

  async function loadHistory() {
    const d = await getJSON("/api/student/history");
    const hist = d && Array.isArray(d.history) ? d.history : [];
    const tb = $("st-results");
    if (tb) {
      tb.innerHTML = hist.length ? hist.map((h) => {
        const pm = h.pass_mark != null ? h.pass_mark : 40;
        const passed = h.passed != null ? !!h.passed : (Number(h.percentage || 0) >= pm);
        const pct = Math.round(Number(h.percentage || 0));
        return `<tr class="hover:bg-surface-variant/30 transition-colors group">
          <td class="py-md px-md font-medium text-on-surface">${esc(h.exam_title || "Exam")}</td>
          <td class="py-md px-md font-data-mono text-data-mono text-on-surface-variant">${esc(fmtDay(h.submitted_at))}</td>
          <td class="py-md px-md"><div class="flex items-baseline gap-2"><span class="font-data-mono text-data-mono font-bold text-on-surface">${esc(h.score)}/${esc(h.total)}</span><span class="font-data-mono text-data-mono text-sm text-primary">${pct}%</span></div></td>
          <td class="py-md px-md"><span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ${passed ? "bg-secondary/10 text-secondary border-secondary/20" : "bg-error/10 text-error border-error/20"} border font-label-caps text-label-caps uppercase">${passed ? "PASS" : "FAIL"}</span></td>
          <td class="py-md px-md text-right"><button data-action="stScorecard" data-sid="${esc(h.session_key || "")}" title="Download scorecard" class="p-1 rounded text-on-surface-variant hover:text-primary hover:bg-surface-variant transition-colors group-hover:opacity-100 opacity-60"><span class="material-symbols-outlined">download</span></button></td></tr>`;
      }).join("") : '<tr><td colspan="5" class="py-lg px-md text-center text-on-surface-variant">No past results yet.</td></tr>';
    }
    if ($("st-taken")) $("st-taken").textContent = hist.length;
    if ($("st-avg")) { const ps = hist.map((h) => Number(h.percentage || 0)); $("st-avg").textContent = ps.length ? Math.round(ps.reduce((a, b) => a + b, 0) / ps.length) + "%" : "—"; }
  }

  onAction("stLogout", async () => { try { await authFetch("/api/v1/student/auth/logout", { method: "POST" }); } catch (_) {} window.location.href = "/login?role=student"; });
  onAction("stJoin", () => { alert("Open the Procta desktop app on your computer to start this proctored exam."); });
  onAction("stScorecard", async (el) => {
    const sid = el.getAttribute("data-sid"); if (!sid) return;
    try {
      const r = await authFetch(`/api/v1/student/scorecard/${encodeURIComponent(sid)}`);
      if (!r.ok) { alert("Scorecard not available (HTTP " + r.status + ")."); return; }
      const blob = await r.blob(); const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = `scorecard_${sid.split("_")[0]}.pdf`;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (_) { alert("Download failed."); }
  });

  loadMe(); loadExams(); loadHistory();
})();
