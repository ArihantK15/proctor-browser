const params = new URLSearchParams(location.hash.slice(1));
const token = params.get('token');
const sessionId = params.get('sid');

const preview = document.getElementById('preview');
const video = document.getElementById('cam');
const placeholder = document.getElementById('placeholder');
const statusDot = document.getElementById('status-dot');
const statusMsg = document.getElementById('status-msg');
const warningBanner = document.getElementById('warning-banner');
const targetOverlay = document.getElementById('target-overlay');

let ws = null;
let sendTimer = null;
let heartbeatTimer = null;
let wakeLock = null;
let stream = null;
let consentGiven = false;
let _stopped = false;   // set on a terminal WS close (exam ended) — no reconnect

function setStatus(state, msg){
  statusDot.className = state;
  statusMsg.textContent = msg;
}

function _consentError(html){
  const el = document.getElementById('consent-error');
  el.innerHTML = html;
  el.style.display = '';
  const btn = document.getElementById('consent-btn');
  btn.textContent = 'Try Again';
  btn.disabled = false;
}

async function startCamera(){
  const overlay = document.getElementById('consent-overlay');
  const btn = document.getElementById('consent-btn');
  document.getElementById('consent-error').style.display = 'none';
  btn.disabled = true;
  btn.textContent = 'Starting camera…';
  setStatus('connecting', 'Starting camera...');

  // getUserMedia MUST be the first async call in this tap handler:
  //  • iOS drops the user-gesture activation after an unrelated `await`, so a
  //    prior `await wakeLock.request()` would suppress the camera permission
  //    prompt; and
  //  • wakeLock.request() can HANG (never settle) in some iOS in-app browsers,
  //    blocking the whole flow so the camera never starts.
  // So we request the camera immediately, then do wakeLock fire-and-forget.
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    _consentError('This browser can’t reach the camera (no mediaDevices API). Open <b>app.procta.net/phone-cam</b> in <b>Safari</b> directly and scan the QR again.');
    return;
  }
  try{
    // Race against a hang: in some iOS webviews getUserMedia never settles, so
    // the page would freeze with no feedback. Surface that as a real message.
    const gum = navigator.mediaDevices.getUserMedia({
      video: {facingMode: {ideal: 'user'}, width: {ideal: 640}, height: {ideal: 480}},
      audio: false,
    });
    stream = await Promise.race([
      gum,
      new Promise((_, rej) => setTimeout(() => rej(Object.assign(new Error('timeout'), {name:'TimeoutError'})), 12000)),
    ]);
  }catch(e){
    const name = (e && e.name) || '';
    let msg;
    if(name === 'NotAllowedError' || name === 'SecurityError'){
      msg = '<b>Camera permission is blocked for this site.</b><br>Tap the <b>“aA”</b> (or ⋯) button at the left of the address bar → <b>Website Settings</b> → set <b>Camera</b> to <b>Allow</b> → then tap <b>Try Again</b>. If there’s no such option, you’re in an in-app browser — tap the share icon → <b>Open in Safari</b> and scan again.';
    } else if(name === 'TimeoutError'){
      msg = 'The camera didn’t respond. Make sure no other app is using it, then tap <b>Try Again</b>. If it keeps failing, open this page in <b>Safari</b> directly.';
    } else if(name === 'NotFoundError' || name === 'OverconstrainedError'){
      msg = 'No usable camera was found on this phone.';
    } else if(name === 'NotReadableError'){
      msg = 'Your camera is busy in another app. Close that app, then tap <b>Try Again</b>.';
    } else {
      msg = 'Couldn’t start the camera (<b>' + (name || 'unknown error') + '</b>). Open this page in <b>Safari</b> and scan again.';
    }
    _consentError(msg);
    return;   // keep the consent overlay up so they can read it + retry
  }

  // Camera is live — commit the UI.
  overlay.style.display = 'none';
  consentGiven = true;
  video.srcObject = stream;
  video.style.display = '';
  placeholder.style.display = 'none';
  targetOverlay.style.display = '';
  try{ await video.play(); }catch(_){ /* autoplay+muted+playsinline should allow it */ }

  // WakeLock AFTER the camera is up — fire-and-forget so it can NEVER block or
  // hang the capture flow (its behaviour is unreliable in iOS webviews).
  if('wakeLock' in navigator){
    navigator.wakeLock.request('screen')
      .then(wl => { wakeLock = wl; })
      .catch(() => {
        warningBanner.textContent = '⚠️ Keep this screen on — disable auto-lock so the camera stays active.';
        warningBanner.style.display = '';
      });
  }

  setStatus('connecting', 'Connecting to server...');
  connectWs();
}

// Exponential-backoff reconnect state. Starts at 1s, doubles per
// failure up to a 30s cap, resets to 1s on each successful connect.
// Matches the pattern used by the student chat WS in renderer/index.html
// (chatScheduleReconnect). Audit M10 — was a hardcoded 3s retry that
// would hammer the server during sustained outages.
let wsReconnectDelay = 1000;
let wsReconnectTimer = null;

function _scheduleWsReconnect(){
  if(wsReconnectTimer || _stopped) return;
  const d = Math.min(wsReconnectDelay, 30000);
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
    if(consentGiven && !_stopped) connectWs();
  }, d);
}

function connectWs(){
  if(!token || !sessionId){
    setStatus('error', 'Invalid QR code — please scan again from the exam screen.');
    return;
  }
  const proto = (location.protocol==='https:')?'wss:':'ws:';
  const host = location.host;
  ws = new WebSocket(`${proto}//${host}/ws/v1/room-frame/${encodeURIComponent(sessionId)}`, [`bearer.${token}`]);

  ws.onopen = () => {
    setStatus('connected', 'Connected ✓ — position the phone, then return to your main device.');
    warningBanner.textContent = '👉 After positioning, return to your main (exam) device — it continues automatically once your teacher approves.';
    warningBanner.style.display = '';
    targetOverlay.style.display = '';
    // Successful open — reset backoff so future failures restart from 1s.
    wsReconnectDelay = 1000;

    // Start sending frames
    sendTimer = setInterval(sendFrame, 1000);
    heartbeatTimer = setInterval(sendHeartbeat, 8000);
  };

  ws.onclose = (e) => {
    if(sendTimer){ clearInterval(sendTimer); sendTimer = null; }
    if(heartbeatTimer){ clearInterval(heartbeatTimer); heartbeatTimer = null; }
    const code = e && e.code;
    // Terminal closes — the exam is over (4004) or this phone was reconnected
    // elsewhere (4000). Stop streaming and do NOT reconnect.
    if(code === 4004){
      _stopped = true;
      if(stream) stream.getTracks().forEach(t => t.stop());
      warningBanner.style.display = 'none';
      setStatus('inactive', '✅ Exam ended — room camera stopped. You can close this page.');
      return;
    }
    if(code === 4000){
      _stopped = true;
      setStatus('inactive', 'Reconnected on another tab/device — this one stopped.');
      return;
    }
    setStatus('connecting', `Disconnected${code?` (code ${code})`:''} — reconnecting in ${Math.round(Math.min(wsReconnectDelay, 30000)/1000)}s...`);
    _scheduleWsReconnect();
  };

  ws.onerror = () => {
    setStatus('error', 'Connection error. Check your network.');
    // onclose follows; reconnect scheduled there.
  };
}

function sendFrame(){
  if(!ws || ws.readyState !== WebSocket.OPEN) return;
  const vw = video.videoWidth, vh = video.videoHeight;
  if(!vw || !vh) return;   // stream not ready yet

  // iOS draws the camera's INTRINSIC (unrotated) buffer to canvas, ignoring
  // the display orientation — so the old fixed 320x240 canvas squished portrait
  // and flipped landscape upside-down. Capture at the video's natural aspect
  // (scaled down for bandwidth) and rotate by the device orientation so the
  // teacher always gets an upright frame regardless of how the phone is held.
  const MAX = 480;
  const scale = Math.min(1, MAX / Math.max(vw, vh));
  const w = Math.max(1, Math.round(vw * scale));
  const h = Math.max(1, Math.round(vh * scale));

  let angle = 0;
  try {
    angle = (screen.orientation && typeof screen.orientation.angle === 'number')
      ? screen.orientation.angle
      : (typeof window.orientation === 'number' ? window.orientation : 0);
  } catch(_) {}
  angle = ((angle % 360) + 360) % 360;

  const canvas = document.createElement('canvas');
  // 90°/270° swap width<->height.
  if(angle === 90 || angle === 270){ canvas.width = h; canvas.height = w; }
  else { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext('2d');
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate(angle * Math.PI / 180);
  ctx.drawImage(video, -w / 2, -h / 2, w, h);

  canvas.toBlob(blob => {
    if(blob && ws.readyState === WebSocket.OPEN){
      ws.send(blob);
    }
  }, 'image/jpeg', 0.5);
}

function sendHeartbeat(){
  if(ws && ws.readyState === WebSocket.OPEN){
    ws.send(JSON.stringify({type: 'heartbeat'}));
  }
}

// Handle WakeLock release
document.addEventListener('visibilitychange', async () => {
  if(document.visibilityState === 'visible' && consentGiven && !wakeLock){
    try{
      if('wakeLock' in navigator){
        wakeLock = await navigator.wakeLock.request('screen');
      }
    }catch(e){}
  }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
  if(sendTimer) clearInterval(sendTimer);
  if(heartbeatTimer) clearInterval(heartbeatTimer);
  if(ws) ws.close();
  if(stream) stream.getTracks().forEach(t => t.stop());
});

// If token and sessionId are in URL, auto-start (after consent)
if(token && sessionId && !consentGiven){
  // Consent overlay is shown — user clicks button to proceed
}

// CSP-safe binding — replaces the former inline onclick="startCamera()".
// The page is served with a header CSP of `script-src 'self'` (no
// 'unsafe-inline'), which blocks inline scripts AND inline event
// handlers in Safari, so the consent button must be wired here.
document.getElementById('consent-btn').addEventListener('click', startCamera);
