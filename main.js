const {
  app, BrowserWindow, ipcMain, screen,
  globalShortcut, powerSaveBlocker, clipboard, dialog
} = require('electron');
const path    = require('path');
const { spawn, spawnSync, exec } = require('child_process');
const os      = require('os');
const fs      = require('fs');
const https   = require('https');
const { autoUpdater } = require('electron-updater');

const SERVER_URL = process.env.PROCTOR_SERVER_URL || 'https://app.procta.net';
const ADMIN_CODE = process.env.EXIT_CODE || 'EXIT2026';

let mainWindow      = null;   // the exam window (kiosk) when present
let lobbyWindow     = null;   // the pre-exam web dashboard window (unlocked)
let setupWindow     = null;
let pythonProcess   = null;
let pythonShouldRun = false; // guard against restart after intentional stop
let calProcess      = null;  // calibration-mode proctor.py (streams gaze readings)
let calBiases       = null;  // {gaze_yaw, gaze_pitch, head_yaw, head_pitch} from dot calibration
let powerBlockId    = null;
let pollInterval    = null;
let studentToken    = null; // JWT issued after validate-student
// Phase 2: kiosk mode is opt-in per-session, not a global launch flag.
// `isKiosk` now reflects whether the CURRENTLY OPEN exam window is locked;
// it starts false on app launch (lobby is never locked) and is toggled
// on only when an exam window is created via the lobby bridge.
let isKiosk        = false;
const KIOSK_ALLOWED = !process.argv.includes('--no-kiosk') &&
                      process.env.PROCTOR_DEBUG !== '1';
let currentSessionId = null;  // set once an exam session is active
let examContext      = null;  // {rollNumber, accessCode, examTitle, teacherId} stashed by lobby
let resolvedPython = null;
let integrityFlags = []; // populated at startup, sent to renderer
let _integrityReady = null; // promise that resolves when checks are done
let _monitorInterval = null; // continuous process monitoring during exam

// ── AUTO-UPDATE (electron-updater) ──────────────────────────────
//
// Checks GitHub Releases for a newer version on every launch.
// Flow: check → download silently → prompt user → restart.
// All non-blocking — the lobby window is already visible.
function initAutoUpdater() {
  // Don't auto-update in dev (no packaged app)
  if (!app.isPackaged) {
    console.log('[AutoUpdate] Skipping — app not packaged (dev mode)');
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('checking-for-update', () => {
    console.log('[AutoUpdate] Checking for updates...');
  });

  autoUpdater.on('update-available', (info) => {
    console.log(`[AutoUpdate] Update available: v${info.version}`);
    // Notify user via lobby window
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      lobbyWindow.webContents.executeJavaScript(
        `if(document.getElementById('update-banner')){document.getElementById('update-banner').style.display='flex'}` +
        `else{var b=document.createElement('div');b.id='update-banner';` +
        `b.style.cssText='position:fixed;top:0;left:0;right:0;padding:10px 20px;background:#1a73e8;color:#fff;font-size:14px;font-family:system-ui;display:flex;align-items:center;justify-content:center;gap:8px;z-index:99999;';` +
        `b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Downloading update v${info.version}...';` +
        `document.body.prepend(b)}`
      ).catch(() => {});
    }
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[AutoUpdate] App is up to date');
  });

  autoUpdater.on('download-progress', (progress) => {
    const pct = Math.round(progress.percent);
    console.log(`[AutoUpdate] Download: ${pct}%`);
    if (lobbyWindow && !lobbyWindow.isDestroyed()) {
      lobbyWindow.webContents.executeJavaScript(
        `var b=document.getElementById('update-banner');if(b)b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Downloading update... ${pct}%'`
      ).catch(() => {});
    }
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log(`[AutoUpdate] Update downloaded: v${info.version}`);
    // Show restart prompt — only if NOT in an active exam
    if (mainWindow && !mainWindow.isDestroyed()) {
      // Exam is active — install on next quit
      console.log('[AutoUpdate] Exam in progress — update will install on quit');
      if (lobbyWindow && !lobbyWindow.isDestroyed()) {
        lobbyWindow.webContents.executeJavaScript(
          `var b=document.getElementById('update-banner');if(b){b.style.background='#34a853';b.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9 12l2 2 4-4"/></svg> Update ready — will install when you close the app'}`
        ).catch(() => {});
      }
    } else {
      // No exam active — prompt restart
      dialog.showMessageBox(lobbyWindow || null, {
        type: 'info',
        title: 'Update Ready',
        message: `Procta Browser v${info.version} has been downloaded.`,
        detail: 'The app will restart to apply the update.',
        buttons: ['Restart Now', 'Later'],
        defaultId: 0
      }).then(({ response }) => {
        if (response === 0) {
          autoUpdater.quitAndInstall(false, true);
        }
      });
    }
  });

  autoUpdater.on('error', (err) => {
    console.error('[AutoUpdate] Error:', err.message);
  });

  // Fire the check
  autoUpdater.checkForUpdates().catch(err => {
    console.error('[AutoUpdate] Check failed:', err.message);
  });
}

// ── VM / INTEGRITY CHECKS (fully async — never blocks UI) ───────
//
// Every shell command uses child_process.exec (async) instead of
// execSync. On Windows this eliminates the 20-40 second startup freeze
// caused by `systeminfo` + 3x `tasklist` + `reg query` all running
// synchronously before the window was created.
//
// The flow is:
//   1. app.whenReady → show lobby window IMMEDIATELY
//   2. runIntegrityChecks() runs in background (async)
//   3. Results stored in `integrityFlags` for renderer to fetch

// Promise-wrapped exec helper
function _exec(cmd, timeout = 8000) {
  return new Promise(resolve => {
    exec(cmd, { encoding: 'utf8', timeout }, (err, stdout) => {
      resolve(err ? '' : stdout);
    });
  });
}

async function runIntegrityChecks() {
  const flags = [];
  const isWin = process.platform === 'win32';
  const isMac = process.platform === 'darwin';

  // ── Instant checks (no shell, pure Node.js) ──────────────────

  // 1. VM Detection — MAC address prefixes
  const VM_MAC_PREFIXES = [
    '00:05:69', '00:0c:29', '00:1c:14', '00:50:56',           // VMware
    '08:00:27',                                                  // VirtualBox
    '00:15:5d',                                                  // Hyper-V
    '00:16:3e',                                                  // Xen
    '52:54:00',                                                  // QEMU/KVM
    '00:1a:4a',                                                  // Parallels
  ];
  try {
    const nets = os.networkInterfaces();
    for (const [name, addrs] of Object.entries(nets)) {
      for (const a of addrs) {
        if (a.mac && a.mac !== '00:00:00:00:00:00') {
          const prefix = a.mac.substring(0, 8).toLowerCase();
          if (VM_MAC_PREFIXES.includes(prefix)) {
            flags.push({ type: 'vm_detected', severity: 'high',
              details: `VM MAC address detected (${a.mac}, interface: ${name})` });
          }
        }
      }
    }
  } catch(e) {}

  // 2. VPN network interfaces (tun/tap/utun/ppp/wg adapters)
  //
  // FALSE-POSITIVE GUARD: macOS always creates utun0-utun4 for Apple
  // services (iCloud Private Relay, AirDrop, Handoff, Continuity). These
  // interfaces have only IPv6 link-local (fe80::, scopeid !== 0) addresses
  // — no real routable address. User VPNs (Cloudflare WARP, NordVPN,
  // WireGuard, Tailscale, …) always assign a routable IPv4 or non-link-local
  // IPv6 address. So we flag an interface only when it has at least one
  // ROUTABLE address, not merely "not internal".
  //
  // High-confidence named interfaces (tailscale0, wg0, nordlynx, proton…)
  // are still flagged unconditionally — those names never appear without
  // an active user VPN.
  try {
    const nets = os.networkInterfaces();
    const NAMED_VPN = [/^tailscale/i, /^zt[a-z0-9]/i, /^gpd\d/i,
                       /^proton/i, /^nordlynx/i, /^wg\d+$/i];
    const GENERIC_TUNNEL = [/^tun\d+$/i, /^tap\d+$/i, /^utun\d+$/i, /^ppp\d+$/i];

    const isRoutable = (a) => {
      if (!a || a.internal) return false;
      if (a.address === '127.0.0.1' || a.address === '::1') return false;
      if (a.family === 'IPv4') {
        // Exclude APIPA (link-local)
        if (a.address.startsWith('169.254.')) return false;
        return true;
      }
      if (a.family === 'IPv6') {
        // Exclude link-local (fe80::/10) — these are used by Apple
        // services on utun interfaces without any real VPN
        if (a.scopeid && a.scopeid !== 0) return false;
        const lower = a.address.toLowerCase();
        if (lower.startsWith('fe80:') || lower.startsWith('fe80::')) return false;
        return true;
      }
      return false;
    };

    for (const [name, addrs] of Object.entries(nets)) {
      // High-confidence: user-space VPNs named unambiguously
      const named = NAMED_VPN.some(p => p.test(name));
      if (named && addrs.some(a => !a.internal)) {
        flags.push({ type: 'vpn_detected', severity: 'high',
          details: `VPN interface active: ${name}` });
        continue;
      }
      // Generic tunnel: only flag if at least one ROUTABLE address is
      // assigned. This filters out Apple's always-on utun0..utun4.
      const generic = GENERIC_TUNNEL.some(p => p.test(name));
      if (generic && addrs.some(isRoutable)) {
        flags.push({ type: 'vpn_detected', severity: 'high',
          details: `Tunnel interface with routable address: ${name}` });
      }
    }
  } catch(e) {}

  // 3. Multiple monitors
  try {
    const displays = screen.getAllDisplays();
    if (displays.length > 1) {
      flags.push({ type: 'multiple_monitors', severity: 'medium',
        details: `${displays.length} displays detected` });
    }
  } catch(e) {}

  // 4. Proxy environment variables
  const PROXY_VARS = ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','SOCKS_PROXY',
                       'http_proxy','https_proxy','all_proxy','socks_proxy'];
  for (const v of PROXY_VARS) {
    if (process.env[v]) {
      flags.push({ type: 'proxy_detected', severity: 'high',
        details: `Proxy env var: ${v}=${process.env[v]}` });
      break;
    }
  }

  // 5. Electron debug flags
  if (process.argv.some(a => a.includes('--inspect') || a.includes('--remote-debugging-port'))) {
    flags.push({ type: 'debugger_detected', severity: 'high',
      details: 'Launched with debugging flags (--inspect or --remote-debugging-port)' });
  }

  // ── Async shell checks (run in parallel, never block UI) ─────

  // All process lists for VM/VPN/remote/debugger detection.
  //
  // FALSE-POSITIVE NOTES:
  //   • 'tor' as a bare substring matches storedownloadd, storekitagent,
  //     NVIDIA Container, directory, monitor, accelerator, etc. Use
  //     \btor\b with regex word boundaries instead.
  //   • Discord runs idle on most student machines without screen
  //     sharing — removed from the screen_share list. OBS is kept.
  //   • 'snx' was a 3-char substring causing false matches; dropped in
  //     favour of the more specific 'snxctl'/'snx_install' patterns.
  //
  // Each entry is a regex matched against the full process-list output
  // (lowercased). \b gives a safe word boundary that still matches
  // process basenames like "tor.exe" or "clash.exe" because '.' is
  // non-word, but won't match "stor" or "container".
  const ALL_PROCESSES = {
    vm: [
      /\bvmtoolsd\b/, /\bvmwaretray\b/, /\bvboxservice\b/, /\bvboxtray\b/,
      /\bvmcompute\b/, /\bxenservice\b/,
    ],
    remote: [
      /\bteamviewer\b/, /\banydesk\b/, /\bmstsc\b/, /\bvncviewer\b/,
      /chrome remote desktop/, /\brustdesk\b/, /\bparsec\b/,
      /\bscreenconnect\b/, /\blogmein\b/,
    ],
    screen_share: [
      /\bobs64\b/, /\bobs32\b/, /\bobs studio\b/, /\bobs\.app\b/,
      /\bscreensharingd\b/,
      // NOTE: Discord removed — running ≠ screen-sharing. OBS is a
      // better signal because it's rarely running outside content
      // creation contexts.
    ],
    vpn: [
      /\bopenvpn\b/, /\bnordvpn\b/, /\bexpressvpn\b/, /\bsurfshark\b/,
      /\bprotonvpn\b/, /\bcyberghost\b/, /\bwindscribe\b/,
      /\bprivateinternetaccess\b/, /\bpia-service\b/, /\bmullvad\b/,
      /\bwireguard\b/, /\bwg\.exe\b/, /\btailscale\b/, /\bzerotier\b/,
      /\bv2ray\b/, /\bv2rayn\b/, /\bxray\.exe\b/, /\bclash\b/,
      /\bshadowsocks\b/, /\bss-local\b/, /\btorbrowser\b/,
      /\btor\.exe\b/, /\/tor\b/, // explicit tor binary, not substring
      /\bhotspotshield\b/, /\btunnelbear\b/,
      /\bglobalprotect\b/, /\bpangps\b/, /\bforticlient\b/,
      /\bfortisslvpn\b/, /\bvpnagent\b/, /\bvpnui\b/,
      /\bcheckpoint\b/, /\bsnxctl\b/,
      /\bpsiphon\b/, /\bultrasurf\b/, /\bfreegate\b/,
    ],
    debugger: [
      /\bfiddler\b/, /\bcharles\.exe\b/, /\bwireshark\b/,
      /\bburpsuite\b/, /\bmitmproxy\b/, /\bmitmweb\b/, /\bmitmdump\b/,
      /\bproxyman\b/, /\bhttpdebugger\b/, /\bhttpanalyzer\b/,
    ],
  };

  const TYPE_MAP = {
    vm: 'vm_detected', remote: 'remote_desktop_detected',
    screen_share: 'screen_share_detected', vpn: 'vpn_detected',
    debugger: 'debugger_detected',
  };

  function scanProcessOutput(output) {
    if (!output) return;
    const lower = output.toLowerCase();
    for (const [cat, patterns] of Object.entries(ALL_PROCESSES)) {
      for (const rx of patterns) {
        const m = lower.match(rx);
        if (m) {
          flags.push({ type: TYPE_MAP[cat],
            severity: cat === 'screen_share' ? 'medium' : 'high',
            details: `Process match: ${m[0]}` });
        }
      }
    }
  }

  // Fire all async checks in parallel
  const tasks = [];

  // 6. Process list — ONE call, scan for everything
  if (isWin) {
    tasks.push(_exec('tasklist /fo csv /nh', 8000).then(scanProcessOutput));
  } else if (isMac) {
    tasks.push(_exec('ps -eo comm', 5000).then(scanProcessOutput));
  }

  // 7. VM detection via BIOS/model (replaces slow `systeminfo`)
  if (isWin) {
    // wmic is instant (<0.5s) vs systeminfo (5-15s)
    tasks.push(
      _exec('wmic computersystem get model,manufacturer /format:list', 5000).then(output => {
        if (!output) return;
        const lower = output.toLowerCase();
        const vmKw = ['vmware', 'virtualbox', 'hyper-v', 'qemu', 'xen', 'parallels', 'virtual machine'];
        for (const kw of vmKw) {
          if (lower.includes(kw)) {
            flags.push({ type: 'vm_detected', severity: 'high',
              details: `VM indicator in hardware info: ${kw}` });
            break;
          }
        }
      })
    );
    // Windows proxy registry check
    tasks.push(
      _exec('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings" /v ProxyEnable', 5000)
        .then(output => {
          if (output && /ProxyEnable\s+REG_DWORD\s+0x1/i.test(output)) {
            flags.push({ type: 'proxy_detected', severity: 'high',
              details: 'Windows system proxy is enabled' });
          }
        })
    );
  }

  if (isMac) {
    // macOS VM via CPU brand
    tasks.push(
      _exec('sysctl -n machdep.cpu.brand_string', 3000).then(output => {
        if (!output) return;
        const lower = output.toLowerCase();
        if (lower.includes('qemu') || lower.includes('virtual')) {
          flags.push({ type: 'vm_detected', severity: 'high',
            details: `Virtual CPU: ${output.trim()}` });
        }
      })
    );
    // macOS system proxy
    tasks.push(
      _exec('scutil --proxy', 3000).then(output => {
        if (!output) return;
        const httpOn = /HTTPEnable\s*:\s*1/i.test(output);
        const httpsOn = /HTTPSEnable\s*:\s*1/i.test(output);
        const socksOn = /SOCKSEnable\s*:\s*1/i.test(output);
        if (httpOn || httpsOn || socksOn) {
          const types = [];
          if (httpOn) types.push('HTTP');
          if (httpsOn) types.push('HTTPS');
          if (socksOn) types.push('SOCKS');
          flags.push({ type: 'proxy_detected', severity: 'high',
            details: `System proxy active: ${types.join(', ')}` });
        }
      })
    );
  }

  // Wait for ALL async checks (they run in parallel, ~1-2s total)
  await Promise.all(tasks);

  console.log(`[Integrity] ${flags.length} flag(s) found`);
  for (const f of flags) {
    console.log(`  [${f.severity.toUpperCase()}] ${f.type}: ${f.details}`);
  }
  return flags;
}

// ── CONTINUOUS PROCESS MONITORING ────────────────────────────────
// Runs every 30s during an active exam using async exec.
// Never blocks the main thread.

// Regex patterns with word boundaries — see notes on ALL_PROCESSES above
// for why substring matching (e.g. bare 'tor') is unsafe.
const THREATS = [
  { rx: /\bteamviewer\b/, label: 'TeamViewer', type: 'remote_desktop_detected' },
  { rx: /\banydesk\b/,    label: 'AnyDesk',    type: 'remote_desktop_detected' },
  { rx: /\bmstsc\b/,      label: 'mstsc',      type: 'remote_desktop_detected' },
  { rx: /\bvncviewer\b/,  label: 'VNC',        type: 'remote_desktop_detected' },
  { rx: /\brustdesk\b/,   label: 'RustDesk',   type: 'remote_desktop_detected' },
  { rx: /\bparsec\b/,     label: 'Parsec',     type: 'remote_desktop_detected' },
  { rx: /\bobs64\b/,      label: 'OBS (64)',   type: 'screen_share_detected' },
  { rx: /\bobs32\b/,      label: 'OBS (32)',   type: 'screen_share_detected' },
  { rx: /\bobs studio\b/, label: 'OBS Studio', type: 'screen_share_detected' },
  { rx: /\bscreensharingd\b/, label: 'screensharingd', type: 'screen_share_detected' },
  { rx: /\bopenvpn\b/,    label: 'OpenVPN',    type: 'vpn_detected' },
  { rx: /\bnordvpn\b/,    label: 'NordVPN',    type: 'vpn_detected' },
  { rx: /\bexpressvpn\b/, label: 'ExpressVPN', type: 'vpn_detected' },
  { rx: /\bsurfshark\b/,  label: 'Surfshark',  type: 'vpn_detected' },
  { rx: /\bprotonvpn\b/,  label: 'ProtonVPN',  type: 'vpn_detected' },
  { rx: /\bwireguard\b/,  label: 'WireGuard',  type: 'vpn_detected' },
  { rx: /\btailscale\b/,  label: 'Tailscale',  type: 'vpn_detected' },
  { rx: /\bclash\b/,      label: 'Clash',      type: 'vpn_detected' },
  { rx: /\bv2ray\b/,      label: 'V2Ray',      type: 'vpn_detected' },
  { rx: /\btorbrowser\b/, label: 'Tor Browser',type: 'vpn_detected' },
  { rx: /\btor\.exe\b/,   label: 'Tor',        type: 'vpn_detected' },
  { rx: /\bfiddler\b/,    label: 'Fiddler',    type: 'debugger_detected' },
  { rx: /\bcharles\.exe\b/, label: 'Charles',  type: 'debugger_detected' },
  { rx: /\bwireshark\b/,  label: 'Wireshark',  type: 'debugger_detected' },
  { rx: /\bburpsuite\b/,  label: 'Burp Suite', type: 'debugger_detected' },
  { rx: /\bmitmproxy\b/,  label: 'mitmproxy',  type: 'debugger_detected' },
  { rx: /\bproxyman\b/,   label: 'Proxyman',   type: 'debugger_detected' },
];

function startProcessMonitor() {
  if (_monitorInterval) return;
  console.log('[Monitor] starting continuous process monitoring');
  _monitorInterval = setInterval(() => _scanProcesses(), 30000);
}

function stopProcessMonitor() {
  if (_monitorInterval) {
    clearInterval(_monitorInterval);
    _monitorInterval = null;
    console.log('[Monitor] stopped');
  }
}

async function _scanProcesses() {
  const isWin = process.platform === 'win32';
  const cmd = isWin ? 'tasklist /fo csv /nh' : 'ps -eo comm';

  const output = await _exec(cmd, 8000);
  if (!output) return;
  const lower = output.toLowerCase();

  for (const { rx, label, type } of THREATS) {
    if (rx.test(lower)) {
      const flag = { type, severity: 'high',
        details: `[Live scan] ${label} detected during exam` };
      // Report to server
      if (currentSessionId && studentToken) {
        fetch(`${SERVER_URL}/event`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ session_id: currentSessionId,
            event_type: type, severity: 'high', details: flag.details }),
        }).catch(() => {});
      }
      // Push to renderer
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('violation-detected', flag);
      }
      console.log(`[Monitor] THREAT: ${label} (${type})`);
    }
  }

  // Check for new displays
  try {
    const displays = screen.getAllDisplays();
    if (displays.length > 1 && currentSessionId && studentToken) {
      fetch(`${SERVER_URL}/event`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ session_id: currentSessionId,
          event_type: 'multiple_monitors', severity: 'medium',
          details: `[Live scan] ${displays.length} displays detected` }),
      }).catch(() => {});
    }
  } catch(e) {}
}

// GPU-based VM check — must run after BrowserWindow is created
async function _checkGPU(win) {
  try {
    const renderer = await win.webContents.executeJavaScript(`
      (function() {
        try {
          const c = document.createElement('canvas');
          const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
          if (!gl) return '';
          const ext = gl.getExtension('WEBGL_debug_renderer_info');
          return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '';
        } catch(e) { return ''; }
      })()
    `);
    if (renderer) {
      const lower = renderer.toLowerCase();
      const vmRenderers = [
        'vmware', 'virtualbox', 'llvmpipe', 'swiftshader',
        'microsoft basic render', 'chromium', 'virgl',
      ];
      for (const vr of vmRenderers) {
        if (lower.includes(vr)) {
          integrityFlags.push({
            type: 'vm_detected',
            severity: 'high',
            details: `Virtual GPU renderer: ${renderer}`
          });
          console.log(`[Integrity] VM GPU detected: ${renderer}`);
          break;
        }
      }
    }
  } catch(e) { console.error('[Integrity] GPU check error:', e.message); }
}

// ── PYTHON FINDER ─────────────────────────────────────────────────
// Async version — uses _exec instead of spawnSync to avoid blocking
// the main thread. Falls back to sync fs.existsSync for file checks
// (instant) and only goes async for command probing.
async function findPython() {
  if (resolvedPython) return resolvedPython;

  const isWin = process.platform === 'win32';
  const candidates = isWin ? [
    // 1. Bundled embeddable Python shipped inside the .exe (highest priority)
    path.join(process.resourcesPath || __dirname, 'python', 'python.exe'),
    path.join(__dirname, 'resources', 'python', 'python.exe'),
    // 2. User-installed Python
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python311','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python312','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python310','python.exe'),
    'C:\\Python311\\python.exe',
    'C:\\Python312\\python.exe',
    'C:\\Python310\\python.exe',
    path.join(os.homedir(),'AppData','Local','Microsoft','WindowsApps','python3.exe'),
  ] : [
    path.join(__dirname, 'venv', 'bin', 'python3'),
    path.join(process.resourcesPath || '', 'venv', 'bin', 'python3'),
    '/usr/local/bin/python3',
    '/usr/bin/python3',
  ];

  // File existence checks — instant, no blocking
  for (const p of candidates) {
    try {
      if (p.includes('/') || p.includes('\\')) {
        if (fs.existsSync(p)) {
          resolvedPython = p;
          return p;
        }
      }
    } catch(e) {}
  }

  // Probe system commands — async to avoid blocking main thread
  for (const cmd of (isWin ? ['python','py','python3'] : ['python3','python'])) {
    const output = await _exec(`${cmd} --version`, 3000);
    if (output) {
      resolvedPython = cmd;
      return cmd;
    }
  }

  return null;
}

function getScriptPath() {
  const candidates = [
    path.join(process.resourcesPath || '', 'proctor.py'),
    path.join(__dirname, 'proctor.py'),
    path.join(app.getAppPath(), 'proctor.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(__dirname, 'proctor.py');
}

// ── CHECK IF PACKAGES READY ───────────────────────────────────────
async function checkPackagesReady(python) {
  return new Promise(resolve => {
    exec(`${python} -c "import cv2, mediapipe, ultralytics, sounddevice"`,
      { encoding: 'utf8', timeout: 10000 }, (err) => {
        resolve(!err);
      });
  });
}

// ── DOWNLOAD FILE ─────────────────────────────────────────────────
function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req  = https.get(url, res => {
      if (res.statusCode === 302 || res.statusCode === 301) {
        file.close();
        fs.unlink(dest, () => {});
        downloadFile(res.headers.location, dest)
          .then(resolve).catch(reject);
        return;
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        file.close();
        fs.unlink(dest, () => {});
        reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        res.resume(); // drain the response so the socket closes
        return;
      }
      res.pipe(file);
      file.on('finish', () => { file.close(); resolve(); });
    });
    req.on('error', err => {
      fs.unlink(dest, () => {});
      reject(err);
    });
    req.setTimeout(30000, () => {
      req.destroy();
      reject(new Error('Download timeout'));
    });
  });
}

// ── SETUP WINDOW ──────────────────────────────────────────────────
function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width:    520,
    height:   420,
    frame:    true,
    resizable: false,
    alwaysOnTop: true,
    backgroundColor: '#0d1117',
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    }
  });

  const html = `<!DOCTYPE html>
<html>
<head>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,sans-serif; background:#0d1117;
         color:#c9d1d9; display:flex; align-items:center;
         justify-content:center; height:100vh;
         flex-direction:column; padding:32px; text-align:center; }
  h2  { color:#58a6ff; margin-bottom:8px; font-size:18px; }
  p   { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .spinner { width:36px; height:36px; border:3px solid #30363d;
             border-top-color:#58a6ff; border-radius:50%;
             animation:spin 1s linear infinite; margin:0 auto 20px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .log { background:#161b22; border:1px solid #30363d;
         border-radius:8px; padding:12px; width:100%;
         max-height:200px; overflow-y:auto;
         font-size:11px; font-family:monospace;
         color:#3fb950; text-align:left; line-height:1.6; }
</style>
</head>
<body>
  <div class="spinner"></div>
  <h2>Setting Up AI Exam Environment</h2>
  <p>Installing required components.<br>This only happens once (~3 mins).</p>
  <div class="log" id="log">Starting...\n</div>
  <script>
    const { ipcRenderer } = require('electron');
    ipcRenderer.on('setup-status', (_, msg) => {
      const log = document.getElementById('log');
      log.appendChild(document.createTextNode(msg + '\n'));
      log.scrollTop = log.scrollHeight;
    });
  </script>
</body>
</html>`;

  const tmpHtml = path.join(os.tmpdir(), 'proctor_setup.html');
  fs.writeFileSync(tmpHtml, html);
  setupWindow.loadFile(tmpHtml);
  setupWindow.setMenuBarVisibility(false);
}

function sendSetupStatus(msg) {
  console.log('[Setup]', msg);
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send('setup-status', msg);
  }
}

async function runWindowsSetup() {
  let python = await findPython();

  if (!python) {
    sendSetupStatus('Python not found. Downloading Python 3.11...');
    const installerPath = path.join(os.tmpdir(), 'python_installer.exe');
    try {
      await downloadFile(
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe',
        installerPath
      );
      sendSetupStatus('Installing Python 3.11 silently...');
      // Python installer must be sync — it's a one-time operation
      const r = spawnSync(installerPath,
        ['/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1'],
        { timeout: 300000 });
      if (r.status === 0) {
        sendSetupStatus('✅ Python installed!');
        resolvedPython = null; // reset cache
        python = await findPython();
      } else {
        sendSetupStatus('⚠️ Python install failed. Trying pip packages anyway...');
        python = 'python';
      }
    } catch(e) {
      sendSetupStatus(`⚠️ Download failed: ${e.message}`);
      python = 'python';
    }
  } else {
    sendSetupStatus(`✅ Python found: ${python}`);
  }

  // Check if packages already installed
  if (await checkPackagesReady(python)) {
    sendSetupStatus('✅ All AI packages ready!');
    return true;
  }

  // Install packages — each pip install runs async to avoid blocking UI
  const packages = [
    'opencv-python',
    'mediapipe',
    'ultralytics',
    'sounddevice',
    'numpy',
    'scipy',
    'requests',
  ];
  sendSetupStatus(`Installing AI packages (~3 mins, one-time only). Do NOT close this window.`);
  const setupStart = Date.now();

  for (let idx = 0; idx < packages.length; idx++) {
    const pkg = packages[idx];
    const elapsed = Math.round((Date.now() - setupStart) / 1000);
    sendSetupStatus(`  [${idx+1}/${packages.length}] Installing ${pkg}... (${elapsed}s elapsed)`);
    try {
      const ok = await new Promise(resolve => {
        exec(`${python} -m pip install ${pkg} --quiet --no-warn-script-location`,
          { encoding: 'utf8', timeout: 120000 }, (err) => resolve(!err));
      });
      sendSetupStatus(ok ? `  ✅ ${pkg}` : `  ⚠️ ${pkg} failed`);
    } catch(e) {
      sendSetupStatus(`  ⚠️ ${pkg} error`);
    }
  }
  const totalSecs = Math.round((Date.now() - setupStart) / 1000);
  sendSetupStatus(`Setup complete in ${totalSecs}s.`);

  const ready = await checkPackagesReady(python);
  sendSetupStatus(ready ?
    '✅ All packages ready! Starting exam...' :
    '⚠️ Some packages missing — AI features may be limited');
  return ready;
}

// ── START/STOP PYTHON ─────────────────────────────────────────────
async function startPython(sessionId) {
  pythonShouldRun = true; // mark intent; stopPython() sets this to false

  const python = await findPython();
  const script = getScriptPath();

  console.log('[AI] Python:', python);
  console.log('[AI] Script:', script);
  console.log('[AI] Script exists:', fs.existsSync(script));

  if (!python) {
    console.error('[AI] No Python found — AI proctoring disabled');
    return;
  }
  if (!fs.existsSync(script)) {
    console.error('[AI] proctor.py not found');
    return;
  }

  const evidenceDir = path.join(app.getPath('userData'), 'evidence');
  try { fs.mkdirSync(evidenceDir, { recursive: true }); } catch(e) {}

  const envVars = {
    ...process.env,
    PROCTOR_SESSION_ID:              sessionId,
    PROCTOR_SERVER_URL:              `${SERVER_URL}/event`,
    PROCTOR_EVIDENCE_DIR:            evidenceDir,
    PROCTOR_JWT_TOKEN:               studentToken || '',
    PROCTOR_SKIP_ENROLLMENT:         '1',  // renderer handled the UI phase
    PROCTOR_WRONG_PERSON_THRESHOLD:  '0.25',
    PROCTOR_VOICE_THRESHOLD:         '0.035',
  };
  // Pass calibration biases from the dot-calibration step (if available).
  // Bias  = the student's personal "looking at centre" readings
  //         (already subtracted by proctor.py to zero-centre the signal).
  // Range = max deviation observed when the student looked at the 4 screen
  //         corners — lets proctor.py tune per-student off-screen thresholds
  //         instead of the one-size-fits-all GAZE_YAW_RAD=0.30 default.
  if (calBiases) {
    envVars.PROCTOR_GAZE_YAW_BIAS   = String(calBiases.gaze_yaw);
    envVars.PROCTOR_GAZE_PITCH_BIAS = String(calBiases.gaze_pitch);
    envVars.PROCTOR_HEAD_YAW_BIAS   = String(calBiases.head_yaw);
    envVars.PROCTOR_HEAD_PITCH_BIAS = String(calBiases.head_pitch);

    if (calBiases.gaze_yaw_range != null) {
      envVars.PROCTOR_GAZE_YAW_RANGE   = String(calBiases.gaze_yaw_range);
      envVars.PROCTOR_GAZE_PITCH_RANGE = String(calBiases.gaze_pitch_range);
      envVars.PROCTOR_HEAD_YAW_RANGE   = String(calBiases.head_yaw_range);
      envVars.PROCTOR_HEAD_PITCH_RANGE = String(calBiases.head_pitch_range);
    }
  }

  pythonProcess = spawn(python, [script], { env: envVars });

  pythonProcess.stdout.on('data', d =>
    console.log('[AI]', d.toString().trim()));
  pythonProcess.stderr.on('data', d =>
    console.error('[AI]', d.toString().trim()));
  pythonProcess.on('close', code => {
    console.log('[AI] Exited:', code);
    pythonProcess = null;
    if (code !== 0 && code !== null && pythonShouldRun) {
      console.log('[AI] Unexpected exit — restarting in 3s');
      setTimeout(() => startPython(sessionId), 3000);
    }
  });
  pythonProcess.on('error', err =>
    console.error('[AI] Spawn error:', err.message));
}

function stopPython() {
  pythonShouldRun = false;
  if (pythonProcess) {
    try {
      if (process.platform === 'win32') {
        // SIGTERM is ignored on Windows — use taskkill to force kill the
        // entire process tree (Python + any child cv2/mediapipe threads)
        spawnSync('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch(e) { console.error('[AI] Stop error:', e.message); }
    pythonProcess = null;
  }
}

// ── CALIBRATION MODE ────────────────────────────────────────────
// Spawns proctor.py with PROCTOR_CALIBRATION_MODE=1 so it streams
// gaze/head readings as CAL:{...} JSON lines on stdout. The renderer
// uses these to verify the student is looking at each calibration dot.
async function startCalibration(sessionId) {
  stopCalibration(); // kill any prior instance

  const python = await findPython();
  const script = getScriptPath();
  if (!python || !fs.existsSync(script)) {
    console.error('[CAL] Python or script not found — calibration unavailable');
    return;
  }

  calProcess = spawn(python, [script], {
    env: {
      ...process.env,
      PROCTOR_SESSION_ID:          sessionId,
      PROCTOR_SERVER_URL:          `${SERVER_URL}/event`,
      PROCTOR_JWT_TOKEN:           studentToken || '',
      PROCTOR_CALIBRATION_MODE:    '1',
      PROCTOR_SKIP_ENROLLMENT:     '1',
      PROCTOR_HEADLESS:            '1',
    }
  });

  calProcess.stdout.on('data', d => {
    const lines = d.toString().split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('CAL:')) {
        try {
          const reading = JSON.parse(trimmed.slice(4));
          // Forward to the exam renderer window
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('cal-reading', reading);
          }
        } catch (e) { /* malformed JSON — skip */ }
      } else if (trimmed) {
        console.log('[CAL]', trimmed);
      }
    }
  });
  calProcess.stderr.on('data', d =>
    console.error('[CAL]', d.toString().trim()));
  calProcess.on('close', code => {
    console.log('[CAL] Exited:', code);
    calProcess = null;
  });
  calProcess.on('error', err =>
    console.error('[CAL] Spawn error:', err.message));

  console.log('[CAL] Calibration proctor started');
}

function stopCalibration() {
  if (calProcess) {
    try {
      if (process.platform === 'win32') {
        spawnSync('taskkill', ['/pid', String(calProcess.pid), '/f', '/t'],
          { timeout: 5000 });
      } else {
        calProcess.kill('SIGTERM');
      }
    } catch (e) { console.error('[CAL] Stop error:', e.message); }
    calProcess = null;
  }
}

// ── AUTH HEADERS ─────────────────────────────────────────────────
function authHeaders() {
  const base = { 'Content-Type': 'application/json' };
  return studentToken
    ? { ...base, 'Authorization': `Bearer ${studentToken}` }
    : base;
}

// ── POLLING ───────────────────────────────────────────────────────
let _sseAbort = null; // AbortController for SSE fetch
let _sseReconnectDelay = 0; // exponential backoff for SSE reconnect

function startPolling(sessionId) {
  if (pollInterval || _sseAbort) return; // already running

  // Try SSE first; fall back to interval polling on failure
  _startSSE(sessionId).catch(() => _startLegacyPolling(sessionId));
}

async function _startSSE(sessionId) {
  const token = studentToken || '';
  const url = `${SERVER_URL}/api/sse/events/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
  _sseAbort = new AbortController();
  let forceSubmitSent = false;

  const timeout = setTimeout(() => _sseAbort.abort(), 15000);
  const r = await fetch(url, { signal: _sseAbort.signal });
  clearTimeout(timeout);
  if (!r.ok || !r.body) throw new Error('SSE not available');

  console.log('[SSE] connected for', sessionId);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events (accumulate data lines, emit on blank line)
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete last line
      let eventData = '';
      for (const line of lines) {
        if (line === '') {
          // Blank line = event boundary
          if (eventData) {
            try {
              const evt = JSON.parse(eventData);
              // Force-submit
              if (!forceSubmitSent && evt.type === 'exam_submitted') {
                forceSubmitSent = true;
                if (mainWindow && !mainWindow.isDestroyed()) {
                  mainWindow.webContents.send('force-submit');
                }
              }
              // Violation banners
              const IGNORED = ['screenshot','enrollment','started','submitted',
                               'resumed','complete','session_ended','answer_selected'];
              if ((evt.severity === 'high' || evt.severity === 'medium') &&
                  !IGNORED.some(x => (evt.type || '').includes(x))) {
                if (mainWindow && !mainWindow.isDestroyed()) {
                  mainWindow.webContents.send('violation-detected', {
                    type:     evt.type,
                    details:  evt.details,
                    severity: evt.severity,
                  });
                }
              }
            } catch(_) { /* ignore non-JSON / keepalives */ }
          }
          eventData = '';
        } else if (line.startsWith('data: ')) {
          eventData += (eventData ? '\n' : '') + line.slice(6);
        }
        // Ignore event:, id:, retry:, and comment lines
      }
    }
  } catch(e) {
    if (e.name !== 'AbortError') console.error('[SSE] stream error:', e.message);
  }

  // Stream ended or errored — reconnect with exponential backoff
  if (_sseAbort && !_sseAbort.signal.aborted) {
    _sseReconnectDelay = Math.min((_sseReconnectDelay || 2000) * 2, 30000);
    console.log(`[SSE] stream ended, reconnecting in ${_sseReconnectDelay / 1000}s...`);
    setTimeout(() => {
      if (_sseAbort && !_sseAbort.signal.aborted) {
        _sseAbort = null;
        _startSSE(sessionId).then(() => { _sseReconnectDelay = 0; })
          .catch(() => _startLegacyPolling(sessionId));
      }
    }, _sseReconnectDelay);
  }
}

function _startLegacyPolling(sessionId) {
  if (pollInterval) return;
  console.log('[Poll] using legacy polling for', sessionId);
  let lastEventId = 0;
  let forceSubmitSent = false;
  let _pollInFlight = false;
  pollInterval = setInterval(async () => {
    if (_pollInFlight) return;
    _pollInFlight = true;
    try {
      const r    = await fetch(`${SERVER_URL}/events/${sessionId}`,
                               { headers: authHeaders() });
      if (!r.ok) { _pollInFlight = false; return; }
      const data = await r.json();
      const events = data.events || [];

      if (!forceSubmitSent && events.some(e => e.type === 'exam_submitted')) {
        forceSubmitSent = true;
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('force-submit');
        }
      }

      const IGNORED = ['screenshot','enrollment','started','submitted',
                       'resumed','complete','session_ended','answer_selected'];
      const newV = events.filter(e =>
        e.id > lastEventId &&
        (e.severity === 'high' || e.severity === 'medium') &&
        !IGNORED.some(x => e.type.includes(x))
      );
      if (newV.length > 0) {
        lastEventId = Math.max(...newV.map(e => e.id));
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('violation-detected', {
            type:     newV[0].type,
            details:  newV[0].details,
            severity: newV[0].severity,
          });
        }
      }
    } catch(e) { console.error('[Poll]', e.message); }
    _pollInFlight = false;
  }, 2000);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  if (_sseAbort) { try { _sseAbort.abort(); } catch(_) {} _sseAbort = null; }
}

// ── LOBBY WINDOW (pre-exam, NOT kiosk) ───────────────────────────
//
// Phase 2: Electron now boots into a normal window showing the student web
// dashboard (/student). The student logs in there, sees upcoming exams, and
// clicks "Start exam" to trigger an IPC that spawns a locked exam window.
// Everything outside of an active exam is unlocked — no shortcut capture,
// no kiosk mode, no always-on-top, devtools allowed for debugging.
function createLobbyWindow() {
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    lobbyWindow.show();
    lobbyWindow.focus();
    return lobbyWindow;
  }

  console.log('[Lobby] creating lobby window (fullscreen:false, kiosk:false)');

  lobbyWindow = new BrowserWindow({
    width:           1180,
    height:          820,
    minWidth:        900,
    minHeight:       640,
    // Defense-in-depth: macOS may remember a previous Space state from
    // the pre-Phase-2 kiosk builds. Explicitly declare non-kiosk geometry
    // so nothing drags the lobby into full-screen mode at launch.
    fullscreen:      false,
    fullscreenable:  true,
    kiosk:           false,
    alwaysOnTop:     false,
    frame:           true,
    resizable:       true,
    movable:         true,
    minimizable:     true,
    maximizable:     true,
    closable:        true,
    autoHideMenuBar: true,
    title:           'Procta',
    // Match theme.css --bg so the window is never white, even before CSS loads
    backgroundColor: '#06080d',
    show:            false,
    webPreferences: {
      preload:          path.join(__dirname, 'lobby_preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      devTools:         true,
    }
  });

  // Show window only after first paint — prevents white flash on slow systems
  lobbyWindow.once('ready-to-show', () => lobbyWindow.show());

  // Phase 2 fix: the lobby HTML lives inside the Electron bundle, not on
  // the server. The student dashboard is a shell that talks to the
  // backend via fetch(`${SERVER_URL}/api/...`), so deploying the backend
  // is NOT a prerequisite for the app to boot cleanly. Previously the
  // lobby loaded `${SERVER_URL}/student` directly, which failed hard if
  // the route wasn't deployed yet — giving the impression the app was
  // "stuck in the old startup screen".
  const lobbyHtml = findLobbyHtml();
  console.log('[Lobby] loading:', lobbyHtml);
  lobbyWindow.loadFile(lobbyHtml);

  lobbyWindow.webContents.on('dom-ready', () => {
    console.log('[Lobby] DOM ready');
    // Inject fallback background in case theme.css fails to load (e.g. ASAR
    // path issue on Windows, or Google Fonts @import blocking the stylesheet)
    lobbyWindow.webContents.insertCSS(
      'html,body{background:#06080d !important;color:#c8d1dc !important}'
    ).catch(() => {});
  });

  lobbyWindow.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return; // user-initiated abort
    console.error('[Lobby] load failed:', errorCode, errorDescription, validatedURL);
    const offline = `
      <!DOCTYPE html><html><head><meta charset="utf-8">
      <title>Procta — Error</title>
      <style>
        body{margin:0;font-family:-apple-system,sans-serif;background:#0a0e1a;color:#cbd5e1;
             display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px}
        .box{max-width:480px;background:#0f1629;border:1px solid rgba(255,255,255,.06);
             border-radius:14px;padding:36px 32px}
        h1{color:#fff;font-size:20px;margin:0 0 10px}
        p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 10px}
        code{font-family:monospace;color:#60a5fa;font-size:11px;word-break:break-all;
             display:block;margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px}
      </style></head><body>
      <div class="box">
        <h1>Lobby failed to open</h1>
        <p>Couldn't load the local student dashboard bundle.</p>
        <code>${errorDescription || errorCode}\n${validatedURL || lobbyHtml}</code>
        <p style="margin-top:16px">Relaunch the app, or reinstall if the problem persists.</p>
      </div></body></html>`;
    lobbyWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(offline));
  });

  lobbyWindow.webContents.on('did-finish-load', () => {
    console.log('[Lobby] did-finish-load OK');
  });

  lobbyWindow.webContents.on('will-navigate', (e, url) => {
    // Allow file:// (our own bundle), data: (error page), and SERVER_URL
    // redirects. Everything else is denied.
    try {
      const u = new URL(url);
      const ok = url.startsWith(SERVER_URL) ||
                 u.protocol === 'data:' ||
                 u.protocol === 'file:' ||
                 u.protocol === 'about:';
      if (!ok) e.preventDefault();
    } catch { e.preventDefault(); }
  });
  lobbyWindow.webContents.setWindowOpenHandler(
    () => ({ action: 'deny' }));

  lobbyWindow.on('closed', () => { lobbyWindow = null; });
  return lobbyWindow;
}

// Resolve the lobby HTML path in both dev (project root) and packaged
// (resources) layouts. In dev the file lives at `app/static/student.html`
// relative to main.js. When packaged by electron-builder we copy it to
// `renderer/lobby.html` (added to build.files).
function findLobbyHtml() {
  const candidates = [
    path.join(__dirname, 'renderer', 'lobby.html'),
    path.join(__dirname, 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'app', 'static', 'student.html'),
    path.join(process.resourcesPath || '', 'renderer', 'lobby.html'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch(e) {}
  }
  // Fallback — return the first candidate so did-fail-load fires with a
  // useful error rather than crashing.
  return candidates[0];
}

// ── EXAM WINDOW (kiosk-locked) ───────────────────────────────────
//
// Called by the lobby bridge when the student clicks "Start exam" on an
// exam card. Kiosk mode + global shortcut capture are engaged HERE, not at
// app launch, so the student is only locked down while actually sitting
// an exam. `releaseKiosk()` tears everything back down on submit / panic.
function createExamWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus();
    return mainWindow;
  }

  isKiosk = KIOSK_ALLOWED;  // honor --no-kiosk / PROCTOR_DEBUG overrides

  mainWindow = new BrowserWindow({
    fullscreen:      isKiosk,
    kiosk:           isKiosk,
    alwaysOnTop:     isKiosk,
    resizable:       !isKiosk,
    movable:         !isKiosk,
    minimizable:     !isKiosk,
    maximizable:     !isKiosk,
    closable:        !isKiosk,
    frame:           !isKiosk,
    width:           1280,
    height:          900,
    autoHideMenuBar: true,
    backgroundColor: '#06080d',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      devTools:         !isKiosk,
    }
  });

  mainWindow.loadFile(
    path.join(__dirname, 'renderer', 'index.html'));

  if (!isKiosk) mainWindow.webContents.openDevTools();

  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(SERVER_URL) && !url.startsWith('file://'))
      e.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(
    () => ({ action: 'deny' }));
  mainWindow.webContents.on('devtools-opened', () => {
    if (isKiosk) mainWindow.webContents.closeDevTools();
  });

  mainWindow.webContents.on('render-process-gone', (_, details) => {
    console.log('[App] Renderer gone:', details.reason, '— reloading');
    try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload(); } catch(e) {}
  });

  // Clear clipboard on exam start — prevent pre-copied answers
  try { clipboard.clear(); } catch(e) {}

  // Start continuous process monitoring during exam
  startProcessMonitor();

  if (isKiosk) {
    mainWindow.on('blur',  () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.focus(); });
    mainWindow.on('close', e  => e.preventDefault());
    powerBlockId = powerSaveBlocker.start('prevent-display-sleep');

    // ── Panic unlock chord ──────────────────────────────────────
    // Cmd/Ctrl+Shift+F12 → confirmation → full app quit + flag session.
    //
    // Registration ORDER matters on Windows. Electron's globalShortcut
    // uses Win32 RegisterHotKey under the hood; when the same root key
    // (F12) is later registered as a no-op blocker via registerAll(),
    // the specific-modifier chord registered AFTER could fail silently
    // on some Windows builds. Registering the panic chord FIRST gives
    // it uncontested ownership of (Ctrl+Shift+F12) before the block
    // list claims plain F12. This is the fix for the reported bug
    // "panic shortcut didn't close the app on Windows".
    const panicAccel = 'CommandOrControl+Shift+F12';
    const panicOk = globalShortcut.register(panicAccel, async () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      try {
        const confirmed = await mainWindow.webContents.executeJavaScript(`
          (function() {
            return confirm(
              'PANIC UNLOCK\\n\\n' +
              'This closes the app and flags your session for your teacher to review.\\n\\n' +
              'Your work will NOT be submitted automatically.\\n\\n' +
              'Continue?'
            );
          })()
        `);
        if (!confirmed) return;
        await handlePanicUnlock('student-triggered');
      } catch(e) {
        console.error('[Panic] chord error:', e.message);
      }
    });
    if (!panicOk || !globalShortcut.isRegistered(panicAccel)) {
      // On Windows this can happen if another app already owns the
      // hotkey, or if the F12 block below races us. The renderer-side
      // keydown fallback (see renderer/index.html) and the on-screen
      // "Emergency unlock" link still work, so the student isn't
      // stranded — but log loudly so we notice in support cases.
      console.error(`[Panic] globalShortcut.register('${panicAccel}') FAILED; relying on renderer fallback`);
    } else {
      console.log(`[Panic] chord armed: ${panicAccel}`);
    }

    // Global shortcut capture — kiosk lockdown keys. Registered AFTER
    // the panic chord so Ctrl+Shift+F12 wins priority on Windows.
    // Only registered while the exam window is alive; released by
    // releaseKiosk() on submit/panic.
    globalShortcut.registerAll([
      'Alt+F4','Cmd+Q','Cmd+W','Cmd+M','Cmd+H',
      'Cmd+Tab','Alt+Tab','F11','F12','Escape',
      'Cmd+Shift+I','Ctrl+Shift+I',
      'Cmd+R','Ctrl+R','F5',
      'PrintScreen','Cmd+Shift+3','Cmd+Shift+4',
      'Cmd+C','Cmd+V','Cmd+X',
      'Ctrl+C','Ctrl+V','Ctrl+X',
    ], () => false);

    // Emergency escape — admin-code exit (legacy).
    globalShortcut.register('CommandOrControl+Shift+Alt+E', () => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.executeJavaScript(`
          (function() {
            const code = prompt('Emergency exit — enter admin code:');
            window.proctor && window.proctor.adminExit(code);
          })()
        `);
      }
    });
  }
}

// ── KIOSK TEARDOWN ───────────────────────────────────────────────
// IMPORTANT: window destruction happens FIRST, before any other cleanup,
// so a hung Python process or stuck shortcut can't leave the user with a
// frozen kiosk window on screen. We use destroy() (not close()) because
// close() can be silently swallowed by lingering close-handlers, beforeunload
// handlers, or macOS fullscreen-transition races. destroy() is the documented
// nuclear option: it bypasses all of that and guarantees the window goes away.
function releaseKiosk({ reopenLobby = true } = {}) {
  console.log('[Kiosk] releasing', reopenLobby ? '(→ lobby)' : '(→ quit)');
  isKiosk = false;

  // Step 1: tear down the window FIRST. Nothing else matters if the user
  // is staring at a frozen exam screen.
  const winRef = mainWindow;
  mainWindow = null;
  if (winRef && !winRef.isDestroyed()) {
    try {
      winRef.removeAllListeners('close');
      winRef.removeAllListeners('blur');
      winRef.removeAllListeners('focus');
      try { winRef.setKiosk(false); }       catch(e) {}
      try { winRef.setFullScreen(false); }  catch(e) {}
      try { winRef.setAlwaysOnTop(false); } catch(e) {}
      try { winRef.setClosable(true); }     catch(e) {}
      // destroy() bypasses all close handlers + beforeunload + fullscreen
      // transitions. Window WILL go away.
      winRef.destroy();
      console.log('[Kiosk] window destroyed');
    } catch(e) {
      console.error('[Kiosk] destroy error:', e.message);
    }
  }

  // Step 2: watchdog. If somehow destroy() didn't take, force it again
  // after a beat. Belt-and-suspenders for the bug we just hit.
  setTimeout(() => {
    if (winRef && !winRef.isDestroyed()) {
      console.error('[Kiosk] window survived destroy(); retrying');
      try { winRef.destroy(); } catch(e) {}
    }
  }, 500);

  // Step 3: now clean up the rest. None of this can leave a window stuck.
  try { stopPython(); }              catch(e) { console.error('[Kiosk] stopPython:', e.message); }
  try { stopPolling(); }             catch(e) { console.error('[Kiosk] stopPolling:', e.message); }
  try { stopProcessMonitor(); }      catch(e) { console.error('[Kiosk] stopMonitor:', e.message); }
  try { globalShortcut.unregisterAll(); } catch(e) {}
  if (powerBlockId !== null) {
    try { powerSaveBlocker.stop(powerBlockId); } catch(e) {}
    powerBlockId = null;
  }

  currentSessionId = null;
  examContext = null;
  studentToken = null;
  calBiases = null;

  if (reopenLobby) {
    setTimeout(() => createLobbyWindow(), 200);
  }
}

// ── PANIC UNLOCK HANDLER ─────────────────────────────────────────
// Flags the active session with a high-severity event, then tears down
// the kiosk AND quits the app entirely — the student wanted off. We do
// NOT auto-submit; the session stays in_progress so the teacher can
// review what's there and decide whether to accept or void it.
//
// Why full quit (instead of "return to lobby" like submit does):
//   - A panic user is usually blocked on a hard OS/hardware problem
//     (camera died, browser froze, VM popped up). Dumping them back in
//     the lobby invites an infinite loop of re-entering the exam.
//   - Windows users specifically reported the old "back to lobby"
//     behaviour as "the app didn't close" — which is the report that
//     prompted this rewrite.
//
// Watchdog: if Electron's graceful `app.quit()` gets stuck waiting on
// a lingering child process or GPU shutdown, `app.exit(0)` fires 2s
// later and hard-terminates. On Windows that's the difference between
// a zombie tray icon and a truly-closed app.
async function handlePanicUnlock(reason) {
  const sid = currentSessionId;
  if (sid && studentToken) {
    try {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), 5000);
      await fetch(`${SERVER_URL}/event`, {
        method: 'POST',
        headers: authHeaders(),
        signal: ac.signal,
        body: JSON.stringify({
          session_id: sid,
          event_type: 'panic_unlock',
          severity:   'high',
          details:    `Panic unlock triggered (${reason}). Session left in_progress for teacher review.`,
        }),
      });
      clearTimeout(timer);
    } catch(e) { console.error('[Panic] event post failed:', e.message); }
  }
  console.log(`[Panic] reason=${reason} — quitting app`);
  try { releaseKiosk({ reopenLobby: false }); }
  catch(e) { console.error('[Panic] releaseKiosk threw:', e.message); }

  // Also close the lobby window if it's floating around, so Electron's
  // window-all-closed handler fires cleanly.
  try {
    if (lobbyWindow && !lobbyWindow.isDestroyed()) lobbyWindow.destroy();
  } catch(e) {}

  try { app.quit(); } catch(e) { console.error('[Panic] app.quit:', e.message); }
  // Watchdog — if we're still alive 2s later, nuke from orbit.
  setTimeout(() => {
    console.error('[Panic] graceful quit did not take — app.exit(0)');
    try { app.exit(0); } catch(e) { process.exit(0); }
  }, 2000);
}

// ── APP START ─────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // ── STEP 1: Show the lobby window IMMEDIATELY ──────────────────
  // The user sees the app within milliseconds. Everything else runs
  // in the background. This fixes the Windows "Not Responding" freeze.
  createLobbyWindow();

  // ── STEP 1b: Check for app updates (silent, non-blocking) ─────
  initAutoUpdater();

  // ── STEP 2: Run integrity checks in background (async) ────────
  // All shell commands use async exec — zero main-thread blocking.
  // Store the promise so the IPC handler can await it.
  _integrityReady = runIntegrityChecks().then(flags => {
    integrityFlags = flags;
    console.log(`[Integrity] async checks complete: ${flags.length} flag(s)`);
  }).catch(e => {
    console.error('[Integrity] check failed:', e.message);
    integrityFlags = [];
  });

  // ── STEP 3: GPU-based VM check (needs a window) ───────────────
  if (lobbyWindow) {
    lobbyWindow.webContents.on('did-finish-load', () => {
      _checkGPU(lobbyWindow);
    });
  }

  // ── STEP 4: Windows Python setup (async, non-blocking) ────────
  // findPython() and checkPackagesReady() are fully async. We still defer
  // with setTimeout to let the lobby window render first.
  if (process.platform === 'win32') {
    setTimeout(async () => {
      try {
        const python = await findPython();
        const packagesOk = python && await checkPackagesReady(python);

        if (!packagesOk) {
          createSetupWindow();
          try {
            await runWindowsSetup();
            await new Promise(r => setTimeout(r, 2000));
          } catch(e) {
            console.error('[Setup] Failed:', e);
          }
          if (setupWindow && !setupWindow.isDestroyed()) {
            setupWindow.close();
          }
        } else {
          console.log('[Setup] Python ready, skipping setup');
        }
      } catch(e) {
        console.error('[Setup] Error:', e);
      }
    }, 500);  // 500ms delay — enough for lobby to render
  }
});

app.on('before-quit', () => {
  stopPython();
  stopPolling();
  if (powerBlockId !== null) powerSaveBlocker.stop(powerBlockId);
});

app.on('window-all-closed', () => {
  // Quit whenever every window (lobby + exam) is gone. In Phase 2 the
  // lobby is always closable, so this just works — no more "isKiosk guard".
  stopPython();
  stopPolling();
  try { globalShortcut.unregisterAll(); } catch(e) {}
  app.quit();
});

// ── IPC ───────────────────────────────────────────────────────────
ipcMain.handle('get-integrity-flags', async () => {
  // Wait for async checks to finish before returning results.
  // Without this, the renderer could get an empty array if it asks
  // before the background checks complete.
  if (_integrityReady) {
    try { await _integrityReady; } catch(e) {}
  }
  const BLOCKING_TYPES = new Set([
    'vm_detected', 'remote_desktop_detected', 'vpn_detected',
    'proxy_detected', 'debugger_detected',
  ]);
  return integrityFlags.map(f => ({
    ...f,
    blocking: BLOCKING_TYPES.has(f.type) && f.severity === 'high',
  }));
});

ipcMain.handle('validate-student', async (_, roll, accessCode) => {
  const body = {roll_number: roll, access_code: accessCode || ''};
  if (examContext && examContext.examId) body.exam_id = examContext.examId;
  const r = await fetch(`${SERVER_URL}/api/validate-student`, {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(body)
  });
  if (!r.ok) { const e = await r.json(); throw new Error(e.detail); }
  const data = await r.json();
  studentToken = data.token || null; // store JWT for all subsequent requests
  return data;
});

ipcMain.handle('get-questions', async (_, sessionId) => {
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const r = await fetch(`${SERVER_URL}/api/questions${qs}`,
                        { headers: authHeaders() });
  if (!r.ok) throw new Error('Could not load questions');
  return r.json();
});

ipcMain.handle('log-event', async (_, data) => {
  try {
    await fetch(`${SERVER_URL}/event`, {
      method:  'POST',
      headers: authHeaders(),
      body:    JSON.stringify(data)
    });
  } catch(e) { console.error('[log-event]', e.message); }
});

ipcMain.handle('submit-exam', async (_, data) => {
  const r = await fetch(`${SERVER_URL}/api/submit-exam`, {
    method:  'POST',
    headers: authHeaders(),
    body:    JSON.stringify(data)
  });
  if (!r.ok) {
    const errText = await r.text();
    console.error('[Submit] Server error:', r.status, errText);
    throw new Error(`Submission failed: ${r.status} ${errText}`);
  }
  return r.json();
});

ipcMain.handle('get-events', async (_, sessionId) => {
  const r = await fetch(`${SERVER_URL}/events/${sessionId}`,
                        { headers: authHeaders() });
  if (!r.ok) return { events: [] };
  return r.json();
});

ipcMain.handle('start-calibration', async (_, data) => {
  const sessionId = data && data.sessionId;
  if (sessionId) currentSessionId = sessionId;
  await startCalibration(sessionId);
  return { started: true };
});

ipcMain.handle('stop-calibration', (_, data) => {
  const biases = data && data.biases;
  if (biases) {
    calBiases = biases; // {gaze_yaw, gaze_pitch, head_yaw, head_pitch}
    console.log('[CAL] Biases received:', JSON.stringify(calBiases));
  }
  stopCalibration();
  return { stopped: true };
});

ipcMain.handle('start-proctor', async (_, data) => {
  const sessionId = data && data.sessionId;
  if (sessionId) currentSessionId = sessionId;
  await startPython(sessionId);
  return { started: true };
});

ipcMain.handle('start-polling', (_, data) => {
  const sessionId = data && data.sessionId;
  if (sessionId) currentSessionId = sessionId;
  startPolling(sessionId);
  return { polling: true };
});

ipcMain.handle('stop-proctor', () => {
  stopPython();
  stopPolling();
  return { stopped: true };
});

// Phase 2: the exam renderer fetches pre-filled context (roll, access code,
// exam title) stashed by the lobby bridge so the student doesn't have to
// retype what they already entered on the web dashboard. Returns null if
// the exam window was opened directly (legacy / debug).
ipcMain.handle('get-exam-context', () => {
  return examContext;
});

// Lobby → main bridge: the student clicked "Start exam" on an exam card.
// We stash their context and spawn the locked exam window. The lobby
// window stays open in the background so that panic/submit can return to
// it cleanly (we just re-focus it after releaseKiosk).
ipcMain.handle('lobby-launch-exam', async (_, ctx) => {
  if (!ctx || !ctx.rollNumber) {
    return { ok: false, error: 'Missing roll number' };
  }
  examContext = {
    rollNumber:  String(ctx.rollNumber).trim().toUpperCase(),
    accessCode:  String(ctx.accessCode || '').trim().toUpperCase(),
    examTitle:   ctx.examTitle || '',
    teacherId:   ctx.teacherId || null,
    examId:      ctx.examId || null,
  };
  console.log('[Lobby] launch exam:', examContext);
  // Hide (don't destroy) the lobby so we can come back to it cleanly.
  if (lobbyWindow && !lobbyWindow.isDestroyed()) {
    try { lobbyWindow.hide(); } catch(e) {}
  }
  createExamWindow();
  return { ok: true };
});

// Panic unlock fired from the renderer (in-exam button) — same effect as
// the Cmd/Ctrl+Shift+F12 chord.
ipcMain.handle('panic-unlock', async (_, payload) => {
  await handlePanicUnlock((payload && payload.reason) || 'renderer-triggered');
  return { ok: true };
});

// Normal post-submit exit → release kiosk + reopen lobby.
ipcMain.handle('exit-exam-to-lobby', () => {
  releaseKiosk({ reopenLobby: true });
  return { ok: true };
});

ipcMain.handle('admin-exit', (_, code) => {
  // AUTO_CLOSE is fired by the renderer after a SUCCESSFUL submit. In
  // Phase 2 we treat it as "return to lobby" instead of "quit app" so the
  // student lands back on their dashboard to see the submitted status and
  // browse practice, etc. The manual admin-code path (typed by a teacher)
  // still quits the entire app.
  //
  // IMPORTANT: this path is DISTINCT from panic. Panic → app.quit() (full
  // close). AUTO_CLOSE → releaseKiosk + re-show lobby (graceful return).
  // If you ever unify them, read the comment on handlePanicUnlock first —
  // students use each path for different reasons and the UX diverges.
  if (code === 'AUTO_CLOSE') {
    console.log('[admin-exit] AUTO_CLOSE received');
    // Capture a ref before releaseKiosk nulls it, so the outer watchdog
    // below can verify the destruction took effect even if releaseKiosk
    // itself throws partway through.
    const winRef = mainWindow;
    try {
      releaseKiosk({ reopenLobby: true });
    } catch(e) {
      console.error('[admin-exit] releaseKiosk threw:', e.message);
    }
    // Outer-most safety net: if for ANY reason the kiosk window is still
    // alive 1s later, force-destroy it. The user must NEVER end up staring
    // at a frozen "Exam Submitted" card with no way out.
    setTimeout(() => {
      if (winRef && !winRef.isDestroyed()) {
        console.error('[admin-exit] window still alive after releaseKiosk; force-destroying');
        try { winRef.destroy(); } catch(e) {}
      }
      if (lobbyWindow && !lobbyWindow.isDestroyed()) {
        try { lobbyWindow.show(); lobbyWindow.focus(); } catch(e) {}
      } else {
        // Lobby got lost too — recreate it so the user has somewhere to land.
        try { createLobbyWindow(); } catch(e) {}
      }
    }, 1000);
    return { success: true };
  }
  if (code === ADMIN_CODE) {
    releaseKiosk({ reopenLobby: false });
    app.quit();
    return { success: true };
  }
  return { success: false };
});
