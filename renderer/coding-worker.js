/*
 * coding-worker.js — sandboxed execution worker for the Edge Compiler kiosk
 * runtime (Phase 1: JavaScript). Loaded SAME-ORIGIN via
 * new Worker('coding-worker.js') so it runs under the kiosk CSP
 * (default-src 'self') with NO blob:/CSP relaxation, and — because the worker
 * response carries no CSP of its own — student code can be eval'd here even
 * though the page CSP omits 'unsafe-eval'. (A blob: worker would inherit the
 * page CSP and fail both checks; that is exactly why Option A is used.)
 *
 * SECURITY: the FIRST executable lines null out every network-egress vector
 * (fetch / WebSocket / XMLHttpRequest / EventSource / sendBeacon) AND the
 * nested-context vectors (Worker / SharedWorker — a fresh nested worker would
 * otherwise get a non-null fetch) AND guard importScripts. Student code thus
 * has no path to the network and no DOM/filesystem (worker isolation).
 */
self.fetch = null;
self.WebSocket = null;
self.XMLHttpRequest = null;
self.EventSource = null;
self.Worker = null;          // block nested-worker egress
self.SharedWorker = null;
self.importScripts = function () { throw new Error('importScripts is disabled'); };
try { if (self.navigator && self.navigator.sendBeacon) self.navigator.sendBeacon = function () { return false; }; } catch (e) {}
try { self.caches = null; } catch (e) {}

self.onmessage = function (ev) {
  var source = (ev.data && ev.data.source) || '';
  var stdin  = (ev.data && ev.data.stdin) || '';

  // ----- stdout capture (console.log / print -> one line per call) -----
  var out = [];
  function stringify(v) {
    try { return (typeof v === 'object' && v !== null) ? JSON.stringify(v) : String(v); }
    catch (e) { return String(v); }
  }
  function emit() {
    var parts = [];
    for (var i = 0; i < arguments.length; i++) {
      var a = arguments[i];
      parts.push(typeof a === 'string' ? a : stringify(a));
    }
    out.push(parts.join(' '));
  }
  self.console = { log: emit, info: emit, warn: emit, error: emit, debug: emit };

  // ----- stdin readline shim -----
  var inLines = stdin.length ? stdin.replace(/\r\n/g, '\n').split('\n') : [];
  var inPos = 0;
  function readline() { return inPos < inLines.length ? inLines[inPos++] : ''; }

  // ----- memory (best-effort; performance.memory is window-only in Chromium,
  //        so this is typically null inside a Worker) -----
  function memNow() {
    try { return (self.performance && performance.memory) ? performance.memory.usedJSHeapSize : null; }
    catch (e) { return null; }
  }
  function memDeltaKb(a, b) {
    if (a == null || b == null) return null;
    var d = b - a; if (d < 0) d = b;
    return Math.round(d / 1024);
  }

  var t0 = (self.performance && performance.now) ? performance.now() : Date.now();
  var memBefore = memNow();
  var err = null;
  try {
    // new Function gives the source a function scope with the shims as params.
    // (console is also overridden globally above, so direct console.log works.)
    var fn = new Function('readline', 'input', 'print', 'console', source);
    fn(readline, readline, emit, self.console);
  } catch (e) {
    err = (e && e.stack) ? String(e.stack) : String(e);
  }
  var t1 = (self.performance && performance.now) ? performance.now() : Date.now();
  var memAfter = memNow();

  self.postMessage({
    stdout: out.join('\n'),
    stderr: err,
    time_ms: Math.max(0, Math.round((t1 - t0) * 1000) / 1000),
    mem_kb: memDeltaKb(memBefore, memAfter)
  });
};
