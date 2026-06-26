// PDF/Word question import — vanilla, CSP-safe. Upload → extract PREVIEW → import the
// ready (unflagged) questions into the question bank. Server re-validates blocking flags;
// flagged questions are shown but skipped (resolve them in the advanced editor — a follow-up).
//   POST /api/v1/admin/question-bank/extract          (multipart file)  -> {found,ready,questions[]}
//   POST /api/v1/admin/question-bank/extract/confirm  {questions}       -> persists to bank
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // Blocking flags mirror the server's BLOCKING_FLAGS (questions with these can't import yet).
  const BLOCKING = new Set(["no_answer", "no_options", "needs_review", "unparsed", "empty"]);
  let extracted = [];

  const MODAL =
    '<div id="impModal" class="fixed inset-0 z-[95] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface border border-[#30363d] w-full max-w-2xl max-h-[88vh] rounded-2xl overflow-hidden flex flex-col shadow-2xl">' +
    '<div class="px-lg py-4 border-b border-[#30363d] flex items-center justify-between bg-surface-container-low">' +
    '<h2 class="font-bold text-lg flex items-center gap-2"><span class="material-symbols-outlined text-primary">upload_file</span> Import Questions</h2>' +
    '<button data-action="impClose" class="p-2 hover:bg-surface-container-high rounded-full"><span class="material-symbols-outlined">close</span></button></div>' +
    '<div id="imp-body" class="flex-1 overflow-y-auto p-lg space-y-3"></div>' +
    '<div class="px-lg py-4 border-t border-[#30363d] bg-surface-container-low flex items-center justify-between">' +
    '<span id="imp-summary" class="text-on-surface-variant text-body-sm"></span>' +
    '<div class="flex gap-3"><button data-action="impClose" class="px-6 py-2.5 rounded-lg border border-[#30363d] font-bold text-sm hover:bg-surface-container-high">Close</button>' +
    '<button id="imp-go" data-action="impConfirm" class="px-8 py-2.5 rounded-lg bg-[#6366f1] text-white font-bold text-sm hover:bg-opacity-90 hidden">Import to Bank</button></div></div></div></div>';

  function ensure() {
    if (!$("impModal")) { const h = document.createElement("div"); h.innerHTML = MODAL; document.body.appendChild(h.firstChild); }
    if (!$("imp-file")) { const f = document.createElement("input"); f.type = "file"; f.id = "imp-file"; f.accept = ".pdf,.docx"; f.style.display = "none"; f.addEventListener("change", onFile); document.body.appendChild(f); }
  }
  function show() { const m = $("impModal"); m.classList.remove("hidden"); m.classList.add("flex"); }
  function hide() { const m = $("impModal"); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } }
  const blockingOf = (q) => (Array.isArray(q.flags) ? q.flags.filter((f) => BLOCKING.has(f)) : []);

  function renderPreview(found, ready) {
    const body = $("imp-body");
    body.innerHTML = extracted.map((q, i) => {
      const bf = blockingOf(q);
      const tone = bf.length ? "error" : "secondary";
      return `<div class="bg-surface-container border border-[#30363d] rounded-lg p-3">
        <div class="flex justify-between items-start gap-2 mb-1"><span class="font-data-mono text-outline text-[11px]">#${i + 1}</span>
          <span class="text-[10px] font-bold uppercase px-2 py-0.5 rounded bg-${tone}/10 text-${tone} border border-${tone}/20">${bf.length ? "Needs fixing" : "Ready"}</span></div>
        <p class="text-body-sm text-on-surface">${esc((q.question || "").slice(0, 220))}</p>
        ${bf.length ? `<p class="text-[11px] text-error mt-1">${esc(bf.join(", "))}</p>` : ""}</div>`;
    }).join("");
    const sum = $("imp-summary"); if (sum) sum.textContent = `Found ${found} · ${ready} ready` + (found - ready > 0 ? ` · ${found - ready} need fixing (skipped)` : "");
    const go = $("imp-go"); if (go) go.classList.toggle("hidden", ready === 0);
  }

  async function onFile(e) {
    const file = e.target.files && e.target.files[0]; if (!file) return;
    ensure(); show();
    $("imp-body").innerHTML = '<div class="text-on-surface-variant text-body-sm flex items-center gap-2"><span class="material-symbols-outlined animate-spin">progress_activity</span> Extracting…</div>';
    $("imp-summary").textContent = ""; $("imp-go").classList.add("hidden");
    const fd = new FormData(); fd.append("file", file);
    try {
      const r = await authFetch("/api/v1/admin/question-bank/extract", { method: "POST", body: fd });
      if (!r.ok) { const d = await r.json().catch(() => ({})); $("imp-body").innerHTML = `<p class="text-error text-body-sm">${esc(d.detail || ("Extraction failed (HTTP " + r.status + ")."))}</p>`; e.target.value = ""; return; }
      const d = await r.json();
      extracted = Array.isArray(d.questions) ? d.questions : [];
      if (!extracted.length) { $("imp-body").innerHTML = '<p class="text-on-surface-variant text-body-sm">No questions found in that file.</p>'; e.target.value = ""; return; }
      renderPreview(d.found != null ? d.found : extracted.length, d.ready != null ? d.ready : extracted.filter((q) => !blockingOf(q).length).length);
    } catch (_) { $("imp-body").innerHTML = '<p class="text-error text-body-sm">Upload failed.</p>'; }
    e.target.value = "";
  }

  onAction("importPdf", () => { ensure(); $("imp-file").click(); });
  onAction("impClose", hide);
  onAction("impConfirm", async (btn) => {
    const ready = extracted.filter((q) => !blockingOf(q).length);
    if (!ready.length) return;
    btn.disabled = true; btn.textContent = "Importing…";
    try {
      const r = await authFetch("/api/v1/admin/question-bank/extract/confirm", { method: "POST", body: JSON.stringify({ questions: ready }) });
      if (r.ok) { hide(); alert(`Imported ${ready.length} question${ready.length === 1 ? "" : "s"} into your question bank. Open the bank panel to add them to an exam.`); }
      else { const d = await r.json().catch(() => ({})); alert("Import failed: " + (d.detail || ("HTTP " + r.status))); }
    } catch (_) { alert("Import failed."); }
    finally { btn.disabled = false; btn.textContent = "Import to Bank"; }
  });
})();
