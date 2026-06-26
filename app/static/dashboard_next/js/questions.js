// Questions authoring — vanilla, CSP-safe. Renders the exam's question list from
// /api/v1/admin/questions and toggles the coding-wizard modal (was inline onclick).
// TODO (next increment): coding-wizard steps + save (/admin/coding-question), MCQ/
// numeric inline editors, AI generate, PDF import, question-bank, exam selector.
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;

  // The coding-wizard modal (#codingModal: open/close/steps/save) is owned by wizard.js,
  // which registers openCoding/closeCoding. Here we just keep the list + bank toggle.
  onAction("bankToggle", () => { const p = document.getElementById("bankPanel"); if (p) p.classList.toggle("hidden"); });

  // ---- question list ----
  const list = document.getElementById("question-list");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const TYPE = {
    mcq_single: ["Multiple Choice", "primary"], mcq_multi: ["Multi-Select", "primary"],
    true_false: ["True / False", "primary"], coding: ["Coding", "tertiary"],
    numeric: ["Numeric", "secondary"], short_answer: ["Short Answer", "secondary"],
  };

  function optionsHtml(q) {
    const opts = q.options && typeof q.options === "object" ? q.options : null;
    if (!opts) return "";
    const correct = new Set(String(q.correct || "").split(/[,\s]+/).filter(Boolean));
    const keys = Object.keys(opts);
    return `<div class="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">` + keys.map((k) => {
      const ok = correct.has(k);
      return `<div class="flex items-center gap-2 p-2.5 rounded-lg border ${ok ? "border-secondary bg-secondary/10" : "border-[#30363d]"}">
        <span class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${ok ? "bg-secondary text-on-secondary" : "bg-surface-container-high text-on-surface-variant"}">${esc(k)}</span>
        <span class="text-body-sm flex-1 truncate">${esc(opts[k])}</span>
        ${ok ? '<span class="material-symbols-outlined text-secondary text-[18px]" data-icon="check_circle">check_circle</span>' : ""}</div>`;
    }).join("") + `</div>`;
  }

  function card(q, i) {
    const ty = (q.question_type || "mcq_single").toLowerCase();
    const [label, tone] = TYPE[ty] || ["Question", "outline"];
    const marks = q.marks != null ? q.marks : (q.max_score != null ? q.max_score : "");
    let body = `<p class="text-body-base mb-1">${esc(q.question || "")}</p>`;
    if (ty === "coding") body += `<p class="text-body-sm text-on-surface-variant">Coding question — open to edit languages, starter & test cases.</p>`;
    else if (ty === "numeric") body += `<p class="font-data-mono text-body-sm text-on-surface-variant">Answer: ${esc(q.correct || "")}</p>`;
    else body += optionsHtml(q);
    const el = document.createElement("div");
    el.className = "bg-surface-container border border-[#30363d] rounded-xl p-md";
    el.innerHTML = `
      <div class="flex items-center justify-between mb-2">
        <div class="flex items-center gap-2">
          <span class="font-data-mono text-outline text-body-sm">Q${i + 1}</span>
          <span class="text-[10px] font-bold uppercase px-2 py-0.5 rounded bg-${tone}/10 text-${tone} border border-${tone}/20">${esc(label)}</span>
        </div>
        <div class="flex items-center gap-3 text-on-surface-variant">
          ${marks !== "" ? `<span class="text-body-sm">${esc(marks)} Marks</span>` : ""}
          <button class="hover:text-primary" data-action="${ty === "coding" ? "openCoding" : "editQuestion"}" data-qid="${esc(q.id || "")}"><span class="material-symbols-outlined text-[18px]" data-icon="edit">edit</span></button>
          <button class="hover:text-error" data-action="deleteQuestion" data-qid="${esc(q.id || "")}"><span class="material-symbols-outlined text-[18px]" data-icon="delete">delete</span></button>
        </div></div>
      ${body}`;
    return el;
  }

  async function load() {
    if (!list) return;
    try {
      const ex = api.examId ? api.examId() : "";
      const r = await authFetch("/api/v1/admin/questions" + (ex ? `?exam_id=${encodeURIComponent(ex)}` : ""));
      if (!r.ok) return;
      const d = await r.json().catch(() => ({}));
      const qs = Array.isArray(d) ? d : (Array.isArray(d.questions) ? d.questions : []);
      list.innerHTML = "";
      if (!qs.length) { list.innerHTML = '<div class="p-md text-on-surface-variant text-body-sm">No questions yet — add one, import, or generate.</div>'; return; }
      qs.forEach((q, i) => list.appendChild(card(q, i)));
    } catch (_) {}
  }

  onAction("editQuestion", () => { /* TODO: MCQ/numeric inline editor (next increment) */ });
  onAction("deleteQuestion", () => { /* TODO: confirm + DELETE (next increment) */ });
  if (api.onExamChange) api.onExamChange(() => load());
  window.addEventListener("procta:reload-questions", load); // wizard saved a coding question

  load();
})();
