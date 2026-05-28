(function(){
  const ua = navigator.userAgent.toLowerCase();
  const platform = navigator.platform?.toLowerCase() || '';

  const osEl = document.getElementById('os-name');
  const detailEl = document.getElementById('os-detail');
  const mainBtn = document.getElementById('main-dl');
  const dlText = document.getElementById('dl-text');
  const dlIcon = document.getElementById('dl-icon');
  const unavail = document.getElementById('dl-unavailable');
  const macWarn = document.getElementById('mac-warn');

  let os = 'unknown';
  let arch = '';

  const isMobile = /android|iphone|ipad|ipod|mobile/i.test(ua) || (ua.includes('mac') && navigator.maxTouchPoints > 1);
  if (isMobile) {
    os = 'mobile';
  } else if (ua.includes('mac') || platform.includes('mac')) {
    os = 'mac';
    // Prefer the modern UA-CH high-entropy hint when available
    // (Chrome 90+ on Mac); otherwise fall back to the WebGL renderer
    // string; otherwise default to x64 (covers the long tail of
    // Intel Macs still in the field — audit M2). Previously this
    // defaulted to arm64 which silently served Apple-Silicon
    // installers to Intel-Mac users → install failure.
    try {
      const uaData = navigator.userAgentData;
      if (uaData && typeof uaData.getHighEntropyValues === 'function') {
        // This is async — we kick it off but don't await; the WebGL
        // path below sets arch synchronously as a fast fallback. If
        // UA-CH later returns a more accurate answer, it overrides.
        uaData.getHighEntropyValues(['architecture']).then(v => {
          if (v && v.architecture === 'arm') {
            arch = 'arm64';
          } else if (v && v.architecture === 'x86') {
            arch = 'x64';
          }
        }).catch(() => {});
      }
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl');
      if (gl && !arch) {
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '';
        if (renderer) {
          arch = renderer.toLowerCase().includes('apple') ? 'arm64' : 'x64';
        }
      }
    } catch(e) {}
    // Default to x64 when detection fails. Apple Silicon Macs that
    // somehow miss both UA-CH and WebGL hints (rare) can still
    // download the arm64 build from the explicit chip-picker link
    // shown below the main button.
    if (!arch) arch = 'x64';
  } else if (ua.includes('win')) {
    os = 'win';
  } else if (ua.includes('linux')) {
    os = 'linux';
  }

  if (os === 'mac') {
    const isArm = arch === 'arm64';
    osEl.textContent = isArm ? 'macOS (Apple Silicon)' : 'macOS (Intel)';
    detailEl.textContent = isArm
      ? 'Detected: Mac with M1/M2/M3/M4 chip'
      : 'Detected: Mac with Intel processor';
    mainBtn.href = isArm ? '/download/mac' : '/download/mac-x64';
    dlText.textContent = isArm ? 'Download for Mac (Apple Silicon)' : 'Download for Mac (Intel)';
    mainBtn.style.display = 'inline-flex';
    macWarn.style.display = 'block';
  } else if (os === 'win') {
    osEl.textContent = 'Windows';
    detailEl.textContent = 'Detected: Windows PC';
    mainBtn.href = '/download/win';
    dlText.textContent = 'Download for Windows';
    mainBtn.style.display = 'inline-flex';
  } else if (os === 'linux') {
    osEl.textContent = 'Linux';
    detailEl.textContent = 'Detected: Linux desktop';
    mainBtn.href = '/download/linux';
    dlText.textContent = 'Download for Linux';
    mainBtn.style.display = 'inline-flex';
  } else if (os === 'mobile') {
    osEl.textContent = 'Mobile Device Detected';
    detailEl.textContent = 'The Procta app requires a desktop computer (Windows, macOS, or Linux). Please switch to a laptop or desktop to download and run the exam.';
    unavail.style.display = 'block';
    unavail.textContent = 'Mobile and tablet devices are not supported.';
  } else {
    osEl.textContent = 'Unknown System';
    detailEl.textContent = 'Could not detect your operating system. Choose a download below.';
  }

  function fetchWithTimeout(url, opts={}, timeoutMs=30000){
    const ctrl = new AbortController();
    const timer = setTimeout(()=>ctrl.abort(), timeoutMs);
    return fetch(url, {...opts, signal: opts.signal || ctrl.signal}).finally(()=>clearTimeout(timer));
  }

  function checkLink(id, url) {
    fetchWithTimeout(url, {method:'HEAD'}).then(r=>{
      if(!r.ok) {
        document.getElementById(id).classList.add('disabled');
        document.getElementById(id).title = 'Not available yet';
      }
    }).catch(()=>{
      document.getElementById(id).classList.add('disabled');
    });
  }
  checkLink('dl-mac-arm', '/download/mac');
  checkLink('dl-mac-x64', '/download/mac-x64');
  checkLink('dl-win', '/download/win');
  checkLink('dl-linux', '/download/linux');

  fetchWithTimeout('/api/v1/exam-schedule').then(r=>r.json()).then(d=>{
    if(!d.starts_at && !d.ends_at) return;
    const banner = document.getElementById('exam-schedule');
    banner.style.display = 'block';
    document.getElementById('dl-sched-title').textContent = d.exam_title || 'Exam';
    // Use the user's browser timezone instead of hardcoded IST so
    // students outside India (UAE, Singapore, etc.) see exam times in
    // their local clock. Falls back to Asia/Kolkata if Intl is missing.
    const tz = (() => {
      try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; }
      catch(_) { return 'Asia/Kolkata'; }
    })();
    const opts = {timeZone: tz, month:'short', day:'numeric',
                  hour:'2-digit', minute:'2-digit', hour12:true,
                  timeZoneName:'short'};
    const locale = (navigator.language || 'en-IN');
    let times = '';
    if(d.starts_at) times += 'Starts: ' + new Date(d.starts_at).toLocaleString(locale, opts);
    if(d.ends_at) times += (times ? '  |  ' : '') + 'Ends: ' + new Date(d.ends_at).toLocaleString(locale, opts);
    if(d.duration_minutes) times += '  |  Duration: ' + d.duration_minutes + ' min';
    document.getElementById('dl-sched-times').textContent = times;
    const now = new Date();
    const warn = document.getElementById('dl-sched-warn');
    if(d.starts_at && now < new Date(d.starts_at)){
      warn.style.display = 'block';
      warn.textContent = 'The exam has not started yet. Download the app now and wait for the start time.';
    } else if(d.ends_at && now > new Date(d.ends_at)){
      warn.style.display = 'block';
      warn.textContent = 'The exam window has closed.';
    }
  }).catch(()=>{});
})();
