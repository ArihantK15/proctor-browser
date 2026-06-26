// Coding-question wizard — vanilla, CSP-safe. Stitch shipped only Step 1's body, so the
// full 5-step flow (Problem → Languages → Sample Tests → Hidden Tests → Review) is rendered
// here in the same visual language and actually SAVES. Owns the #codingModal.
//   POST/PUT /api/v1/admin/coding-question  {exam_id,question,options{allowed_languages,
//     marks,marks_policy,time_limit_ms,starter_code},test_cases:[{input,expected_output,
//     visibility,float_tolerance?}],question_id?}
//   GET      /api/v1/admin/coding-question?question_id=…   (edit prefill)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const STEPS = ["Problem", "Languages", "Sample Tests", "Hidden Tests", "Review"];
  const LANGS = ["python", "javascript", "typescript", "java", "cpp", "c"];
  const LANG_LABEL = { python: "Python", javascript: "JavaScript", typescript: "TypeScript", java: "Java", cpp: "C++", c: "C" };
  const ea = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let state, step = 0, editingQid = null;
  const blank = () => ({ title: "", statement: "", marks: 10, difficulty: "medium", languages: ["python"], time_limit_ms: 5000, marks_policy: "partial", starter_code: "", sample: [{ input: "", expected: "" }], hidden: [{ input: "", expected: "", tol: "" }] });

  // ---------- per-step bodies ----------
  const fieldCls = "w-full bg-surface-container-low border border-[#30363d] rounded-lg px-4 py-3 text-body-base focus:ring-primary focus:border-primary";
  const labelCls = "block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-2";

  function bodyProblem() {
    return `<div class="space-y-6 max-w-3xl">
      <div><label class="${labelCls}">Question Title</label>
        <input id="wq-title" class="${fieldCls}" type="text" placeholder="e.g. Newton's Second Law Calculator" value="${ea(state.title)}"/></div>
      <div><label class="${labelCls}">Problem Statement</label>
        <textarea id="wq-statement" rows="7" class="${fieldCls} resize-none" placeholder="Describe the programming task, constraints, and requirements…">${ea(state.statement)}</textarea></div>
      <div class="grid grid-cols-2 gap-4">
        <div><label class="${labelCls}">Points</label>
          <input id="wq-marks" class="${fieldCls} font-data-mono text-primary" type="number" min="1" max="100" value="${ea(state.marks)}"/></div>
        <div><label class="${labelCls}">Difficulty</label>
          <select id="wq-difficulty" class="${fieldCls} font-semibold [&>option]:bg-surface-container">
            ${["easy", "medium", "hard"].map((d) => `<option value="${d}" ${state.difficulty === d ? "selected" : ""}>${d[0].toUpperCase() + d.slice(1)}</option>`).join("")}
          </select></div>
      </div></div>`;
  }

  function bodyLanguages() {
    return `<div class="space-y-6 max-w-3xl">
      <div><label class="${labelCls}">Allowed Languages</label>
        <div class="grid grid-cols-3 gap-2">${LANGS.map((l) => `
          <label class="flex items-center gap-2 p-3 rounded-lg border ${state.languages.includes(l) ? "border-primary bg-primary/10" : "border-[#30363d] bg-surface-container-low"} cursor-pointer">
            <input type="checkbox" class="wq-lang accent-primary" value="${l}" ${state.languages.includes(l) ? "checked" : ""}/>
            <span class="text-sm font-semibold">${LANG_LABEL[l]}</span></label>`).join("")}</div></div>
      <div class="grid grid-cols-2 gap-4">
        <div><label class="${labelCls}">Time Limit (ms)</label>
          <input id="wq-timelimit" class="${fieldCls} font-data-mono" type="number" min="500" max="15000" step="500" value="${ea(state.time_limit_ms)}"/></div>
        <div><label class="${labelCls}">Marking Policy</label>
          <select id="wq-policy" class="${fieldCls} font-semibold [&>option]:bg-surface-container">
            <option value="partial" ${state.marks_policy === "partial" ? "selected" : ""}>Partial (per test case)</option>
            <option value="all_or_nothing" ${state.marks_policy === "all_or_nothing" ? "selected" : ""}>All or nothing</option>
          </select></div></div>
      <div><label class="${labelCls}">Starter Code (optional, shared)</label>
        <textarea id="wq-starter" rows="6" class="${fieldCls} font-data-mono text-sm resize-none" placeholder="// Boilerplate shown to students">${ea(state.starter_code)}</textarea></div></div>`;
  }

  function caseRows(list, kind) {
    if (!list.length) return `<p class="text-on-surface-variant text-sm">No ${kind} cases yet.</p>`;
    return list.map((c, i) => `<div class="bg-surface-container-low border border-[#30363d] rounded-lg p-3 space-y-2">
      <div class="flex justify-between items-center"><span class="text-xs font-bold text-on-surface-variant">Case ${i + 1}</span>
        <button data-action="wizardDelRow" data-kind="${kind}" data-idx="${i}" class="text-on-surface-variant hover:text-error"><span class="material-symbols-outlined text-[18px]">delete</span></button></div>
      <div class="grid grid-cols-2 gap-2">
        <textarea data-row="${kind}" data-idx="${i}" data-f="input" rows="2" class="${fieldCls} font-data-mono text-sm resize-none" placeholder="stdin">${ea(c.input)}</textarea>
        <textarea data-row="${kind}" data-idx="${i}" data-f="expected" rows="2" class="${fieldCls} font-data-mono text-sm resize-none" placeholder="expected stdout">${ea(c.expected)}</textarea></div>
      ${kind === "hidden" ? `<input data-row="hidden" data-idx="${i}" data-f="tol" class="${fieldCls} font-data-mono text-sm py-2" type="text" placeholder="float tolerance (optional, e.g. 0.001)" value="${ea(c.tol)}"/>` : ""}</div>`).join("");
  }

  function bodyTests(kind) {
    const list = kind === "sample" ? state.sample : state.hidden;
    const note = kind === "sample" ? "Shown to students as worked examples." : "Hidden — used for grading. At least one is required.";
    return `<div class="space-y-4 max-w-3xl">
      <p class="text-on-surface-variant text-sm">${note}</p>
      <div id="wq-${kind}-list" class="space-y-3">${caseRows(list, kind)}</div>
      <button data-action="wizardAddRow" data-kind="${kind}" class="flex items-center gap-2 px-4 py-2 rounded-lg border border-primary/30 text-primary font-semibold text-sm hover:bg-primary/10">
        <span class="material-symbols-outlined text-[18px]">add</span> Add ${kind} case</button></div>`;
  }

  function bodyReview() {
    const probs = validate();
    return `<div class="space-y-4 max-w-3xl">
      ${probs.length ? `<div class="bg-error/10 border border-error/30 rounded-lg p-3 text-sm text-error"><b>Fix before saving:</b><ul class="list-disc ml-5 mt-1">${probs.map((p) => `<li>${ea(p)}</li>`).join("")}</ul></div>` : '<div class="bg-secondary/10 border border-secondary/30 rounded-lg p-3 text-sm text-secondary">Ready to save.</div>'}
      <div class="bg-surface-container-low border border-[#30363d] rounded-xl p-4 space-y-2 text-sm">
        <div class="flex justify-between"><span class="text-on-surface-variant">Title</span><span class="font-semibold">${ea(state.title || "—")}</span></div>
        <div class="flex justify-between"><span class="text-on-surface-variant">Points</span><span class="font-data-mono">${ea(state.marks)}</span></div>
        <div class="flex justify-between"><span class="text-on-surface-variant">Difficulty</span><span>${ea(state.difficulty)}</span></div>
        <div class="flex justify-between"><span class="text-on-surface-variant">Languages</span><span>${state.languages.map((l) => LANG_LABEL[l]).join(", ") || "—"}</span></div>
        <div class="flex justify-between"><span class="text-on-surface-variant">Time limit</span><span class="font-data-mono">${ea(state.time_limit_ms)} ms</span></div>
        <div class="flex justify-between"><span class="text-on-surface-variant">Sample / Hidden cases</span><span class="font-data-mono">${state.sample.length} / ${state.hidden.length}</span></div>
      </div>
      <p class="text-on-surface-variant text-xs">Saving to exam: <span class="font-data-mono text-primary">${ea(api.examId() || "— none selected —")}</span></p></div>`;
  }

  const BODIES = [bodyProblem, bodyLanguages, () => bodyTests("sample"), () => bodyTests("hidden"), bodyReview];

  // ---------- capture current DOM into state ----------
  function capture() {
    if (step === 0) {
      if ($("wq-title")) state.title = $("wq-title").value;
      if ($("wq-statement")) state.statement = $("wq-statement").value;
      if ($("wq-marks")) state.marks = parseInt($("wq-marks").value, 10) || 1;
      if ($("wq-difficulty")) state.difficulty = $("wq-difficulty").value;
    } else if (step === 1) {
      const checked = Array.prototype.slice.call(document.querySelectorAll(".wq-lang:checked")).map((c) => c.value);
      state.languages = checked;
      if ($("wq-timelimit")) state.time_limit_ms = parseInt($("wq-timelimit").value, 10) || 5000;
      if ($("wq-policy")) state.marks_policy = $("wq-policy").value;
      if ($("wq-starter")) state.starter_code = $("wq-starter").value;
    } else if (step === 2 || step === 3) {
      const kind = step === 2 ? "sample" : "hidden";
      document.querySelectorAll(`[data-row="${kind}"]`).forEach((el) => {
        const i = parseInt(el.getAttribute("data-idx"), 10), f = el.getAttribute("data-f");
        const list = kind === "sample" ? state.sample : state.hidden;
        if (list[i]) list[i][f] = el.value;
      });
    }
  }

  function validate() {
    const p = [];
    if (!state.statement.trim()) p.push("Problem statement is required.");
    if (!state.languages.length) p.push("Pick at least one language.");
    if (!state.hidden.filter((c) => c.expected.trim() || c.input.trim()).length) p.push("Add at least one hidden test case (nothing is graded otherwise).");
    if (state.marks < 1 || state.marks > 100) p.push("Points must be 1–100.");
    return p;
  }

  // ---------- render ----------
  function renderStepper() {
    const box = $("wizard-stepper"); if (!box) return;
    box.innerHTML = STEPS.map((label, i) => {
      const active = i === step, done = i < step;
      const dot = done ? '<span class="material-symbols-outlined text-[16px]">check</span>' : (i + 1);
      return `${i ? '<div class="w-8 h-px bg-[#30363d] mx-2"></div>' : ""}
        <div class="flex items-center py-4 border-b-2 ${active ? "border-primary" : "border-transparent"} min-w-max px-4 ${active || done ? "" : "opacity-40"}">
          <span class="w-6 h-6 rounded-full ${active ? "bg-primary text-on-primary" : done ? "bg-secondary text-on-secondary" : "bg-surface-container-highest text-on-surface-variant"} text-[10px] font-bold flex items-center justify-center mr-2">${dot}</span>
          <span class="text-xs font-bold text-on-surface">${label}</span></div>`;
    }).join("");
  }
  function renderBody() { const b = $("wizard-body"); if (b) b.innerHTML = BODIES[step](); }
  function renderFooter() {
    const back = $("wizard-back"), next = $("wizard-next");
    if (back) back.classList.toggle("hidden", step === 0);
    if (next) next.textContent = step === STEPS.length - 1 ? "Save Question" : "Next";
    const sub = $("wizard-subtitle"); if (sub) sub.textContent = `Step ${step + 1}: ${STEPS[step]}`;
  }
  function renderAll() { renderStepper(); renderBody(); renderFooter(); }

  // ---------- modal open/close (owns #codingModal) ----------
  function open() { const m = $("codingModal"); if (m) m.classList.remove("hidden"); renderAll(); }
  function close() { const m = $("codingModal"); if (m) m.classList.add("hidden"); }

  onAction("openCoding", async (el) => {
    state = blank(); step = 0; editingQid = null;
    const qid = el && el.getAttribute && el.getAttribute("data-qid");
    if (qid) {
      try {
        const r = await authFetch(`/api/v1/admin/coding-question?question_id=${encodeURIComponent(qid)}`);
        if (r.ok) {
          const d = await r.json(); const o = d.options || {};
          editingQid = d.question_id;
          state.statement = d.question || "";
          state.marks = o.marks || 10; state.marks_policy = o.marks_policy || "partial";
          state.time_limit_ms = o.time_limit_ms || 5000;
          state.languages = Array.isArray(o.allowed_languages) ? o.allowed_languages : ["python"];
          state.starter_code = typeof o.starter_code === "string" ? o.starter_code : (o.starter_code ? Object.values(o.starter_code)[0] || "" : "");
          const tc = Array.isArray(d.test_cases) ? d.test_cases : [];
          state.sample = tc.filter((c) => c.visibility === "sample").map((c) => ({ input: c.input || "", expected: c.expected_output || "" }));
          state.hidden = tc.filter((c) => c.visibility !== "sample").map((c) => ({ input: c.input || "", expected: c.expected_output || "", tol: c.float_tolerance != null ? String(c.float_tolerance) : "" }));
          if (!state.sample.length) state.sample = [{ input: "", expected: "" }];
          if (!state.hidden.length) state.hidden = [{ input: "", expected: "", tol: "" }];
        }
      } catch (_) {}
    }
    open();
  });
  onAction("closeCoding", close);
  onAction("wizardBack", () => { capture(); if (step > 0) { step--; renderAll(); } });
  onAction("wizardNext", () => { capture(); if (step < STEPS.length - 1) { step++; renderAll(); } else save(); });
  onAction("wizardAddRow", (el) => { capture(); const k = el.getAttribute("data-kind"); (k === "sample" ? state.sample : state.hidden).push(k === "sample" ? { input: "", expected: "" } : { input: "", expected: "", tol: "" }); renderBody(); });
  onAction("wizardDelRow", (el) => { capture(); const k = el.getAttribute("data-kind"), i = parseInt(el.getAttribute("data-idx"), 10); (k === "sample" ? state.sample : state.hidden).splice(i, 1); renderBody(); });

  async function save() {
    const probs = validate();
    if (probs.length) { renderBody(); alert("Please fix:\n• " + probs.join("\n• ")); return; }
    const exam_id = api.examId();
    if (!exam_id) { alert("Select an exam in the top bar before saving a coding question."); return; }
    const test_cases = []
      .concat(state.sample.filter((c) => c.input.trim() || c.expected.trim()).map((c) => ({ input: c.input, expected_output: c.expected, visibility: "sample" })))
      .concat(state.hidden.filter((c) => c.input.trim() || c.expected.trim()).map((c) => {
        const row = { input: c.input, expected_output: c.expected, visibility: "hidden" };
        if (c.tol && !isNaN(parseFloat(c.tol))) row.float_tolerance = parseFloat(c.tol);
        return row;
      }));
    const payload = {
      exam_id,
      question: state.title.trim() ? `${state.title.trim()}\n\n${state.statement}` : state.statement,
      options: { allowed_languages: state.languages, marks: state.marks, marks_policy: state.marks_policy, time_limit_ms: state.time_limit_ms, starter_code: state.starter_code },
      test_cases,
    };
    if (editingQid) payload.question_id = editingQid;
    const next = $("wizard-next"); if (next) { next.disabled = true; next.textContent = "Saving…"; }
    try {
      const r = await authFetch("/api/v1/admin/coding-question", { method: editingQid ? "PUT" : "POST", body: JSON.stringify(payload) });
      if (r.ok) { close(); window.dispatchEvent(new CustomEvent("procta:reload-questions")); }
      else { const d = await r.json().catch(() => ({})); alert("Save failed: " + (d.detail || ("HTTP " + r.status))); }
    } catch (_) { alert("Save failed."); }
    finally { if (next) { next.disabled = false; next.textContent = "Save Question"; } }
  }
})();
