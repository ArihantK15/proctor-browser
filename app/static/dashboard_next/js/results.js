// Results & Analytics — vanilla, CSP-safe. Analytics overview + score-distribution
// histogram + per-student scores table with filter tabs + pass-threshold slider.
// Exam-scoped once the selector is wired; for now aggregates across all exams.
//   GET /api/v1/admin/analytics   (exam_overview, score_distribution)
//   GET /api/v1/results           (per-session scores)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let results = [], dist = [], filter = "all", threshold = 60;
  // The table loads one page client-side (filter tabs + threshold recolor operate
  // on the loaded set). PAGE_SIZE is generous enough for a single exam; the "all
  // exams" view can exceed it, so we read `total` and surface an honest notice
  // rather than silently dropping rows. The histogram/stats come from server-side
  // /analytics, so the summary stays complete regardless of table paging.
  const PAGE_SIZE = 500;
  let total = 0;

  const initials = (name) => { const p = String(name || "").trim().split(/\s+/); return (((p[0] || "")[0] || "") + ((p[1] || "")[0] || "") || "?").toUpperCase(); };
  const pct = (r) => (r.percentage != null ? Math.round(r.percentage) : 0);
  const riskOf = (r) => (r.risk_score != null ? Math.round(r.risk_score) : null);
  const riskTone = (v) => (v == null ? "outline" : v >= 70 ? "error" : v >= 30 ? "tertiary" : "secondary");

  async function getJSON(url) { try { const r = await authFetch(url); return r.ok ? await r.json().catch(() => null) : null; } catch (_) { return null; } }

  function histogram() {
    const box = $("score-histogram"); if (!box) return;
    if (!dist.length) { box.innerHTML = '<div class="w-full text-center text-on-surface-variant text-body-sm self-center">No data yet.</div>'; return; }
    const max = Math.max(1, ...dist.map((b) => b.count || 0));
    box.innerHTML = dist.map((b, i) => {
      const h = Math.round(((b.count || 0) / max) * 100);
      const pass = i * 10 >= threshold; // bucket lower-bound vs threshold
      const tone = pass ? "primary" : "error";
      const op = pass ? "" : "/20";
      return `<div class="w-full bg-${tone}${op} rounded-t-sm relative group" style="height:${Math.max(h, 2)}%">
        <div class="absolute -top-6 left-1/2 -translate-x-1/2 bg-surface-container-highest px-xs py-[2px] rounded text-[10px] opacity-0 group-hover:opacity-100 transition-opacity">${b.count || 0}</div></div>`;
    }).join("");
  }

  function stats(overview) {
    if (overview && overview.count) {
      $("stat-passrate") && ($("stat-passrate").textContent = overview.pass_rate != null ? Math.round(overview.pass_rate) + "%" : "—");
      $("stat-completion") && ($("stat-completion").textContent = overview.count);
    } else {
      ["stat-passrate", "stat-completion"].forEach((id) => $(id) && ($(id).textContent = "—"));
    }
    const risks = results.map(riskOf).filter((v) => v != null);
    $("stat-avgrisk") && ($("stat-avgrisk").textContent = risks.length ? Math.round(risks.reduce((a, b) => a + b, 0) / risks.length) : "—");
    $("stat-flags") && ($("stat-flags").textContent = String(results.filter((r) => (r.violation_count || 0) > 0 || (riskOf(r) || 0) >= 70).length));
  }

  function row(r, i) {
    const p = pct(r), pass = p >= threshold, rv = riskOf(r), rt = riskTone(rv);
    const flagged = (r.violation_count || 0) > 0 || (rv || 0) >= 70;
    return `<tr class="hover:bg-surface-container-low transition-colors group ${flagged ? "bg-error/5" : ""}">
      <td class="px-lg py-md"><div class="flex items-center gap-md">
        <div class="w-9 h-9 rounded-full bg-primary-container/20 flex items-center justify-center font-bold text-primary">${esc(initials(r.full_name))}</div>
        <div><p class="font-body-sm font-bold text-on-surface">${esc(r.full_name || "Unnamed")}</p><p class="font-data-mono text-[11px] text-on-surface-variant">#${esc(r.roll_number || "")}</p></div></div></td>
      <td class="px-lg py-md"><div class="flex flex-col"><span class="font-data-mono text-body-base font-bold text-on-surface">${esc(r.score)}/${esc(r.total)}</span><span class="text-[11px] ${pass ? "text-secondary" : "text-error"} font-bold">${p}%</span></div></td>
      <td class="px-lg py-md text-center"><span class="px-sm py-1 rounded-full ${pass ? "bg-secondary-container/20 text-secondary border-secondary/30" : "bg-error-container/20 text-error border-error/30"} text-[11px] font-bold border">${pass ? "PASS" : "FAIL"}</span></td>
      <td class="px-lg py-md"><div class="flex flex-col items-center gap-1"><span class="font-data-mono text-${rt} font-bold">${rv == null ? "—" : String(rv).padStart(2, "0")}</span>
        <div class="w-16 h-1 bg-surface-container-highest rounded-full overflow-hidden"><div class="bg-${rt} h-full" style="width:${rv || 0}%"></div></div></div></td>
      <td class="px-lg py-md text-center"><span class="text-on-surface-variant text-body-sm">${r.violation_count ? esc(r.violation_count) : "—"}</span></td>
      <td class="px-lg py-md text-right"><button class="text-primary font-bold text-body-sm hover:underline active:scale-95 transition-transform" data-action="viewScorecard" data-sid="${esc(r.session_id || "")}">View Scorecard</button></td>
    </tr>`;
  }

  function render() {
    const tb = $("results-tbody"); if (!tb) return;
    const rows = results.filter((r) => {
      if (filter === "flagged") return (r.violation_count || 0) > 0 || (riskOf(r) || 0) >= 70;
      if (filter === "failed") return pct(r) < threshold;
      return true;
    });
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="6" class="px-lg py-xl text-center text-on-surface-variant text-body-sm">No results match.</td></tr>'; return; }
    let html = rows.map((r, i) => row(r, i)).join("");
    // Honest truncation notice — never silently hide rows past the page.
    if (total > results.length) {
      html += `<tr><td colspan="6" class="px-lg py-md text-center text-on-surface-variant text-body-sm bg-surface-container-low">Showing the first ${results.length} of ${total} results. Select a specific exam from the top bar to see its complete results.</td></tr>`;
    }
    tb.innerHTML = html;
  }

  async function load() {
    const ex = api.examId ? api.examId() : "";
    const q = ex ? `?exam_id=${encodeURIComponent(ex)}` : "";
    const an = await getJSON("/api/v1/admin/analytics" + q);
    dist = an && Array.isArray(an.score_distribution) ? an.score_distribution : [];
    const res = await getJSON(`/api/v1/results?page=1&page_size=${PAGE_SIZE}` + (ex ? `&exam_id=${encodeURIComponent(ex)}` : ""));
    results = res && Array.isArray(res.results) ? res.results : [];
    total = res && typeof res.total === "number" ? res.total : results.length;
    histogram(); stats(an && an.exam_overview); render();
  }
  if (api.onExamChange) api.onExamChange(() => load());

  // filter tabs
  onAction("filterResults", (el) => {
    filter = el.getAttribute("data-filter") || "all";
    document.querySelectorAll('[data-action="filterResults"]').forEach((b) => {
      const on = b === el;
      b.classList.toggle("bg-surface-container-high", on);
      b.classList.toggle("text-on-surface", on); b.classList.toggle("font-bold", on);
      b.classList.toggle("text-on-surface-variant", !on);
    });
    render();
  });

  // pass-threshold slider -> recolor histogram + pass/fail + label
  const slider = $("pass-threshold");
  if (slider) slider.addEventListener("input", (e) => {
    threshold = parseInt(e.target.value, 10) || 0;
    const lbl = $("threshold-val"); if (lbl) lbl.textContent = threshold + "%";
    histogram(); render();
  });

  onAction("viewScorecard", () => { /* TODO: open scorecard drawer / route */ });

  load();
})();
