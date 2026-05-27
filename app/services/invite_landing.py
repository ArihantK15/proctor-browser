from ..utils import _html_escape

_INVITE_CSS = """\
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0f172a;color:#e2e8f0;min-height:100vh;padding:24px}
.wrap{max-width:640px;margin:0 auto}
.hero{background:linear-gradient(135deg,#10b981,#3b82f6);border-radius:20px;padding:36px;margin-bottom:16px}
.brand{color:#fff;font-size:12px;letter-spacing:2px;font-weight:700;opacity:.9}
.title{color:#fff;font-size:28px;font-weight:700;margin-top:8px;line-height:1.2}
.subtitle{color:#e0f2fe;font-size:15px;margin-top:8px}
.card{background:#1e293b;border-radius:16px;padding:24px;border:1px solid #334155;margin-bottom:16px}
h2{margin:0 0 16px 0;font-size:16px;color:#e2e8f0;font-weight:600}
.field{margin:12px 0}
.lbl{font-size:12px;color:#94a3b8;margin-bottom:4px}
.val{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
code{background:#0f172a;padding:6px 12px;border-radius:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:15px;color:#10b981;font-weight:600;border:1px solid #334155}
.copy{background:#334155;color:#e2e8f0;border:none;padding:6px 10px;border-radius:6px;
cursor:pointer;font-size:12px;font-weight:600}
.copy:hover{background:#475569}
.copy.ok{background:#10b981}
.meta{font-size:13px;color:#94a3b8;margin:6px 0}
.dlbtn{display:inline-block;background:#10b981;color:#fff;text-decoration:none;padding:14px 28px;
border-radius:10px;font-weight:600;margin:8px 4px 8px 0;transition:transform .1s}
.dlbtn:hover{transform:translateY(-1px)}
.dlbtn.alt{background:#475569}
.step{counter-increment:step;display:flex;gap:12px;align-items:flex-start;margin:14px 0}
.step::before{content:counter(step);flex:0 0 28px;height:28px;border-radius:50%;background:#10b981;
color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:14px}
.steps{counter-reset:step;padding:0}
.step-body{flex:1}
.step-title{font-weight:600;color:#e2e8f0;margin-bottom:2px}
.step-desc{font-size:13px;color:#94a3b8;line-height:1.5}
.notice{display:flex;gap:14px;align-items:flex-start;background:rgba(245,158,11,.08);
border:1px solid rgba(245,158,11,.35);border-radius:14px;padding:16px 20px;margin-bottom:16px}
.notice .icon{flex:0 0 28px;height:28px;border-radius:50%;background:#f59e0b;color:#1f2937;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:16px}
.notice .body{flex:1}
.notice .t{color:#fbbf24;font-weight:700;font-size:14px;margin-bottom:4px;letter-spacing:.02em}
.notice .d{color:#fde68a;font-size:13px;line-height:1.55}
footer{text-align:center;color:#64748b;font-size:12px;margin-top:20px}"""

_INVITE_JS = """\
(function(){
  var ua = (navigator.userAgent || '').toLowerCase();
  var btn = document.getElementById('primary-dl');
  if(!btn) return;
  if(ua.indexOf('mac') !== -1){
    btn.href = '/download/mac';
    btn.textContent = 'Download for macOS';
  } else if(ua.indexOf('win') !== -1){
    btn.href = '/download/win';
    btn.textContent = 'Download for Windows';
  } else {
    btn.textContent = 'Download installer';
  }
})();
function copyVal(v, btn){
  navigator.clipboard.writeText(v).then(function(){
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('ok');
    setTimeout(function(){ btn.textContent = orig; btn.classList.remove('ok'); }, 1500);
  });
}
function openInApp(e){
  var btn = document.getElementById('open-in-app');
  var token = btn ? btn.getAttribute('data-token') : '';
  if(!token) return;
  var launched = false;
  function markLaunched(){ launched = true; }
  window.addEventListener('blur', markLaunched, {once:true});
  document.addEventListener('visibilitychange', function h(){
    if(document.hidden) markLaunched();
  }, {once:true});
  var url = 'procta://invite/' + encodeURIComponent(token);
  try {
    var f = document.createElement('iframe');
    f.style.display = 'none';
    f.src = url;
    document.body.appendChild(f);
    setTimeout(function(){ try { f.remove(); } catch(_){} }, 2000);
  } catch(_) {}
}"""


def _render_invite_error(msg: str) -> str:
    safe = _html_escape(msg)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Procta invite</title>
<style>body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;
min-height:100vh;padding:24px}}
.card{{background:#1e293b;border-radius:16px;padding:40px;max-width:480px;text-align:center;
border:1px solid #334155}}
h1{{color:#f87171;margin:0 0 16px 0;font-size:24px}}
p{{color:#94a3b8;line-height:1.6;margin:0}}</style></head>
<body><div class="card"><h1>Invite unavailable</h1><p>{safe}</p></div>
<footer style="margin-top:48px;padding:16px 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid rgba(255,255,255,0.06)">
  Powered by <a href="https://procta.net" style="color:#94a3b8;text-decoration:none;font-weight:600">Procta</a>
  &nbsp;·&nbsp;
  AI-proctored exams for Indian institutions
</footer></body></html>"""


def _invite_hero(title: str, name: str) -> str:
    e = _html_escape
    return f"""<div class="hero">
    <div class="brand">PROCTA · EXAM INVITE</div>
    <div class="title">{e(title)}</div>
    <div class="subtitle">Hi {e(name)} — here's everything you need to get started.</div>
  </div>"""


def _invite_credentials(roll: str, code: str, starts: str, ends: str) -> str:
    e = _html_escape
    parts = f"""<div class="field">
      <div class="lbl">Roll number</div>
      <div class="val"><code>{e(roll)}</code>
        <button class="copy" onclick="copyVal('{e(roll)}', this)">Copy</button></div>
    </div>"""
    if code:
        parts += f"""<div class="field">
        <div class="lbl">Access code</div>
        <div class="val"><code>{e(code)}</code>
          <button class="copy" onclick="copyVal('{e(code)}', this)">Copy</button></div>
      </div>"""
    if starts:
        parts += f'<div class="meta"><b>Starts:</b> {e(starts)}</div>'
    if ends:
        parts += f'<div class="meta"><b>Closes:</b> {e(ends)}</div>'
    return f"""<div class="card">
    <h2>Your credentials</h2>
    {parts}
  </div>"""


def _invite_download() -> str:
    return """<div class="card">
    <h2>Download Procta</h2>
    <div id="dlbtns">
      <a id="primary-dl" class="dlbtn" href="/download/win">Download (detecting OS…)</a>
    </div>
    <div style="margin-top:12px;font-size:13px">
      <a class="dlbtn alt" href="/download/mac">macOS (Apple Silicon)</a>
      <a class="dlbtn alt" href="/download/mac-x64">macOS (Intel)</a>
      <a class="dlbtn alt" href="/download/win">Windows</a>
    </div>
  </div>"""


def _invite_steps(has_code: bool) -> str:
    code_line = " and access code" if has_code else ""
    return f"""<div class="card">
    <h2>How to take the exam</h2>
    <div class="steps">
      <div class="step"><div class="step-body"><div class="step-title">Install Procta</div>
        <div class="step-desc">Run the installer you just downloaded.</div></div></div>
      <div class="step"><div class="step-body"><div class="step-title">Launch and sign in</div>
        <div class="step-desc">Enter the roll number{code_line} shown above.</div></div></div>
      <div class="step"><div class="step-body"><div class="step-title">Take the exam</div>
        <div class="step-desc">When the exam window opens your questions appear.</div></div></div>
    </div>
  </div>"""


def _render_invite_landing(*, token, full_name, exam_title, roll_number, access_code, starts_at, ends_at) -> str:
    e = _html_escape
    hero = _invite_hero(exam_title, full_name)
    creds = _invite_credentials(roll_number, access_code, starts_at or "", ends_at or "")
    dl = _invite_download()
    steps = _invite_steps(bool(access_code))
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{e(exam_title)} — Procta invite</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_INVITE_CSS}</style></head><body><div class="wrap">
  {hero}
  <div class="notice">
    <div class="icon">!</div>
    <div class="body">
      <div class="t">Desktop or laptop only</div>
      <div class="d">Procta runs as a secure desktop app on <b>Windows</b> and <b>macOS</b>.
        You can't take the exam on a phone or tablet. A mobile app is on the way —
        for now, open this invite on the computer you'll take the exam on.</div>
    </div>
  </div>
  <div class="card" id="app-launch-card" style="text-align:center">
    <h2 style="margin-bottom:6px">Already installed Procta?</h2>
    <p style="color:#94a3b8;font-size:13px;margin:0 0 14px 0">
      Skip the download — open this invite directly in your installed app.
    </p>
    <a id="open-in-app" class="dlbtn" href="#"
       onclick="openInApp(event); return false;"
       data-token="{e(token)}"
       style="background:#1e293b;border:1px solid #334155;color:#e2e8f0">
      Open in Procta app
    </a>
    <p style="color:#64748b;font-size:11px;margin:14px 0 0 0;line-height:1.5">
      Don't have it installed? Skip this and use the download buttons below.
    </p>
  </div>
  {creds}
  {dl}
  {steps}
  <footer style="margin-top:48px;padding:16px 0;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid rgba(255,255,255,0.06)">
    Powered by <a href="https://procta.net" style="color:#94a3b8;text-decoration:none;font-weight:600">Procta</a>
    &nbsp;·&nbsp;
    AI-proctored exams for Indian institutions
  </footer>
</div>
<script>{_INVITE_JS}</script>
</body></html>"""
