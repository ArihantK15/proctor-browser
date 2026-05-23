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

function extractInviteToken(urlOrArg, regex) {
  try {
    if (!urlOrArg) return null;
    const s = String(urlOrArg);
    const m = s.match(regex);
    return m ? m[1] : null;
  } catch { return null; }
}

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
          const evidence = ln.trim().slice(0, 120);
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

module.exports = { _exec, authHeaders, extractInviteToken, scanProcessOutput, fetchWithTimeout };
