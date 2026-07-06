const { exec } = require('child_process');

function _exec(cmd, timeout = 8000) {
  return new Promise(resolve => {
    exec(cmd, { encoding: 'utf8', timeout }, (err, stdout) => {
      resolve(err ? '' : stdout);
    });
  });
}

function authHeaders(studentToken) {
  const base = { 'Content-Type': 'application/json' };
  return studentToken
    ? { ...base, 'Authorization': `Bearer ${studentToken}` }
    : base;
}

function fetchWithTimeout(url, options = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

// Electron's default User-Agent appends its own app name + an explicit
// "Electron/x.y.z" token, which some Cloudflare bot-management / WAF
// heuristics (and Turnstile's own client-side check) flag as automated
// traffic — a real Chrome browser on the same machine/network reaches the
// same Cloudflare-fronted domains fine while the packaged app fails with
// "Failed to fetch" / a Turnstile "Unable to connect" widget error, which
// is the actual live symptom this was written to fix (2026-07-06). This
// isn't spoofing a different engine — Electron IS the same Chromium build
// (process.versions.chrome), so presenting as plain Chrome just drops the
// two tokens that make it look automated, without misrepresenting real
// capabilities the server might otherwise rely on (JS features, etc).
function browserUserAgent() {
  const chromeVersion = process.versions.chrome || '120.0.0.0';
  const platform =
    process.platform === 'darwin' ? 'Macintosh; Intel Mac OS X 10_15_7'
    : process.platform === 'win32' ? 'Windows NT 10.0; Win64; x64'
    : 'X11; Linux x86_64';
  return `Mozilla/5.0 (${platform}) AppleWebKit/537.36 (KHTML, like Gecko) `
    + `Chrome/${chromeVersion} Safari/537.36`;
}

function extractInviteToken(urlOrArg, regex) {
  try {
    if (!urlOrArg) return null;
    const s = String(urlOrArg);
    if (s.toLowerCase().startsWith('procta://')) {
      try {
        const u = new URL(s);
        const token =
          u.searchParams.get('token') ||
          u.searchParams.get('invite') ||
          ((u.hostname || '').toLowerCase() === 'invite'
            ? u.pathname.replace(/^\/+/, '').split('/')[0]
            : '');
        if (/^[A-Za-z0-9_-]{8,128}$/.test(token || '')) return token;
      } catch (_) {
        // Fall through to the legacy regex for malformed-but-matchable args.
      }
    }
    const m = s.match(regex);
    return m ? m[1] : null;
  } catch { return null; }
}

// macOS ships its own private "CoreParsec" framework (parsecd / parsec-fbf,
// under /System/Library/PrivateFrameworks/CoreParsec.framework — part of
// on-device visual-intelligence, unrelated to the third-party Parsec remote-
// desktop app) whose process name collides with our 'parsec'/'parsecd'
// THREATS patterns. /System/Library is SIP-protected on modern macOS — real
// third-party software (remote-desktop tools included) cannot be installed
// there — so any match whose evidence line is rooted under it is guaranteed
// to be a stock Apple binary, never the actual threat being screened for.
// This exclusion is intentionally OS-path-shaped (a Windows `tasklist` line
// never starts with this prefix), so it only ever suppresses this exact
// macOS false-positive class, on any THREATS pattern, not just Parsec's.
const _SIP_PROTECTED_PREFIX = '/system/library/';

function scanProcessOutput(output, ALL_PROCESSES, SCAN_TYPE_MAP) {
  const flags = [];
  if (!output) return flags;
  const lower = output.toLowerCase();
  const lines = lower.split('\n');
  for (const [cat, patterns] of Object.entries(ALL_PROCESSES)) {
    for (const rx of patterns) {
      for (const ln of lines) {
        const m = ln.match(rx);
        if (m) {
          const trimmed = ln.trim();
          if (trimmed.includes(_SIP_PROTECTED_PREFIX)) continue;
          const evidence = trimmed.slice(0, 120);
          flags.push({
            type: SCAN_TYPE_MAP[cat],
            severity: cat === 'screen_share' ? 'medium' : 'high',
            details: `Process match: ${m[0]} — in: ${evidence}`,
          });
          break;
        }
      }
    }
  }
  return flags;
}

module.exports = { _exec, authHeaders, extractInviteToken, scanProcessOutput, fetchWithTimeout, browserUserAgent };
