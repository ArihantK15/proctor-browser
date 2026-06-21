/* Kiosk coding worker — P1-T6 (same-origin file, NOT a Blob; CSP-clean per the
 * locked decision in the plan). Host-isolated sandbox: runs untrusted student JS
 * for one test case and captures stdout. Network APIs are nullified — Approach A
 * doesn't rely on the sandbox for grading integrity, only for host safety.
 *
 * `executeJs` is defined first and exported under CommonJS so it can be unit-tested
 * in Node; the worker message plumbing + the network nullification only run in the
 * real Worker context (where `self.postMessage` exists). This split is why the
 * core can be verified off-device while the CSP/worker behaviour is verified on it.
 */
function executeJs(source, stdin) {
  const text = (stdin == null) ? '' : String(stdin);
  const lines = text.split('\n');
  let li = 0;
  const out = [];
  const readline = () => (li < lines.length ? lines[li++] : '');
  const readAll = () => text;
  const print = (...a) => out.push(a.map(String).join(' '));
  // Capture console.log/etc. too — students reach for console.log by habit.
  const sandboxConsole = { log: print, error: print, info: print, warn: print, debug: print };
  // new Function = host-isolated eval inside the worker. "use strict" so the
  // student source can't leak globals as easily.
  const fn = new Function('readline', 'readAll', 'print', 'console',
                          '"use strict";\n' + String(source || ''));
  fn(readline, readAll, print, sandboxConsole);
  return out.join('\n');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { executeJs };   // Node unit-test entry
}

// ---- Worker context only (browser) -----------------------------------------
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  // Nullify network/exfil surfaces. The sandbox is host-isolation, not the
  // integrity boundary (that's server-side judging), but keep it tight.
  try { self.fetch = null; } catch (e) {}
  try { self.WebSocket = null; } catch (e) {}
  try { self.XMLHttpRequest = null; } catch (e) {}
  try { self.importScripts = function () { throw new Error('importScripts disabled'); }; } catch (e) {}

  const _now = () => ((self.performance && self.performance.now) ? self.performance.now() : Date.now());

  self.onmessage = function (e) {
    const m = e.data || {};
    if (m.cmd !== 'run') return;
    const t0 = _now();
    try {
      const stdout = executeJs(m.source, m.stdin);
      self.postMessage({ ev: 'ran', ok: true, stdout: stdout, runMs: _now() - t0 });
    } catch (err) {
      self.postMessage({ ev: 'ran', ok: false, err: String((err && err.message) || err), runMs: _now() - t0 });
    }
  };
}
