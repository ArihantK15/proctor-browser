const path = require('path');
const os   = require('os');

// ── Server / Environment ──────────────────────────────────────────
const SERVER_URL = process.env.PROCTOR_SERVER_URL || 'https://app.procta.net';
const ADMIN_CODE = process.env.EXIT_CODE || 'EXIT2026';
// The `--no-kiosk` flag and `PROCTOR_DEBUG=1` are DEV-ONLY escape hatches.
// In a packaged (shipped) build they MUST be ignored — otherwise a student can
// launch `Procta.exe --no-kiosk` (or set PROCTOR_DEBUG=1) and run the exam fully
// unlocked: no kiosk/fullscreen, DevTools auto-opened, blocked-shortcuts skipped
// — defeating the secure-browser guarantee. Gate the bypass behind
// !app.isPackaged so kiosk is ALWAYS enforced in production.
let _IS_PACKAGED = false;
try { _IS_PACKAGED = require('electron').app.isPackaged; } catch (_) { _IS_PACKAGED = false; }
const KIOSK_ALLOWED = _IS_PACKAGED
  ? true
  : (!process.argv.includes('--no-kiosk') && process.env.PROCTOR_DEBUG !== '1');

// ── Kiosk lockdown shortcuts ──────────────────────────────────────
// Base set registered on every platform. Cmd+* accelerators simply fail to
// register on Windows/Linux (no Command key) and are skipped — the arming
// loop only tracks successful registrations — so a shared base is safe.
//
// Known phantom entries on macOS, researched 2026-07-03 (kept in the list
// anyway — registering them is harmless and they DO work on the other
// platform they're aimed at, e.g. Alt+Tab genuinely blocks via Windows'
// RegisterHotKey — but do not assume the macOS half of the pair does
// anything):
//   - Cmd+Tab:   OS App Switcher, dispatched by the WindowServer before it
//     reaches Carbon's RegisterEventHotKey (what globalShortcut uses on
//     Mac) — confirmed still open as electron/electron#18207. The real
//     defense is the blur/focus refocus watcher in kiosk-manager.js
//     (createExamWindow's 'blur' handler), which yanks focus back within
//     100ms of ANY focus loss, regardless of cause.
//   - Cmd+Q:     also NOT reliably intercepted by globalShortcut on macOS
//     (electron/electron#13440, "CMD+Q exits kiosk mode on OSX") — Cocoa
//     routes it to NSApplication terminate: independent of app-level
//     shortcut registration. The real backstop is the kiosk-state check in
//     main.js's `before-quit` handler, which preventDefaults the actual
//     app quit while an exam is armed (registering the accelerator here is
//     still worth keeping as defense-in-depth for any environment where it
//     does register).
// Cmd+Space (Spotlight, in _BLOCKED_MAC below) is the same phantom-
// coverage class — globalShortcut doesn't actually intercept it either
// (electron/electron#9203) — but it's still caught by the same blur/focus
// watcher as Cmd+Tab: Spotlight's search overlay steals keyboard focus
// from the exam window the instant it opens, which fires 'blur' and gets
// yanked back within 100ms same as any other focus-stealing switch.
//
// Cmd+Shift+3/4/5 (screenshot/screen-recording, below and in _BLOCKED_MAC)
// are the SAME class again, and unlike the three above there is NO
// existing backstop for them: Apple's own docs group Screenshots under
// the same "symbolic hotkeys" system as Spotlight/Mission Control, where
// system-assigned combos take precedence over any third-party
// RegisterEventHotKey registration — no dedicated Electron issue number
// found for this specific combo, but it follows directly from the same
// WindowServer-priority mechanism already confirmed above, so treat it as
// equally unenforced until verified otherwise. This one actually matters:
// a screenshot/recording doesn't require sustained focus loss (Cmd+Shift+3
// captures instantly with the window still key), so the blur/focus watcher
// doesn't catch it — a student can grab exam content and share it outside
// the proctored session. No safe fix at the shortcut layer exists (same
// reasoning as Tor Browser detection — don't guess). The real fix is
// macOS's `systemPreferences.subscribeNotification(
// 'com.apple.screenIsBeingCapturedDidChange', ...)`, which detects *active*
// screen recording/sharing (not one-off screenshots) and could feed the
// existing violation/event pipeline the same way remote-desktop process
// detection does — not implemented, tracked as a follow-up.
//
// TESTED AND REJECTED, 2026-07-04: disabling screenshot capture at the
// source via `com.apple.symbolichotkeys` entries 28-31 (same mechanism
// that genuinely works for Launchpad, entry 160 — see kiosk-manager.js).
// Live-tested this specific combination and it's genuinely ambiguous
// (couldn't get a clean keystroke-simulation result — osascript needs
// Accessibility permission this test process didn't have), but MULTIPLE
// independent sources (including a Jamf enterprise-IT community thread
// specifically about disabling screenshots on managed Macs) report that
// writing `enabled = 0` to entries 28-31 updates the plist but the
// shortcuts KEEP WORKING anyway — unlike Launchpad, screenshot capture is
// implemented at a lower level (CoreGraphics/WindowServer screencapture
// daemon) that apparently doesn't consult symbolichotkeys the same way
// Dock-mediated features (Mission Control, Launchpad) do. Did NOT ship
// this — shipping something that looks like protection but silently
// doesn't work is worse than no fix. If revisited, needs a real physical
// keypress test on real hardware (not a simulated one) before trusting it.
//
// WINDOWS SIDE, researched 2026-07-04: PrintScreen has the SAME phantom-
// coverage problem as macOS's symbolic hotkeys, via a different
// mechanism — electron/electron#7629 confirms
// `globalShortcut.register('PrintScreen', ...)` returns success but the
// callback never fires on a real press. `Win+Shift+S` (Snip & Sketch,
// the more commonly used modern screenshot shortcut) isn't in
// _BLOCKED_BASE at all — a bigger gap than PrintScreen itself. Found a
// real, per-user (HKCU, no admin needed) registry mechanism —
// `HKEY_CURRENT_USER\Control Panel\Keyboard` →
// `PrintScreenKeyForSnippingEnabled` (DWORD) = 0 — that reportedly stops
// PrintScreen from opening the Snipping Tool overlay. NOT implemented:
// this environment has no Windows hardware to verify the live-apply
// behavior (does it need an Explorer restart / UpdatePerUserSystemParameters
// refresh, or does it apply on the very next keypress?), unlike every
// macOS fix in this file which was verified end-to-end on real hardware
// before shipping. Needs a real Windows machine to confirm before
// trusting it. The more aggressive option — a `Scancode Map` REG_BINARY
// under HKLM that remaps the PrintScreen key to a no-op — was rejected
// outright: it needs admin rights AND a logout/reboot to take effect,
// same session-disruption risk class as the Launchpad trackpad-gesture
// path that was already declined for the same reason.
const _BLOCKED_BASE = [
  'Alt+F4','Cmd+Q','Cmd+W','Cmd+M','Cmd+H',
  'Cmd+Tab','Alt+Tab','F11','F12','Escape',
  'Cmd+Shift+I','Ctrl+Shift+I',
  'Cmd+R','Ctrl+R','F5',
  'PrintScreen','Cmd+Shift+3','Cmd+Shift+4',
  'Cmd+C','Cmd+V','Cmd+X',
  'Ctrl+C','Ctrl+V','Ctrl+X',
];

// macOS-only kiosk escapes the base list missed — each one lets a student
// leave the locked exam window on a Mac (Windows had no equivalent hole):
//   Cmd+Space          → Spotlight: search the web / launch any app
//   Cmd+Shift+5        → screen-recording toolbar (3/4 were blocked, the
//                        record UI was not)
//   Control+Up/Down    → Mission Control / App Exposé (see & pick other windows)
//   Control+Left/Right → switch to another Space/Desktop (cheat sheet there)
//   F3                 → Mission Control hardware key
// Gated to darwin: Control+Arrow is legitimate word/line navigation on
// Windows and must NOT be blocked there.
//
// NOT coverable at all, researched 2026-07-04: on a trackpad-equipped Mac
// (every MacBook, any Magic Trackpad), 3/4-finger swipes reach Mission
// Control / Space-switching the exact same way the keyboard shortcuts
// above do, but there is no macOS API — public or otherwise — for a
// userspace app to intercept or disable trackpad gestures. This is
// Apple's platform security model working as intended (apps can't hijack
// system-level input gestures), not a bug in this list. The keyboard
// shortcuts below are real, working coverage for keyboard-only Macs
// (external keyboard, no trackpad) — they just don't help a student on a
// laptop trackpad. No code fix exists; noting it so nobody assumes the
// keyboard coverage below implies gesture coverage too. Tried disabling
// Mission Control at the source instead (`com.apple.dock
// mcx-expose-disabled`) — the preference wrote and applied cleanly, but a
// real physical test on real hardware confirmed Mission Control still
// opens anyway. Reverted — see lib/kiosk-manager.js's "TESTED AND
// REVERTED" comment. Genuinely uncoverable by any means found so far.
//
// Longer-term (not started, no timeline): Apple's FamilyControls/
// ManagedSettings/DeviceActivity frameworks are the Apple-sanctioned way
// to do managed restrictions, but they're an app-BLOCKING primitive
// (prevent launching/switching to specific other apps), not a gesture/
// Mission-Control-blocking one — adopting them would be a different,
// arguably stronger capability (block ANY other app from opening during
// an exam) rather than a direct fix for this specific gap. Requires a
// native Swift helper (unreachable from Electron/Node directly) and a
// separate Apple entitlement request with its own multi-week approval
// lead time. Scoped as its own future project, not a quick fix.
const _BLOCKED_MAC = [
  'Cmd+Space', 'Cmd+Shift+5',
  'Control+Up', 'Control+Down', 'Control+Left', 'Control+Right', 'F3',
];

const BLOCKED_SHORTCUTS = process.platform === 'darwin'
  ? [..._BLOCKED_BASE, ..._BLOCKED_MAC]
  : _BLOCKED_BASE;

const PANIC_SHORTCUT = 'CommandOrControl+Shift+F12';
const EMERGENCY_SHORTCUT = 'CommandOrControl+Shift+Alt+E';

// ── Integrity check ───────────────────────────────────────────────
const VM_MAC_PREFIXES = [
  '00:05:69', '00:0c:29', '00:1c:14', '00:50:56',
  '08:00:27', '00:15:5d', '00:16:3e', '52:54:00', '00:1a:4a',
];

// Only GPU strings that are *uniquely* produced by virtual hardware belong
// here. Software rasterizers — "microsoft basic render" (WARP), "swiftshader",
// "llvmpipe", "chromium" — are Chromium's fallback when hardware acceleration
// is disabled/unavailable, which happens on plenty of REAL student laptops
// (no GPU driver, old integrated GPU, accel toggled off). Flagging those as a
// VM blocked legit students with "Exam Blocked: Virtual Machine". VMware/
// VirtualBox/virgl are real VM GPUs and are also caught by the Manufacturer
// and MAC-prefix checks, so this stays defense-in-depth without false blocks.
const VM_GPU_RENDERERS = [
  'vmware', 'virtualbox', 'virgl',
];

const BLOCKING_TYPES = new Set([
  'vm_detected', 'remote_desktop_detected', 'vpn_detected',
  'proxy_detected', 'debugger_detected',
]);

// ── Process threat detection (shared between pre-exam scan & live monitor) ─
const nb    = '(?<![\\w-])';
const nbEnd = '(?![\\w-])';
const mk    = name => new RegExp(nb + name + nbEnd, 'i');

const THREATS = [
  { rx: mk('teamviewer'),  label: 'TeamViewer', type: 'remote_desktop_detected' },
  { rx: mk('anydesk'),     label: 'AnyDesk',    type: 'remote_desktop_detected' },
  { rx: mk('mstsc'),       label: 'mstsc',      type: 'remote_desktop_detected' },
  { rx: mk('vncviewer'),   label: 'VNC',        type: 'remote_desktop_detected' },
  { rx: mk('rustdesk'),    label: 'RustDesk',   type: 'remote_desktop_detected' },
  { rx: mk('parsec'),      label: 'Parsec',     type: 'remote_desktop_detected' },
  { rx: mk('parsecd'),     label: 'Parsec (daemon)', type: 'remote_desktop_detected' },
  { rx: mk('screenconnect'), label: 'ScreenConnect', type: 'remote_desktop_detected' },
  { rx: mk('logmein'),     label: 'LogMeIn',    type: 'remote_desktop_detected' },
  { rx: mk('obs64'),       label: 'OBS (64)',   type: 'screen_share_detected' },
  { rx: mk('obs32'),       label: 'OBS (32)',   type: 'screen_share_detected' },
  { rx: /(?<![\w-])obs studio(?![\w-])/i, label: 'OBS Studio', type: 'screen_share_detected' },
  { rx: /(?<![\w-])obs\.app(?![\w-])/i,       label: 'OBS.app',  type: 'screen_share_detected' },
  { rx: mk('screensharingd'), label: 'ScreenSharingD', type: 'screen_share_detected' },
  { rx: mk('openvpn'),     label: 'OpenVPN',    type: 'vpn_detected' },
  { rx: mk('nordvpn'),     label: 'NordVPN',    type: 'vpn_detected' },
  { rx: mk('expressvpn'),  label: 'ExpressVPN', type: 'vpn_detected' },
  { rx: mk('surfshark'),   label: 'Surfshark',  type: 'vpn_detected' },
  { rx: mk('protonvpn'),   label: 'ProtonVPN',  type: 'vpn_detected' },
  { rx: mk('cyberghost'),  label: 'CyberGhost', type: 'vpn_detected' },
  { rx: mk('windscribe'),  label: 'Windscribe', type: 'vpn_detected' },
  { rx: mk('privateinternetaccess'), label: 'PIA', type: 'vpn_detected' },
  { rx: mk('pia-service'), label: 'PIA Service', type: 'vpn_detected' },
  { rx: mk('mullvad'),     label: 'Mullvad',    type: 'vpn_detected' },
  { rx: mk('wireguard'),   label: 'WireGuard',  type: 'vpn_detected' },
  { rx: /(?<![\w-])wg\.exe(?![\w-])/i, label: 'WG.exe', type: 'vpn_detected' },
  { rx: mk('tailscale'),   label: 'Tailscale',  type: 'vpn_detected' },
  { rx: mk('zerotier'),    label: 'ZeroTier',   type: 'vpn_detected' },
  { rx: mk('v2ray'),       label: 'V2Ray',      type: 'vpn_detected' },
  { rx: mk('v2rayn'),      label: 'V2RayN',     type: 'vpn_detected' },
  { rx: mk('xray'),        label: 'Xray',       type: 'vpn_detected' },
  { rx: mk('clash'),       label: 'Clash',      type: 'vpn_detected' },
  { rx: mk('shadowsocks'), label: 'Shadowsocks', type: 'vpn_detected' },
  { rx: mk('ss-local'),    label: 'SS-Local',   type: 'vpn_detected' },
  // Tor Browser detection, researched 2026-07-03 (was silently non-functional
  // — see below):
  //   - Windows: verified the actual browser process is named "tor.exe" (Tor
  //     Project renamed it away from "firefox.exe" specifically to avoid
  //     confusion/camouflage concerns). Covered correctly below.
  //   - macOS: verified the current executable is
  //     /Applications/TorBrowser.app/Contents/MacOS/firefox — the bare
  //     process name is literally "firefox", IDENTICAL to a legitimate
  //     Firefox install. `ps -eo comm` only returns the bare command name,
  //     not the full path, so there is no reliable way to tell Tor
  //     Browser's process apart from a student's ordinary Firefox with this
  //     scan mechanism. Do NOT add a bare mk('firefox') pattern here — that
  //     would false-flag every legitimate Firefox user, which is worse than
  //     not detecting Tor Browser at all. A real fix needs the scan to
  //     capture full executable paths (e.g. `ps -eo comm,args` or
  //     `wmic process get ExecutablePath` equivalents) and match on
  //     "TorBrowser.app" / "Tor Browser" in the path instead of the bare
  //     name — tracked as a follow-up, not attempted here.
  //   The old mk('torbrowser') pattern below matched neither of the above —
  //   it never matched any real observed process name on either platform —
  //   so it looked like coverage that didn't actually exist. Removed rather
  //   than left as misleading dead weight.
  { rx: /(?<![\w-])tor\.exe(?![\w-])/i, label: 'Tor', type: 'vpn_detected' },
  { rx: /\/tor(?![\w-])/i, label: 'Tor (unix)', type: 'vpn_detected' },
  { rx: mk('hotspotshield'), label: 'Hotspot Shield', type: 'vpn_detected' },
  { rx: mk('tunnelbear'),  label: 'TunnelBear', type: 'vpn_detected' },
  { rx: mk('globalprotect'), label: 'GlobalProtect', type: 'vpn_detected' },
  { rx: mk('pangps'),      label: 'PanGPS',     type: 'vpn_detected' },
  { rx: mk('forticlient'), label: 'FortiClient', type: 'vpn_detected' },
  { rx: mk('fortisslvpn'), label: 'FortiSSLVPN', type: 'vpn_detected' },
  { rx: mk('vpnagent'),    label: 'VPN Agent',  type: 'vpn_detected' },
  { rx: mk('vpnui'),       label: 'VPN UI',     type: 'vpn_detected' },
  { rx: mk('checkpoint'),  label: 'Checkpoint', type: 'vpn_detected' },
  { rx: mk('snxctl'),      label: 'SNX CTL',    type: 'vpn_detected' },
  { rx: mk('psiphon'),     label: 'Psiphon',    type: 'vpn_detected' },
  { rx: mk('ultrasurf'),   label: 'UltraSurf',  type: 'vpn_detected' },
  { rx: mk('freegate'),    label: 'FreeGate',   type: 'vpn_detected' },
  { rx: mk('fiddler'),     label: 'Fiddler',    type: 'debugger_detected' },
  { rx: mk('charles'),     label: 'Charles',    type: 'debugger_detected' },
  { rx: mk('wireshark'),   label: 'Wireshark',  type: 'debugger_detected' },
  { rx: mk('burpsuite'),   label: 'Burp Suite', type: 'debugger_detected' },
  { rx: mk('mitmproxy'),   label: 'mitmproxy',  type: 'debugger_detected' },
  { rx: mk('mitmweb'),     label: 'mitmweb',    type: 'debugger_detected' },
  { rx: mk('mitmdump'),    label: 'mitmdump',   type: 'debugger_detected' },
  { rx: mk('proxyman'),    label: 'Proxyman',   type: 'debugger_detected' },
  { rx: mk('httpdebugger'), label: 'HTTP Debugger', type: 'debugger_detected' },
  { rx: mk('httpanalyzer'), label: 'HTTP Analyzer', type: 'debugger_detected' },
];

// Pre-exam scan categories (same patterns, grouped for scanning)
const ALL_PROCESSES = {
  vm: [mk('vmtoolsd'), mk('vmwaretray'), mk('vboxservice'), mk('vboxtray'),
       mk('vmcompute'), mk('xenservice')],
  remote: [mk('teamviewer'), mk('anydesk'), mk('mstsc'), mk('vncviewer'),
           /chrome remote desktop/i, mk('rustdesk'), mk('parsec'), mk('parsecd'),
           mk('screenconnect'), mk('logmein')],
  screen_share: [mk('obs64'), mk('obs32'), /(?<![\w-])obs studio(?![\w-])/i,
                 /(?<![\w-])obs\.app(?![\w-])/i, mk('screensharingd')],
  vpn: THREATS.filter(t => t.type === 'vpn_detected').map(t => t.rx),
  debugger: THREATS.filter(t => t.type === 'debugger_detected').map(t => t.rx),
};

const SCAN_TYPE_MAP = {
  vm: 'vm_detected', remote: 'remote_desktop_detected',
  screen_share: 'screen_share_detected', vpn: 'vpn_detected',
  debugger: 'debugger_detected',
};

// ── Python finder ─────────────────────────────────────────────────
function getPythonCandidates() {
  const isWin = process.platform === 'win32';
  return isWin ? [
    path.join(process.resourcesPath || __dirname, 'python', 'python.exe'),
    path.join(__dirname, 'resources', 'python', 'python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python311','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python312','python.exe'),
    path.join(os.homedir(),'AppData','Local','Programs','Python','Python310','python.exe'),
    'C:\\Python311\\python.exe', 'C:\\Python312\\python.exe', 'C:\\Python310\\python.exe',
    path.join(os.homedir(),'AppData','Local','Microsoft','WindowsApps','python3.exe'),
  ] : [
    // Production: the bundled relocatable venv (these two win).
    path.join(__dirname, 'venv', 'bin', 'python3'),
    path.join(process.resourcesPath || '', 'venv', 'bin', 'python3'),
    // Dev / no-bundle fallbacks. /opt/homebrew is the Apple-Silicon Homebrew
    // prefix (the default on every M-series Mac) and was MISSING — only the
    // Intel /usr/local prefix was listed, so a bundle-less Apple-Silicon Mac
    // relying on Homebrew python never found it. Add the common managers;
    // system /usr/bin/python3 stays LAST (PEP 668 "externally managed" — we
    // never want to pip into it if anything else is available).
    '/opt/homebrew/bin/python3',                                          // Apple Silicon Homebrew
    '/usr/local/bin/python3',                                             // Intel Homebrew / manual
    '/opt/local/bin/python3',                                             // MacPorts
    path.join(os.homedir(), '.pyenv', 'shims', 'python3'),               // pyenv
    '/Library/Frameworks/Python.framework/Versions/Current/bin/python3', // python.org installer
    '/usr/bin/python3',
  ];
}

function getScriptPathCandidates() {
  return [
    path.join(process.resourcesPath || '', 'proctor.py'),
    path.join(__dirname, 'proctor.py'),
    path.join(appRequires().getAppPath(), 'proctor.py'),
  ];
}

// Lazy require for app (avoid circular deps)
function appRequires() { return require('electron').app; }

// IMPORTANT: This list MUST be a superset of requirements-proctor.txt.
// The electron-smoke-test enforces this so a student install never
// diverges from the documented dev install. When you add a package to
// requirements-proctor.txt, add it here too (or to the test's known
// allowlist if it's a transitive-only dep we don't need to spawn pip
// for separately).
const PIP_PACKAGES = [
  // VERSION-PINNED on purpose. Unbounded '>=' let the Windows bundle pull
  // uniface 3.7.0 while detect_faces targeted 1.1.0's dict API — detect() then
  // returned Face OBJECTS and face detection silently broke on every frame.
  // uniface is pinned EXACTLY (its retinaface_mnet_v2.onnx is SHA-locked to
  // this release); the rest are capped below the next major so an API-breaking
  // bump can't land unnoticed while patches/minors still flow.
  'opencv-python>=4.13.0.92,<5', 'numpy>=1.24.0,<3', 'requests',
  'uniface==3.7.0', 'onnxruntime>=1.19.2,<2',
  // Object detection runs on onnxruntime from a bundled weights/yolo26n.onnx
  // (legacy weights/yolov8n.onnx is the fallback) — we no longer install
  // ultralytics (which dragged in torch, ~2 GB).
  'sounddevice',
  // Phase 75 — audio detection
  'vosk', 'python_speech_features',
  // Previously drifted from requirements-proctor.txt
  'insightface>=0.7.3,<0.8', 'websocket-client', 'psutil',
];

// ── Polling ───────────────────────────────────────────────────────
const POLL_INTERVAL_MS = 2000;
const IGNORED_EVENT_TYPES = new Set([
  'screenshot','enrollment','started','submitted',
  'resumed','complete','session_ended','answer_selected',
]);

// ── Process monitoring ────────────────────────────────────────────
const MONITOR_INTERVAL_MS = 30000;

// ── Invite deep-link ──────────────────────────────────────────────
const INVITE_REGEX = /^procta:\/\/invite\/([A-Za-z0-9_\-]{8,128})\/?$/i;

// ── Lobby window defaults ─────────────────────────────────────────
const LOBBY_WIDTH  = 1180;
const LOBBY_HEIGHT = 820;
const LOBBY_MIN_W  = 900;
const LOBBY_MIN_H  = 640;

// ── Exam window defaults ──────────────────────────────────────────
const EXAM_WIDTH  = 1280;
const EXAM_HEIGHT = 900;

// ── Setup window defaults ─────────────────────────────────────────
const SETUP_WIDTH  = 520;
const SETUP_HEIGHT = 420;

module.exports = {
  SERVER_URL, ADMIN_CODE, KIOSK_ALLOWED,
  BLOCKED_SHORTCUTS, PANIC_SHORTCUT, EMERGENCY_SHORTCUT,
  VM_MAC_PREFIXES, VM_GPU_RENDERERS, BLOCKING_TYPES,
  THREATS, ALL_PROCESSES, SCAN_TYPE_MAP,
  getPythonCandidates, getScriptPathCandidates, PIP_PACKAGES,
  POLL_INTERVAL_MS, IGNORED_EVENT_TYPES, MONITOR_INTERVAL_MS,
  INVITE_REGEX,
  LOBBY_WIDTH, LOBBY_HEIGHT, LOBBY_MIN_W, LOBBY_MIN_H,
  EXAM_WIDTH, EXAM_HEIGHT,
  SETUP_WIDTH, SETUP_HEIGHT,
};
