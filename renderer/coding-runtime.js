/*
 * coding-runtime.js — Edge Compiler client runtime API. Phase 1: JavaScript ONLY.
 *
 * Runs UNTRUSTED student code in a nullified SAME-ORIGIN Web Worker
 * (coding-worker.js), one test at a time, with a MAIN-THREAD wall-clock
 * watchdog that terminates + respawns the worker on timeout. Loaded by the
 * kiosk renderer; runs under the kiosk CSP (default-src 'self') without any
 * blob:/CSP relaxation.
 *
 * Public contract (stable — renderer + server lane code against this):
 *   runTestCases(language, source, stdins[], opts?) ->
 *       Promise<{ outputs: string[], metrics: Array<{
 *           time_ms: number, mem_kb: number|null,
 *           timed_out: boolean, error: string|null }> }>
 *   outputs[i] is the program's stdout for stdins[i] ('' on timeout/error).
 *
 * Student I/O contract (for problem authors):
 *   - console.log(...) / print(...)  -> captured as stdout (one line per call).
 *   - readline() / input()           -> returns the next stdin line ('' at EOF).
 */
(function (global) {
  'use strict';

  var DEFAULT_LIMIT_MS = 2000;
  var SUPPORTED = { javascript: true, js: true };
  // Same-origin flat file (the procta-lobby protocol serves renderer/<name>).
  // Resolved relative to the page, so procta-lobby://exam/coding-worker.js.
  var WORKER_URL = 'coding-worker.js';

  // Run ONE test in a fresh worker. The main-thread watchdog wins the race on
  // timeout: terminate the worker, mark the test timed_out, move on.
  function runSingle(source, stdin, limitMs) {
    return new Promise(function (resolve) {
      var worker, timer, done = false;

      function finish(result) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        try { if (worker) worker.terminate(); } catch (e) {}
        resolve(result);
      }

      try {
        worker = new Worker(WORKER_URL);
      } catch (e) {
        resolve({
          output: '',
          metric: { time_ms: 0, mem_kb: null, timed_out: false,
                    error: 'worker_spawn_failed: ' + e }
        });
        return;
      }

      timer = setTimeout(function () {
        finish({ output: '',
                 metric: { time_ms: limitMs, mem_kb: null, timed_out: true, error: null } });
      }, limitMs);

      worker.onmessage = function (ev) {
        var d = ev.data || {};
        finish({
          output: d.stdout != null ? d.stdout : '',
          metric: { time_ms: d.time_ms || 0,
                    mem_kb: d.mem_kb != null ? d.mem_kb : null,
                    timed_out: false,
                    error: d.stderr || null }
        });
      };
      worker.onerror = function (e) {
        try { if (e && e.preventDefault) e.preventDefault(); } catch (_) {}
        finish({ output: '',
                 metric: { time_ms: 0, mem_kb: null, timed_out: false,
                           error: 'worker_error: ' + (e && e.message ? e.message : e) } });
      };

      worker.postMessage({ source: source, stdin: stdin });
    });
  }

  /**
   * Run `source` against each stdin sequentially. Each test gets a FRESH worker
   * (clean global state + clean timeout/terminate semantics). One worker is
   * alive at a time, bounding the footprint while live proctoring shares the box.
   */
  function runTestCases(language, source, stdins, opts) {
    opts = opts || {};
    var limitMs = opts.limit_ms || opts.limitMs || DEFAULT_LIMIT_MS;
    var lang = String(language || '').toLowerCase();

    if (!SUPPORTED[lang]) {
      return Promise.reject(new Error(
        'coding-runtime: language "' + language +
        '" is not supported in Phase 1 (JavaScript only)'));
    }
    if (!Array.isArray(stdins)) stdins = [];
    source = source == null ? '' : String(source);

    var outputs = [];
    var metrics = [];

    var chain = Promise.resolve();
    stdins.forEach(function (raw) {
      chain = chain.then(function () {
        return runSingle(source, raw == null ? '' : String(raw), limitMs);
      }).then(function (r) {
        outputs.push(r.output);
        metrics.push(r.metric);
      });
    });

    return chain.then(function () {
      return { outputs: outputs, metrics: metrics };
    });
  }

  global.runTestCases = runTestCases;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { runTestCases: runTestCases };
  }
})(typeof self !== 'undefined' ? self : this);
