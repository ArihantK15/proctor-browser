/*
 * coding-ui.js — Edge Compiler kiosk coding-question UI (Phase 1).
 * Loaded same-origin in renderer/index.html <head>; defines global helpers
 * (_renderCodingQuestion etc.) called from renderQ()'s `coding` branch.
 * References shared globals (answers, SERVER, authHdr, sessionId,
 * _persistAnswers, dotClass, curQ, questions, qNormType, runTestCases) at
 * call time — all classic scripts on the page share one global scope.
 */
// ── Edge Compiler: coding question UI (CodeMirror + Run/Submit) ──────
// Source is stored at answers[q.id] so it rides the EXISTING bulk autosave
// (save-answers-bulk) — never a separate save path. Run grades sample cases
// client-side (their expected output is public); Submit runs hidden inputs and
// POSTs only the OUTPUTS to the server judge, which holds the secret expected
// outputs and returns {passed,total}. Contracts are fixed by the server lane.
const _codingTele = {}; // per-qid telemetry: {paste,focusLoss,kIntervals[],lastKey}
let _codingBlurWired = false;
function _teleFor(qid){
  if(!_codingTele[qid]) _codingTele[qid] = {paste:0, focusLoss:0, kIntervals:[], lastKey:0};
  return _codingTele[qid];
}
function _wireCodingBlur(){
  if(_codingBlurWired) return; _codingBlurWired = true;
  window.addEventListener('blur', ()=>{
    const q=questions[curQ]; if(q && qNormType(q)==='coding'){ _teleFor(q.id).focusLoss++; }
  });
}
function _variance(a){
  if(!a.length) return 0;
  const m=a.reduce((x,y)=>x+y,0)/a.length;
  return a.reduce((s,y)=>s+(y-m)*(y-m),0)/a.length;
}
function _langsFor(q){
  const opt=q.options||{};
  // Accept every key the authoring/seed layer has used for the language list:
  // `allowed_languages` (current seed shape) plus the older `languages`/`language`.
  // Without `allowed_languages` here the dropdown silently fell back to JS only.
  let langs=opt.allowed_languages||opt.languages||opt.language||['javascript'];
  if(typeof langs==='string') langs=[langs];
  return langs.length?langs:['javascript'];
}
function _normOut(s){
  // mirror the server's lenient compare for the client-side sample diff:
  // strip trailing whitespace per line + trailing blank lines.
  return String(s==null?'':s).replace(/[ \t]+$/gm,'').replace(/\n+$/,'');
}
function _mountCodeEditor(holder, doc, language, onChange){
  // CodeMirror 6 if the bundle loaded; plain <textarea> fallback otherwise so
  // the slice still works if the editor bundle is ever missing.
  if(window.CMEditor && typeof window.CMEditor.create==='function'){
    return window.CMEditor.create(holder, {doc:doc||'', language:language, onChange:onChange});
  }
  const ta=document.createElement('textarea');
  ta.value=doc||''; ta.spellcheck=false;
  ta.style.cssText='width:100%;height:100%;min-height:240px;font:13px ui-monospace,Menlo,monospace;padding:10px;border:0;background:var(--card,#161a22);color:inherit;resize:vertical;box-sizing:border-box';
  ta.addEventListener('input',()=>onChange&&onChange(ta.value));
  holder.appendChild(ta);
  return {getValue:()=>ta.value, setValue:v=>{ta.value=v;}, focus:()=>ta.focus(), destroy:()=>ta.remove()};
}
async function _codingFetchCases(qid){
  // session_id is REQUIRED by the server (it runs the session-ownership check).
  const r=await fetch(`${SERVER}/api/v1/coding/testcases?session_id=${encodeURIComponent(sessionId)}&question_id=${encodeURIComponent(qid)}`,{headers:authHdr()});
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json(); // {sample:[{idx,input,expected_output}], hidden_inputs:[{idx,input}]}
}
let _codingJudgeQueue=[]; let _codingJudgeRetryTimer=null;
async function _codingSubmitJudge(body){
  try{
    const r=await fetch(`${SERVER}/api/v1/coding/judge`,{method:'POST',headers:authHdr(),body:JSON.stringify(body)});
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json(); // {passed,total}
  }catch(e){
    _codingJudgeQueue.push(body); _codingScheduleJudgeRetry();
    throw new Error('offline — queued for retry');
  }
}
function _codingScheduleJudgeRetry(){
  if(_codingJudgeRetryTimer) return;
  _codingJudgeRetryTimer=setTimeout(async ()=>{
    _codingJudgeRetryTimer=null;
    const pending=_codingJudgeQueue; _codingJudgeQueue=[];
    for(const body of pending){
      try{ const r=await fetch(`${SERVER}/api/v1/coding/judge`,{method:'POST',headers:authHdr(),body:JSON.stringify(body)}); if(!r.ok) throw 0; }
      catch(e){ _codingJudgeQueue.push(body); }
    }
    if(_codingJudgeQueue.length) _codingScheduleJudgeRetry();
  }, 5000);
}
function _renderCodingQuestion(container, q){
  _wireCodingBlur();
  const qid=q.id, tele=_teleFor(qid), langs=_langsFor(q);
  const starter=(q.options&&(q.options.starter_code||q.options.starter))||'';
  const initial = answers[qid]!=null ? String(answers[qid]) : String(starter);
  if(answers[qid]==null && starter){ answers[qid]=String(starter); } // baseline source

  const wrap=document.createElement('div'); wrap.className='coding-wrap';
  const bar=document.createElement('div'); bar.style.cssText='display:flex;gap:8px;align-items:center;margin-bottom:8px';
  const sel=document.createElement('select'); sel.className='coding-lang';
  sel.style.cssText='padding:6px 8px;border-radius:8px;border:1px solid var(--border,#2a2f3a);background:var(--card,#161a22);color:inherit';
  langs.forEach(l=>{ const o=document.createElement('option'); o.value=String(l).toLowerCase(); o.textContent=l; sel.appendChild(o); });
  bar.appendChild(document.createTextNode('Language: ')); bar.appendChild(sel);
  wrap.appendChild(bar);

  const edHolder=document.createElement('div'); edHolder.className='coding-editor';
  // Responsive height (was a fixed 260px) so the editor + Run/Submit + results
  // can't push the exam-finish nav (.enav / "Submit Exam") off-screen on a
  // laptop/kiosk display. The .enav is also made sticky in index.html as a backstop.
  edHolder.style.cssText='height:clamp(150px,30vh,260px);border:1px solid var(--border,#2a2f3a);border-radius:8px;overflow:hidden';
  wrap.appendChild(edHolder);
  const editor=_mountCodeEditor(edHolder, initial, sel.value, (val)=>{
    if(val && val.trim()) answers[qid]=val; else delete answers[qid];
    _persistAnswers();
    const d=document.getElementById(`qd-${curQ}`); if(d) d.className=dotClass(curQ);
  });
  edHolder.addEventListener('keydown',()=>{ const now=performance.now(); if(tele.lastKey) tele.kIntervals.push(now-tele.lastKey); tele.lastKey=now; });
  edHolder.addEventListener('paste',()=>{ tele.paste++; });

  const btns=document.createElement('div'); btns.style.cssText='display:flex;gap:8px;margin:10px 0';
  const btnCss='padding:8px 16px;border-radius:8px;border:1px solid var(--border,#2a2f3a);background:var(--card,#161a22);color:inherit;cursor:pointer;font:inherit';
  const runBtn=document.createElement('button'); runBtn.textContent='Run'; runBtn.type='button'; runBtn.style.cssText=btnCss;
  const subBtn=document.createElement('button'); subBtn.textContent='Submit'; subBtn.type='button'; subBtn.style.cssText=btnCss+';font-weight:600';
  btns.appendChild(runBtn); btns.appendChild(subBtn); wrap.appendChild(btns);

  const res=document.createElement('div'); res.className='coding-result';
  res.style.cssText='font:13px ui-monospace,Menlo,monospace;white-space:pre-wrap;line-height:1.5';
  wrap.appendChild(res); container.appendChild(wrap);

  function busy(on){ runBtn.disabled=on; subBtn.disabled=on; }

  runBtn.addEventListener('click', async ()=>{
    busy(true); res.textContent='Running sample tests…';
    try{
      const lang=sel.value, src=editor.getValue();
      const cases=await _codingFetchCases(qid); const sample=(cases&&cases.sample)||[];
      if(!sample.length){ res.textContent='No sample tests for this question.'; busy(false); return; }
      const {outputs,metrics}=await runTestCases(lang, src, sample.map(s=>s.input));
      const lines=[]; let pass=0;
      sample.forEach((c,i)=>{
        const ok=_normOut(outputs[i])===_normOut(c.expected_output); if(ok) pass++;
        const tag = metrics[i].timed_out?'⏱ timeout':(metrics[i].error?'✗ error':(ok?'✓ pass':'✗ fail'));
        lines.push(`Test ${i+1}: ${tag}`);
        lines.push(`  input:    ${JSON.stringify(c.input)}`);
        lines.push(`  expected: ${JSON.stringify(c.expected_output)}`);
        lines.push(`  actual:   ${JSON.stringify(outputs[i])}`);
        if(metrics[i].error) lines.push(`  stderr:   ${String(metrics[i].error).split('\n')[0]}`);
      });
      res.textContent=`Sample: ${pass}/${sample.length} passed\n\n`+lines.join('\n');
    }catch(e){ res.textContent='Run failed: '+(e&&e.message||e); }
    busy(false);
  });

  subBtn.addEventListener('click', async ()=>{
    busy(true); res.textContent='Submitting…';
    try{
      const lang=sel.value, src=editor.getValue();
      answers[qid]=src; _persistAnswers();
      const cases=await _codingFetchCases(qid); const hidden=(cases&&cases.hidden_inputs)||[];
      // hidden_inputs is [{idx,input}] — extract the stdin strings (same as sample).
      const {outputs,metrics}=await runTestCases(lang, src, hidden.map(h=>h.input));
      const times=metrics.map(m=>m.time_ms||0), mems=metrics.map(m=>m.mem_kb).filter(x=>x!=null);
      const body={
        session_id:sessionId, question_id:qid, language:lang, source:src, outputs:outputs,
        metrics:{
          average_execution_ms: times.length?Math.round(times.reduce((a,b)=>a+b,0)/times.length):0,
          memory_consumed_kb: mems.length?Math.round(mems.reduce((a,b)=>a+b,0)/mems.length):0
        },
        telemetry:{
          keystroke_rhythm_variance: Math.round(_variance(tele.kIntervals)*100)/100,
          paste_attempts: tele.paste, focus_loss_count: tele.focusLoss
        }
      };
      const verdict=await _codingSubmitJudge(body);
      res.textContent=`Submitted ✓  Passed ${verdict.passed}/${verdict.total}`;
    }catch(e){ res.textContent='Submit failed: '+(e&&e.message||e); }
    busy(false);
  });
}
