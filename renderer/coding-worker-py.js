/* coding-worker-py.js — Pyodide execution worker for the Edge Compiler kiosk.
 * Loaded SAME-ORIGIN via new Worker('coding-worker-py.js') so it runs under the
 * kiosk CSP with no blob:/relaxation. WARM: Pyodide loads once and is reused
 * across a question's test cases (init is ~1-2s). Same postMessage reply shape
 * as coding-worker.js: {stdout, stderr, time_ms, mem_kb}.
 *
 * SECURITY — load-then-lockdown: Pyodide needs fetch/importScripts to load, so
 * we (1) load it from the cached procta-pyodide:// dir, (2) THEN null every
 * egress vector, (3) ONLY THEN accept student code. Python network (urllib /
 * pyodide.http) dies with the JS fetch it bridges to.
 *
 * NOTE: Unlike coding-worker.js (which nulls egress immediately at parse time),
 * this worker MUST defer lockdown until after Pyodide is loaded. The lockdown()
 * function is called inside loadPy(), immediately after loadPyodide() resolves,
 * before any student code is executed.
 */

let _pyReady = null;     // Promise<pyodide> — memoized; reused across test cases
let _locked = false;

function lockdown() {
  if (_locked) return; _locked = true;
  self.fetch            = null;
  self.WebSocket        = null;
  self.XMLHttpRequest   = null;
  self.EventSource      = null;
  self.Worker           = null;          // block nested-worker egress
  self.SharedWorker     = null;
  self.importScripts    = function () { throw new Error('importScripts is disabled'); };
  try { if (self.navigator && self.navigator.sendBeacon) self.navigator.sendBeacon = function () { return false; }; } catch (e) {}
  try { self.caches = null; } catch (e) {}
}

function loadPy() {
  if (_pyReady) return _pyReady;
  _pyReady = (async () => {
    // importScripts the loader BEFORE lockdown (lockdown disables importScripts).
    // procta-pyodide:// is a custom Electron protocol registered in T3 that
    // serves Pyodide files from the userData cache directory.
    self.importScripts('procta-pyodide://py/pyodide.js');
    const py = await self.loadPyodide({ indexURL: 'procta-pyodide://py/' });
    lockdown();   // egress sealed immediately after Pyodide init; before any student code
    return py;
  })();
  return _pyReady;
}

self.onmessage = async function (ev) {
  const source = (ev.data && ev.data.source) || '';
  const stdin  = (ev.data && ev.data.stdin)  || '';

  var out = [];
  var err = null;
  var t0 = (self.performance && performance.now) ? performance.now() : Date.now();

  try {
    var py = await loadPy();

    // ----- stdin readline shim -----
    // Split on \n (normalise \r\n first). Pyodide's stdin() is called once
    // per readline(); return null when exhausted (signals EOF to Python).
    var inLines = stdin.length ? String(stdin).replace(/\r\n/g, '\n').split('\n') : [];
    var inPos = 0;
    py.setStdin({ stdin: () => (inPos < inLines.length ? inLines[inPos++] : null) });

    // ----- stdout capture -----
    // batched mode: callback receives one complete line (no trailing \n).
    // Join with '\n' to match the JS worker's out.join('\n') shape.
    py.setStdout({ batched: (s) => out.push(s) });

    // Program-written stderr is silenced (not folded into stdout) — the judge
    // compares stdout only. Runtime/syntax errors surface via the catch below
    // as `err`. This is intentional; flagged for confirmation on the on-device
    // spike.
    py.setStderr({ batched: () => {} });

    // Fresh namespace per test so globals don't leak between test cases (warm
    // interpreter, clean scope).
    var ns = py.toPy({});
    py.runPython(source, { globals: ns });
    ns.destroy();
  } catch (e) {
    err = (e && e.message) ? String(e.message) : String(e);
  }

  var t1 = (self.performance && performance.now) ? performance.now() : Date.now();

  self.postMessage({
    stdout:   out.join('\n'),
    stderr:   err,
    // time_ms: wall-clock milliseconds with 3-decimal (microsecond) precision.
    // Formula mirrors coding-worker.js exactly: performance.now() is already
    // in ms, so *1000/1000 rounds to 3 decimal places.
    time_ms:  Math.max(0, Math.round((t1 - t0) * 1000) / 1000),
    // mem_kb: performance.memory is unavailable in Workers (Chromium exposes it
    // only on the window). Return null to match the worker's own null path.
    mem_kb:   null,
  });
};
