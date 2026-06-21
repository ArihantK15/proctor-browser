/* Kiosk coding runtime API — P1-T6. Single entry point the renderer calls:
 *
 *   CodingRuntime.runTestCases(language, source, stdins[], {limitMs}) ->
 *       Promise<{ outputs:[string|null], metrics:[{ms, timeout?, error?}] }>
 *
 * Spawns the SAME-ORIGIN worker (coding-worker.js — not a Blob; CSP-clean per the
 * locked decision), runs each test's stdin with a MAIN-THREAD wall-clock watchdog,
 * and on overrun TERMINATES + RESPAWNS the worker (an infinite loop can't be
 * recovered any other way). Phase 1 is JavaScript-only; Pyodide/Clang/Doppio land
 * in Phase 2-4 behind THIS contract and the same same-origin-worker pattern.
 */
(function (global) {
  var WORKER_URL = 'coding-worker.js';   // same-origin, flat (no vendor/ subdir)
  var DEFAULT_LIMIT_MS = 5000;

  function _ask(worker, msg, limitMs) {
    return new Promise(function (resolve) {
      var done = false;
      var timer = setTimeout(function () {
        if (done) return; done = true;
        try { worker.terminate(); } catch (e) {}
        resolve({ ev: 'ran', ok: false, timeout: true, err: 'timeout' });
      }, limitMs);
      worker.onmessage = function (e) {
        if (done) return; done = true; clearTimeout(timer); resolve(e.data);
      };
      worker.onerror = function (e) {
        if (done) return; done = true; clearTimeout(timer);
        resolve({ ev: 'ran', ok: false, err: String((e && e.message) || 'worker error') });
      };
      worker.postMessage(msg);
    });
  }

  async function runTestCases(language, source, stdins, opts) {
    opts = opts || {};
    var limitMs = opts.limitMs || DEFAULT_LIMIT_MS;
    stdins = stdins || [];
    if (language !== 'javascript') {
      return {
        outputs: stdins.map(function () { return null; }),
        metrics: stdins.map(function () { return {}; }),
        error: 'language "' + language + '" not supported in this build (Phase 1 = JavaScript only)',
      };
    }
    var worker = new Worker(WORKER_URL);
    var outputs = [], metrics = [];
    try {
      for (var i = 0; i < stdins.length; i++) {
        var r = await _ask(worker, { cmd: 'run', source: source, stdin: stdins[i] }, limitMs);
        if (r && r.timeout) {
          outputs.push(null);
          metrics.push({ timeout: true, ms: limitMs });
          worker = new Worker(WORKER_URL);   // respawn after terminate
        } else if (r && r.ok) {
          outputs.push(r.stdout);
          metrics.push({ ms: r.runMs });
        } else {
          outputs.push(null);
          metrics.push({ ms: (r && r.runMs) || 0, error: (r && r.err) || 'error' });
        }
      }
    } finally {
      try { worker.terminate(); } catch (e) {}
    }
    return { outputs: outputs, metrics: metrics };
  }

  global.CodingRuntime = { runTestCases: runTestCases };
})(typeof self !== 'undefined' ? self : this);
