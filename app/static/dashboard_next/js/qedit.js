// MCQ / numeric / true-false / short-answer authoring — vanilla, CSP-safe. The bulk
// endpoint POST /api/v1/admin/questions REPLACES the exam's non-coding set (coding rows
// are server-protected), so every save re-sends the full non-coding set fetched fresh —
// no stale client state, no data loss. Coding questions are authored by wizard.js.
//   GET  /api/v1/admin/questions?exam_id=…    (full set; now incl. reference_answer/max_score/rubric)
//   POST /api/v1/admin/questions  {exam_id, questions:[{id,question,options,correct,question_type,…}]}
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const ea = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const KEYS = "ABCDEFGH";
  const TYPES = [["mcq_single", "Multiple Choice"], ["mcq_multi", "Multi-Select"], ["true_false", "True / False"], ["numeric", "Numeric"], ["short_answer", "Short Answer"], ["coding", "Coding (wizard)"]];
  const fieldCls = "w-full bg-surface-container-low border border-[#30363d] rounded-lg px-4 py-3 text-body-base focus:ring-primary focus:border-primary";
  const labelCls = "block text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-2";

  let cur = null; // {id, type, question, opts:[{text,correct}], tf, min, max, ref, maxscore, rubric, _orig}

  const MODAL_HTML =
    '<div id="qeditModal" class="fixed inset-0 z-[90] hidden items-center justify-center bg-black/70 backdrop-blur-sm p-md">' +
    '<div class="bg-surface border border-[#30363d] w-full max-w-2xl max-h-[90vh] rounded-2xl overflow-hidden flex flex-col shadow-2xl">' +
    '<div class="px-lg py-4 border-b border-[#30363d] flex items-center justify-between bg-surface-container-low">' +
    '<h2 id="qe-title" class="font-bold text-lg">Add Question</h2>' +
    '<button data-action="qeClose" class="p-2 hover:bg-surface-container-high rounded-full"><span class="material-symbols-outlined">close</span></button></div>' +
    '<div class="flex-1 overflow-y-auto p-lg space-y-5">' +
    '<div><label class="' + labelCls + '">Type</label><select id="qe-type" data-action="qeTypeChange" class="' + fieldCls + ' font-semibold [&>option]:bg-surface-container">' +
    TYPES.map((t) => '<option value="' + t[0] + '">' + t[1] + '</option>').join("") + '</select></div>' +
    '<div><label class="' + labelCls + '">Question</label><textarea id="qe-question" rows="3" class="' + fieldCls + ' resize-none" placeholder="Enter the question text…"></textarea></div>' +
    '<div id="qe-body"></div><p id="qe-err" class="text-error text-body-sm hidden"></p></div>' +
    '<div class="px-lg py-4 border-t border-[#30363d] bg-surface-container-low flex justify-end gap-3">' +
    '<button data-action="qeClose" class="px-6 py-2.5 rounded-lg border border-[#30363d] font-bold text-sm hover:bg-surface-container-high">Cancel</button>' +
    '<button id="qe-save" data-action="qeSave" class="px-8 py-2.5 rounded-lg bg-[#6366f1] text-white font-bold text-sm hover:bg-opacity-90">Save Question</button></div></div></div>';

  function ensureModal() { if (!$("qeditModal")) { const h = document.createElement("div"); h.innerHTML = MODAL_HTML; document.body.appendChild(h.firstChild); } }
  function showModal() { const m = $("qeditModal"); if (m) { m.classList.remove("hidden"); m.classList.add("flex"); } }
  function hideModal() { const m = $("qeditModal"); if (m) { m.classList.add("hidden"); m.classList.remove("flex"); } }

  // ---------- per-type body ----------
  function optionRow(o, i) {
    const ctrl = cur.type === "mcq_multi"
      ? `<input type="checkbox" class="qe-correct accent-secondary w-5 h-5" data-i="${i}" ${o.correct ? "checked" : ""}/>`
      : `<input type="radio" name="qe-correct" class="qe-correct accent-secondary w-5 h-5" data-i="${i}" ${o.correct ? "checked" : ""}/>`;
    return `<div class="flex items-center gap-2">
      <span class="w-7 h-7 rounded-lg bg-surface-container-high flex items-center justify-center text-xs font-bold text-on-surface-variant shrink-0">${KEYS[i] || i + 1}</span>
      <input class="qe-opt ${fieldCls} py-2" data-i="${i}" type="text" placeholder="Option ${KEYS[i] || i + 1}" value="${ea(o.text)}"/>
      <label class="flex items-center gap-1 text-[11px] text-secondary font-bold shrink-0" title="Mark correct">${ctrl}</label>
      <button data-action="qeDelOpt" data-i="${i}" class="text-on-surface-variant hover:text-error shrink-0"><span class="material-symbols-outlined text-[18px]">close</span></button></div>`;
  }
  function renderBody() {
    const b = $("qe-body"); if (!b) return;
    if (cur.type === "mcq_single" || cur.type === "mcq_multi") {
      b.innerHTML = `<label class="${labelCls}">Options ${cur.type === "mcq_multi" ? "(check all correct)" : "(select the correct one)"}</label>
        <div id="qe-opts" class="space-y-2">${cur.opts.map(optionRow).join("")}</div>
        <button data-action="qeAddOpt" class="mt-3 flex items-center gap-2 px-4 py-2 rounded-lg border border-primary/30 text-primary font-semibold text-sm hover:bg-primary/10"><span class="material-symbols-outlined text-[18px]">add</span> Add option</button>`;
    } else if (cur.type === "true_false") {
      b.innerHTML = `<label class="${labelCls}">Correct Answer</label>
        <div class="flex gap-3">${["True", "False"].map((v) => `<label class="flex-1 flex items-center gap-2 p-3 rounded-lg border ${cur.tf === v ? "border-secondary bg-secondary/10" : "border-[#30363d]"} cursor-pointer">
          <input type="radio" name="qe-tf" class="qe-tf accent-secondary" value="${v}" ${cur.tf === v ? "checked" : ""}/><span class="font-semibold">${v}</span></label>`).join("")}</div>`;
    } else if (cur.type === "numeric") {
      b.innerHTML = `<label class="${labelCls}">Accepted Answer Range</label><div class="grid grid-cols-2 gap-4">
        <div><span class="text-[11px] text-on-surface-variant">Minimum</span><input id="qe-min" type="number" step="any" class="${fieldCls} font-data-mono" value="${ea(cur.min)}"/></div>
        <div><span class="text-[11px] text-on-surface-variant">Maximum</span><input id="qe-max" type="number" step="any" class="${fieldCls} font-data-mono" value="${ea(cur.max)}"/></div></div>
        <p class="text-on-surface-variant text-xs mt-2">A student's numeric answer is correct if it falls within [min, max]. Use the same value twice for an exact answer.</p>`;
    } else if (cur.type === "short_answer") {
      b.innerHTML = `<div class="space-y-4">
        <div><label class="${labelCls}">Reference Answer</label><textarea id="qe-ref" rows="3" class="${fieldCls} resize-none" placeholder="Model answer used for grading…">${ea(cur.ref)}</textarea></div>
        <div class="grid grid-cols-2 gap-4"><div><label class="${labelCls}">Max Score</label><input id="qe-maxscore" type="number" min="1" step="0.5" class="${fieldCls} font-data-mono text-primary" value="${ea(cur.maxscore || 1)}"/></div></div>
        <div><label class="${labelCls}">Rubric (optional)</label><textarea id="qe-rubric" rows="2" class="${fieldCls} resize-none" placeholder="Grading guidance…">${ea(cur.rubric)}</textarea></div></div>`;
    } else { b.innerHTML = ""; }
  }

  // ---------- capture DOM -> cur ----------
  function capture() {
    if (!cur) return;
    if ($("qe-question")) cur.question = $("qe-question").value;
    if (cur.type === "mcq_single" || cur.type === "mcq_multi") {
      document.querySelectorAll(".qe-opt").forEach((el) => { const i = +el.getAttribute("data-i"); if (cur.opts[i]) cur.opts[i].text = el.value; });
      const correct = Array.prototype.map.call(document.querySelectorAll(".qe-correct:checked"), (c) => +c.getAttribute("data-i"));
      cur.opts.forEach((o, i) => { o.correct = correct.indexOf(i) !== -1; });
    } else if (cur.type === "true_false") {
      const sel = document.querySelector(".qe-tf:checked"); if (sel) cur.tf = sel.value;
    } else if (cur.type === "numeric") {
      if ($("qe-min")) cur.min = $("qe-min").value; if ($("qe-max")) cur.max = $("qe-max").value;
    } else if (cur.type === "short_answer") {
      if ($("qe-ref")) cur.ref = $("qe-ref").value; if ($("qe-maxscore")) cur.maxscore = $("qe-maxscore").value; if ($("qe-rubric")) cur.rubric = $("qe-rubric").value;
    }
  }

  function blank(type) { return { id: null, type: type || "mcq_single", question: "", opts: [{ text: "", correct: true }, { text: "", correct: false }], tf: "True", min: "", max: "", ref: "", maxscore: 1, rubric: "", _orig: null }; }

  function fromExisting(q) {
    const c = blank(q.question_type || "mcq_single");
    c.id = q.id; c.question = q.question || ""; c._orig = q;
    if (c.type === "mcq_single" || c.type === "mcq_multi") {
      const opts = q.options && typeof q.options === "object" ? q.options : {};
      const correct = new Set(String(q.correct || "").split(",").map((s) => s.trim()).filter(Boolean));
      const keys = Object.keys(opts);
      c.opts = keys.length ? keys.map((k) => ({ text: opts[k], correct: correct.has(k) })) : c.opts;
    } else if (c.type === "true_false") { c.tf = String(q.correct || "True"); }
    else if (c.type === "numeric") { const p = String(q.correct || "").split(":"); if (p[0] === "range") { c.min = p[1] || ""; c.max = p[2] || ""; } }
    else if (c.type === "short_answer") { c.ref = q.reference_answer || ""; c.maxscore = q.max_score || 1; c.rubric = q.rubric || ""; }
    return c;
  }

  function renderTitleType() { const t = $("qe-type"); if (t) t.value = cur.type; const h = $("qe-title"); if (h) h.textContent = cur.id ? "Edit Question" : "Add Question"; }

  // ---------- open ----------
  function openEditor(q) {
    ensureModal();
    cur = q ? fromExisting(q) : blank("mcq_single");
    renderTitleType();
    const qq = $("qe-question"); if (qq) qq.value = cur.question;
    renderBody(); const e = $("qe-err"); if (e) e.classList.add("hidden");
    showModal();
  }

  onAction("addQuestion", () => openEditor(null));
  onAction("editQuestion", async (el) => {
    const qid = el.getAttribute("data-qid");
    const ex = api.examId ? api.examId() : "";
    try {
      const r = await authFetch("/api/v1/admin/questions" + (ex ? `?exam_id=${encodeURIComponent(ex)}` : ""));
      const d = r.ok ? await r.json() : {};
      const q = (d.questions || []).find((x) => String(x.id) === String(qid));
      if (q) openEditor(q); else openEditor(null);
    } catch (_) { openEditor(null); }
  });
  onAction("qeClose", hideModal);
  onAction("qeTypeChange", (el) => {
    capture();
    const t = el.value;
    if (t === "coding") {
      // Coding is authored by wizard.js — close this editor and hand off by firing a
      // synthetic openCoding click (api.js delegates it to the wizard's handler).
      hideModal();
      const f = document.createElement("button");
      f.setAttribute("data-action", "openCoding");
      f.style.display = "none";
      document.body.appendChild(f); f.click(); f.remove();
      return;
    }
    cur.type = t; renderBody();
  });
  onAction("qeAddOpt", () => { capture(); if (cur.opts.length < 8) cur.opts.push({ text: "", correct: false }); renderBody(); });
  onAction("qeDelOpt", (el) => { capture(); const i = +el.getAttribute("data-i"); if (cur.opts.length > 2) cur.opts.splice(i, 1); renderBody(); });

  // ---------- validate + save ----------
  function showErr(msg) { const e = $("qe-err"); if (e) { e.textContent = msg; e.classList.remove("hidden"); } }
  function buildPayloadQ() {
    const t = cur.type;
    if (t === "mcq_single" || t === "mcq_multi") {
      const opts = cur.opts.filter((o) => o.text.trim());
      if (opts.length < 2) return { err: "Add at least 2 options." };
      const options = {}, correct = [];
      opts.forEach((o, i) => { const k = KEYS[i] || String(i + 1); options[k] = o.text; if (o.correct) correct.push(k); });
      if (t === "mcq_single" && correct.length !== 1) return { err: "Pick exactly one correct option." };
      if (t === "mcq_multi" && correct.length < 2) return { err: "Multi-select needs at least 2 correct options." };
      return { q: { id: cur.id, question: cur.question, options, correct: correct.join(","), question_type: t } };
    }
    if (t === "true_false") return { q: { id: cur.id, question: cur.question, options: { True: "True", False: "False" }, correct: cur.tf, question_type: t } };
    if (t === "numeric") {
      if (cur.min === "" || cur.max === "" || isNaN(+cur.min) || isNaN(+cur.max)) return { err: "Enter numeric min and max." };
      return { q: { id: cur.id, question: cur.question, options: {}, correct: `range:${cur.min}:${cur.max}`, question_type: t } };
    }
    if (t === "short_answer") {
      if (!cur.ref.trim()) return { err: "Reference answer is required." };
      return { q: { id: cur.id, question: cur.question, options: {}, correct: "", question_type: t, reference_answer: cur.ref, max_score: +cur.maxscore || 1, rubric: cur.rubric } };
    }
    return { err: "Unsupported type." };
  }
  // round-trip an untouched question straight back to the bulk payload
  function passThrough(q) {
    const p = { id: q.id, question: q.question || "", options: q.options || {}, correct: q.correct || "", question_type: q.question_type || "mcq_single" };
    if (q.image_url) p.image_url = q.image_url;
    if (p.question_type === "short_answer") { p.reference_answer = q.reference_answer || ""; p.max_score = q.max_score || 1; if (q.rubric) p.rubric = q.rubric; }
    return p;
  }

  onAction("qeSave", async (btn) => {
    capture();
    if (!cur.question.trim()) { showErr("Question text is required."); return; }
    const built = buildPayloadQ();
    if (built.err) { showErr(built.err); return; }
    const exam_id = api.examId ? api.examId() : "";
    if (!exam_id) { showErr("Select an exam in the top bar before saving."); return; }
    const edited = built.q;
    if (!edited.id) edited.id = String(Date.now());
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const r0 = await authFetch(`/api/v1/admin/questions?exam_id=${encodeURIComponent(exam_id)}`);
      const d0 = r0.ok ? await r0.json() : { questions: [] };
      const nonCoding = (d0.questions || []).filter((q) => (q.question_type || "") !== "coding");
      const arr = nonCoding.filter((q) => String(q.id) !== String(edited.id)).map(passThrough);
      arr.push(edited);
      const r = await authFetch("/api/v1/admin/questions", { method: "POST", body: JSON.stringify({ exam_id, questions: arr }) });
      if (r.ok) { hideModal(); window.dispatchEvent(new CustomEvent("procta:reload-questions")); }
      else { const d = await r.json().catch(() => ({})); showErr("Save failed: " + (d.detail || ("HTTP " + r.status))); }
    } catch (_) { showErr("Save failed."); }
    finally { btn.disabled = false; btn.textContent = "Save Question"; }
  });

  // delete a non-coding question (bulk re-save without it)
  onAction("deleteQuestion", async (el) => {
    const qid = el.getAttribute("data-qid");
    if (!window.confirm("Delete this question?")) return;
    const exam_id = api.examId ? api.examId() : "";
    if (!exam_id) { alert("Select an exam first."); return; }
    try {
      const r0 = await authFetch(`/api/v1/admin/questions?exam_id=${encodeURIComponent(exam_id)}`);
      const d0 = r0.ok ? await r0.json() : { questions: [] };
      const arr = (d0.questions || []).filter((q) => (q.question_type || "") !== "coding" && String(q.id) !== String(qid)).map(passThrough);
      const r = await authFetch("/api/v1/admin/questions", { method: "POST", body: JSON.stringify({ exam_id, questions: arr }) });
      if (r.ok) window.dispatchEvent(new CustomEvent("procta:reload-questions"));
      else { const d = await r.json().catch(() => ({})); alert("Delete failed: " + (d.detail || ("HTTP " + r.status))); }
    } catch (_) { alert("Delete failed."); }
  });
})();
