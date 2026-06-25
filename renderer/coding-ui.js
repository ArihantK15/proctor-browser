/*
 * coding-ui.js — kiosk coding-question UI.
 * Loaded same-origin in renderer/index.html <head>; defines global helpers
 * (_renderCodingQuestion etc.) called from renderQ()'s `coding` branch.
 * References shared globals (answers, SERVER, authHdr, sessionId,
 * _persistAnswers, dotClass, curQ, questions, qNormType) at call time — all
 * classic scripts on the page share one global scope.
 *
 * Code EXECUTES SERVER-SIDE (see
 * docs/superpowers/specs/2026-06-23-server-side-coding-execution-design.md): the
 * kiosk only edits and POSTs source. Run -> /api/v1/coding/run (sample cases),
 * Submit -> /api/v1/coding/judge (hidden cases, graded); the server runs the
 * code in its sandbox and returns per-case results + the verdict. This file
 * never executes student code.
 *
 * UX goal (see docs/CODING_UI_FINDINGS.md): HackerRank/LeetCode-grade — rendered
 * problem statement, per-test-case result cards, an Accepted/Wrong-Answer verdict.
 */
const _codingTele = {}; // per-qid telemetry: {paste,focusLoss,kIntervals[],lastKey}
const _codingSubmits = {}; // per-qid client-side submit counter (shown to student)
let _codingBlurWired = false;
let _codingStylesInjected = false;
// Friendly labels for the language picker (value stays the canonical key the
// server/runner use; only the displayed text is prettified).
const _LANG_LABEL = {javascript:'JavaScript', typescript:'TypeScript', python:'Python',
                     c:'C', cpp:'C++', java:'Java'};

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
  let langs=opt.allowed_languages||opt.languages||opt.language||['javascript'];
  if(typeof langs==='string') langs=[langs];
  return langs.length?langs:['javascript'];
}
// starter_code is a {lang: code} map (per-language templates); older questions
// stored a single string. Resolve the template for one language, handling both.
function _starterFor(opt, lang){
  const sc=opt&&opt.starter_code;
  const key=String(lang||'').toLowerCase();
  if(sc&&typeof sc==='object') return String(sc[key]||sc[lang]||'');
  return String(sc||opt.starter||'');
}
// ── minimal, safe markdown (escape first, then a few block/inline rules) ──────
function _mdEscape(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function _mdToHtml(md){
  const src=String(md==null?'':md);
  // Pull fenced code blocks out first so their contents aren't markdown-processed.
  const blocks=[];
  let work=src.replace(/```[\w]*\n?([\s\S]*?)```/g,(_,code)=>{
    blocks.push('<pre class="cmd-pre"><code>'+_mdEscape(code.replace(/\n$/,''))+'</code></pre>');
    return '\n\u0000B'+(blocks.length-1)+'\u0000\n';
  });
  work=_mdEscape(work);
  work=work.replace(/^#{4,6}\s?(.*)$/gm,'<h4>$1</h4>')
           .replace(/^###\s?(.*)$/gm,'<h4>$1</h4>')
           .replace(/^##\s?(.*)$/gm,'<h3>$1</h3>')
           .replace(/^#\s?(.*)$/gm,'<h3>$1</h3>');
  work=work.replace(/`([^`]+)`/g,'<code>$1</code>')
           .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  work=work.replace(/(?:^|\n)((?:[-*]\s.*(?:\n|$))+)/g,(m,items)=>{
    const lis=items.trim().split('\n').map(li=>'<li>'+li.replace(/^[-*]\s/,'')+'</li>').join('');
    return '\n<ul>'+lis+'</ul>\n';
  });
  work=work.split(/\n{2,}/).map(chunk=>{
    const c=chunk.trim();
    if(!c) return '';
    if(/^<(h\d|ul|ol|pre|blockquote)/.test(c)) return c;
    if(/^\u0000B\d+\u0000$/.test(c)) return c;
    return '<p>'+c.replace(/\n/g,'<br>')+'</p>';
  }).join('\n');
  work=work.replace(/\u0000B(\d+)\u0000/g,(_,i)=>blocks[+i]||'');
  return work;
}

function _injectCodingStyles(){
  if(_codingStylesInjected) return; _codingStylesInjected=true;
  const css=`
  .coding-wrap{display:flex;flex-direction:column;gap:12px}
  .cmeta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;color:var(--text-muted,#8b949e)}
  .cmeta .cpill{padding:3px 9px;border:1px solid var(--border,#2a2f3a);border-radius:999px;background:var(--surface-1,#161a22)}
  .cbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .cbar select{padding:6px 10px;border-radius:8px;border:1px solid var(--border,#2a2f3a);background:var(--surface-1,#161a22);color:inherit;font:inherit}
  .coding-editor{height:clamp(300px,48vh,560px);border:1px solid var(--border,#2a2f3a);border-radius:10px;overflow:hidden}
  .cbtns{display:flex;gap:10px;align-items:center}
  .cbtn{display:inline-flex;align-items:center;gap:7px;padding:9px 18px;border-radius:8px;border:1px solid var(--border,#2a2f3a);
        background:var(--surface-1,#161a22);color:var(--text-high,#e6edf3);cursor:pointer;font:inherit;font-weight:500;transition:filter .12s ease,opacity .12s ease}
  .cbtn:hover:not(:disabled){filter:brightness(1.15)} .cbtn:disabled{opacity:.55;cursor:default}
  .cbtn.submit{background:var(--accent,#238636);border-color:var(--accent,#238636);color:#fff;font-weight:600}
  .cbtn.ghost{border:0;background:transparent;color:var(--text-muted,#8b949e);padding:9px 8px}
  .cbtn .spin{width:13px;height:13px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:cspin .7s linear infinite}
  @keyframes cspin{to{transform:rotate(360deg)}}
  .cconsole{border:1px solid var(--border,#2a2f3a);border-radius:10px;background:var(--surface-1,#161a22);overflow:hidden}
  .cconsole-hd{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border,#2a2f3a);font-size:12px;font-weight:600;color:var(--text-muted,#8b949e)}
  .cconsole-body{padding:10px 12px;max-height:38vh;overflow:auto;display:flex;flex-direction:column;gap:10px}
  .cverdict{display:flex;flex-wrap:wrap;align-items:center;gap:10px;font-size:15px;font-weight:700}
  .cverdict.ok{color:#3fb950} .cverdict.bad{color:#f85149} .cverdict.warn{color:#d29922}
  .cverdict .sub{font-size:12px;font-weight:500;color:var(--text-muted,#8b949e)}
  .ccase{border:1px solid var(--border,#2a2f3a);border-radius:8px;overflow:hidden}
  .ccase-hd{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:12px;font-weight:600;background:rgba(255,255,255,.02)}
  .cchip{padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
  .cchip.ok{background:rgba(63,185,80,.16);color:#3fb950} .cchip.bad{background:rgba(248,81,73,.16);color:#f85149}
  .cchip.warn{background:rgba(210,153,34,.18);color:#d29922}
  .ccase-body{padding:8px 10px;display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font:12px ui-monospace,Menlo,monospace}
  .ccase-body .k{color:var(--text-muted,#8b949e)} .ccase-body pre{margin:0;white-space:pre-wrap;word-break:break-word}
  .ccase-body pre.bad{color:#f0a5a0}
  .cmd-pre{background:var(--surface-1,#161a22);border:1px solid var(--border,#2a2f3a);border-radius:8px;padding:10px;overflow:auto;font:12px ui-monospace,Menlo,monospace}
  .cmsg{font-size:13px;color:var(--text-muted,#8b949e)}`;
  const st=document.createElement('style'); st.id='coding-ui-styles'; st.textContent=css;
  document.head.appendChild(st);
}

function _mountCodeEditor(holder, doc, language, onChange){
  if(window.CMEditor && typeof window.CMEditor.create==='function'){
    return window.CMEditor.create(holder, {doc:doc||'', language:language, onChange:onChange});
  }
  const ta=document.createElement('textarea');
  ta.value=doc||''; ta.spellcheck=false;
  ta.style.cssText='width:100%;height:100%;min-height:240px;font:13px ui-monospace,Menlo,monospace;padding:10px;border:0;background:var(--surface-1,#161a22);color:inherit;resize:none;box-sizing:border-box';
  ta.addEventListener('input',()=>onChange&&onChange(ta.value));
  holder.appendChild(ta);
  return {getValue:()=>ta.value, setValue:v=>{ta.value=v;}, focus:()=>ta.focus(), destroy:()=>ta.remove()};
}

// Run sample cases server-side. The server executes the source against the
// (public) sample cases in its sandbox and returns per-case results — the kiosk
// never runs code or sees hidden expecteds.
async function _codingRunSample(body){
  const r=await fetch(`${SERVER}/api/v1/coding/run`,{method:'POST',headers:authHdr(),body:JSON.stringify(body)});
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json(); // {cases:[{input,expected_output,output,status,time_ms,error}], passed, total}
}

let _codingJudgeQueue=[]; let _codingJudgeRetryTimer=null;
async function _codingSubmitJudge(body){
  let r;
  try{ r=await fetch(`${SERVER}/api/v1/coding/judge`,{method:'POST',headers:authHdr(),body:JSON.stringify(body)}); }
  catch(netErr){ _codingJudgeQueue.push(body); _codingScheduleJudgeRetry(); const e=new Error('offline — queued for retry'); e.code=0; throw e; }
  if(r.status===429){ const e=new Error('limit'); e.code=429; throw e; }
  if(!r.ok){ const e=new Error('HTTP '+r.status); e.code=r.status; throw e; }
  return r.json(); // {passed,total}
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

function _caseStatus(metric, ok){
  if(metric && metric.timed_out) return {chip:'warn', label:'Timeout'};
  if(metric && metric.error) return {chip:'bad', label:'Runtime Error'};
  return ok ? {chip:'ok', label:'Passed'} : {chip:'bad', label:'Wrong Answer'};
}
function _caseCard(n, input, expected, actual, metric, ok){
  const st=_caseStatus(metric, ok);
  const card=document.createElement('div'); card.className='ccase';
  const ms=(metric&&metric.time_ms!=null)?`${Math.round(metric.time_ms)} ms`:'';
  const hd=document.createElement('div'); hd.className='ccase-hd';
  hd.innerHTML=`<span class="cchip ${st.chip}">${st.label}</span><span>Test ${n}</span>`+
    `<span style="margin-left:auto;color:var(--text-muted,#8b949e);font-weight:400">${ms}</span>`;
  card.appendChild(hd);
  const body=document.createElement('div'); body.className='ccase-body';
  const row=(k,v,bad)=>{ const kk=document.createElement('div'); kk.className='k'; kk.textContent=k;
    const pre=document.createElement('pre'); if(bad) pre.className='bad';
    pre.textContent=(v==null||v==='')?'(empty)':String(v);
    body.appendChild(kk); body.appendChild(pre); };
  row('Input', input);
  if(expected!==undefined) row('Expected', expected);
  row('Your output', (metric&&metric.error)?String(metric.error).split('\n')[0]:actual, !ok);
  card.appendChild(body); return card;
}

function _renderCodingQuestion(container, q){
  _wireCodingBlur(); _injectCodingStyles();
  const qid=q.id, tele=_teleFor(qid), langs=_langsFor(q), opt=q.options||{};
  const _firstLang=String(langs[0]||'javascript').toLowerCase();
  const starter=_starterFor(opt, _firstLang);
  const initial = answers[qid]!=null ? String(answers[qid]) : starter;
  if(answers[qid]==null && starter){ answers[qid]=starter; }

  // Rendered problem statement (was raw markdown in #qtxt).
  const qtxt=document.getElementById('qtxt');
  if(qtxt){ qtxt.innerHTML=_mdToHtml(q.question||''); }

  const wrap=document.createElement('div'); wrap.className='coding-wrap';

  const meta=document.createElement('div'); meta.className='cmeta';
  const pill=(t)=>{ const s=document.createElement('span'); s.className='cpill'; s.textContent=t; meta.appendChild(s); };
  if(opt.marks!=null) pill(`${opt.marks} mark${opt.marks==1?'':'s'}`);
  if(opt.marks_policy) pill(opt.marks_policy==='all_or_nothing'?'All-or-nothing':'Partial credit');
  if(opt.time_limit_ms) pill(`${+(opt.time_limit_ms/1000).toFixed(1)}s / test`);
  pill(langs.length===1?String(langs[0]):`${langs.length} languages`);
  wrap.appendChild(meta);

  const bar=document.createElement('div'); bar.className='cbar';
  const sel=document.createElement('select');
  langs.forEach(l=>{ const o=document.createElement('option'); const lk=String(l).toLowerCase(); o.value=lk; o.textContent=_LANG_LABEL[lk]||l; sel.appendChild(o); });
  bar.appendChild(document.createTextNode('Language')); bar.appendChild(sel);
  const resetBtn=document.createElement('button'); resetBtn.type='button'; resetBtn.className='cbtn ghost'; resetBtn.textContent='Reset to starter';
  bar.appendChild(resetBtn);
  wrap.appendChild(bar);

  const edHolder=document.createElement('div'); edHolder.className='coding-editor'; wrap.appendChild(edHolder);
  let editor;
  const onChange=(val)=>{ if(val && val.trim()) answers[qid]=val; else delete answers[qid];
    _persistAnswers(); const d=document.getElementById(`qd-${curQ}`); if(d) d.className=dotClass(curQ); };
  function buildEditor(doc){
    if(editor && editor.destroy) editor.destroy();
    edHolder.innerHTML='';
    editor=_mountCodeEditor(edHolder, doc, sel.value, onChange);
  }

  const btns=document.createElement('div'); btns.className='cbtns';
  const runBtn=document.createElement('button'); runBtn.type='button'; runBtn.className='cbtn run';
  runBtn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Run';
  const subBtn=document.createElement('button'); subBtn.type='button'; subBtn.className='cbtn submit'; subBtn.textContent='Submit';
  const hint=document.createElement('span'); hint.style.cssText='font-size:12px;color:var(--text-muted,#8b949e);margin-left:auto';
  hint.textContent='Run = sample tests · Submit = graded (hidden tests)';
  btns.appendChild(runBtn); btns.appendChild(subBtn); btns.appendChild(hint); wrap.appendChild(btns);

  buildEditor(initial);
  // Telemetry + Ctrl/Cmd+Enter-to-Run: attach ONCE to the persistent holder. The
  // editor children rebuild on each language switch / Reset, but edHolder itself
  // does not — attaching inside buildEditor would stack a fresh listener per
  // rebuild, inflating paste counts and multi-firing Run.
  edHolder.addEventListener('keydown',(e)=>{
    const now=performance.now(); if(tele.lastKey) tele.kIntervals.push(now-tele.lastKey); tele.lastKey=now;
    if((e.ctrlKey||e.metaKey) && e.key==='Enter'){ e.preventDefault(); runBtn.click(); }
  });
  edHolder.addEventListener('paste',()=>{ tele.paste++; });
  sel.dataset.prevLang=_firstLang;
  sel.addEventListener('change',()=>{
    const cur=editor.getValue();
    const prevStarter=_starterFor(opt, sel.dataset.prevLang);
    // Load the new language's starter only if the student hasn't typed over the
    // previous one — never clobber their code.
    const next=(cur.trim()===''||cur===prevStarter)?_starterFor(opt, sel.value):cur;
    sel.dataset.prevLang=sel.value;
    buildEditor(next);
    if(next!==cur) onChange(next);
  });
  resetBtn.addEventListener('click',()=>{ const s=_starterFor(opt, sel.value); buildEditor(s); onChange(s); });

  const cons=document.createElement('div'); cons.className='cconsole'; cons.style.display='none';
  const consHd=document.createElement('div'); consHd.className='cconsole-hd'; consHd.textContent='Results';
  const consBody=document.createElement('div'); consBody.className='cconsole-body';
  cons.appendChild(consHd); cons.appendChild(consBody); wrap.appendChild(cons);
  container.appendChild(wrap);

  function showConsole(){ cons.style.display='block'; }
  function setBusy(btn, on, label){
    runBtn.disabled=on; subBtn.disabled=on;
    if(on){ btn.dataset.html=btn.innerHTML; btn.innerHTML='<span class="spin"></span> '+(label||'Working…'); }
    else if(btn.dataset.html!=null){ btn.innerHTML=btn.dataset.html; delete btn.dataset.html; }
  }
  function msg(t){ consBody.innerHTML=''; const d=document.createElement('div'); d.className='cmsg'; d.textContent=t; consBody.appendChild(d); }

  runBtn.addEventListener('click', async ()=>{
    showConsole(); setBusy(runBtn,true,'Running…'); msg('Running sample tests on the server…');
    try{
      const lang=sel.value, src=editor.getValue();
      answers[qid]=src; _persistAnswers();
      const res=await _codingRunSample({session_id:sessionId, question_id:qid, language:lang, source:src});
      const cases=(res&&res.cases)||[];
      if(!cases.length){ msg('No sample tests for this question — use Submit to grade against the hidden tests.'); return; }
      consBody.innerHTML='';
      const head=document.createElement('div'); consBody.appendChild(head);
      cases.forEach((c,i)=>{
        const ok=c.status==='passed';
        const metric={time_ms:c.time_ms, error:c.error, timed_out:c.status==='timeout'};
        consBody.appendChild(_caseCard(i+1, c.input, c.expected_output, c.output, metric, ok));
      });
      const passed=res.passed!=null?res.passed:cases.filter(c=>c.status==='passed').length;
      const allOk=passed===cases.length;
      head.className='cverdict '+(allOk?'ok':'bad');
      head.innerHTML=`${allOk?'✓':'✗'} Sample tests: ${passed}/${cases.length} passed`+
        `<span class="sub">visible examples — Submit runs the hidden graded tests</span>`;
    }catch(e){ msg('Run failed: '+(e&&e.message||e)); }
    finally{ setBusy(runBtn,false); }
  });

  subBtn.addEventListener('click', async ()=>{
    showConsole(); setBusy(subBtn,true,'Submitting…');
    msg('Submitted — running the hidden tests on the server. This can take a few seconds, please wait…');
    try{
      const lang=sel.value, src=editor.getValue();
      answers[qid]=src; _persistAnswers();
      // Server runs the hidden cases in its sandbox and grades; the client sends
      // only source + behavioural telemetry (no client-computed outputs/metrics).
      const body={
        session_id:sessionId, question_id:qid, language:lang, source:src,
        telemetry:{
          keystroke_rhythm_variance: Math.round(_variance(tele.kIntervals)*100)/100,
          paste_attempts: tele.paste, focus_loss_count: tele.focusLoss
        }
      };
      const verdict=await _codingSubmitJudge(body); // {passed,total,average_execution_ms?}
      _codingSubmits[qid]=(_codingSubmits[qid]||0)+1;
      const accepted=verdict.total>0 && verdict.passed===verdict.total;
      const avg=verdict.average_execution_ms;
      consBody.innerHTML='';
      const v=document.createElement('div'); v.className='cverdict '+(accepted?'ok':'bad');
      v.innerHTML=`${accepted?'✓ Accepted':'✗ Wrong Answer'} `+
        `<span class="sub">${verdict.passed}/${verdict.total} hidden tests`+
        `${avg?` · avg ${Math.round(avg)} ms`:''} · submission ${_codingSubmits[qid]}</span>`;
      consBody.appendChild(v);
      const note=document.createElement('div'); note.className='cmsg';
      note.textContent=accepted?'All hidden tests passed. Your final answer is saved.'
        :'Some hidden tests failed. Revise and Submit again (submissions are limited).';
      consBody.appendChild(note);
    }catch(e){
      if(e&&e.code===429){ msg('Submission limit reached for this problem. Your last graded result stands.'); }
      else if(e&&e.code===0){ msg('You appear offline — your submission is queued and will retry automatically.'); }
      else{ msg('Submit failed: '+(e&&e.message||e)); }
    }
    finally{ setBusy(subBtn,false); }
  });
}
