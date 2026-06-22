/*
 * coding-runtime.js — Edge Compiler client runtime API.
 * Languages: JavaScript, TypeScript (sucrase), Python (Pyodide warm worker).
 *
 * Runs UNTRUSTED student code in a nullified SAME-ORIGIN Web Worker, one test
 * at a time, with a MAIN-THREAD wall-clock watchdog that terminates + respawns
 * the worker on timeout. Loaded by the kiosk renderer; runs under the kiosk
 * CSP (default-src 'self') without any blob:/CSP relaxation.
 *
 * JS/TS path: coding-worker.js — FRESH worker per test case.
 * Python path: coding-worker-py.js — WARM worker per batch; Pyodide loads once
 *   and is reused across test cases. On timeout the warm worker is terminated
 *   and a fresh one is spawned for the remaining tests.
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
  // TypeScript is supported by transpiling to JS on the main thread (sucrase)
  // and running the result through the same JS worker path — see runTestCases.
  var SUPPORTED = { javascript: true, js: true, typescript: true, ts: true,
                    python: true, py: true };
  // Same-origin flat file (the procta-lobby protocol serves renderer/<name>).
  // Resolved relative to the page, so procta-lobby://exam/coding-worker.js.
  var WORKER_URL = 'coding-worker.js';
  // Warm Pyodide worker — ONE per batch, reused across test cases.
  var PY_WORKER_URL = 'coding-worker-py.js';

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
   * Run `source` against each stdin in `stdins` using ONE warm Pyodide worker.
   * Pyodide loads once; each test posts to the same worker (fresh namespace).
   * Per-test watchdog: on timeout, terminate the current worker (kills Pyodide),
   * record the timed_out result, then respawn a fresh worker for remaining tests.
   * The active worker is always terminated at the end of the batch.
   */
  function runPythonBatch(source, stdins, limitMs) {
    return new Promise(function (resolve) {
      var outputs = [];
      var metrics = [];
      var worker = null;

      function spawnWorker() {
        try {
          return new Worker(PY_WORKER_URL);
        } catch (e) {
          return null;
        }
      }

      function runNext(idx, currentWorker) {
        if (idx >= stdins.length) {
          // Batch complete — terminate the surviving worker and resolve.
          try { if (currentWorker) currentWorker.terminate(); } catch (e) {}
          resolve({ outputs: outputs, metrics: metrics });
          return;
        }

        var stdin = stdins[idx] == null ? '' : String(stdins[idx]);
        var done = false;
        var timer;

        if (!currentWorker) {
          // Worker spawn failed for this test — push an error result and advance.
          outputs.push('');
          metrics.push({ time_ms: 0, mem_kb: null, timed_out: false,
                         error: 'worker_spawn_failed' });
          runNext(idx + 1, null);
          return;
        }

        function finishTest(output, metric, nextWorker) {
          if (done) return;
          done = true;
          clearTimeout(timer);
          outputs.push(output);
          metrics.push(metric);
          runNext(idx + 1, nextWorker);
        }

        timer = setTimeout(function () {
          // Terminate the warm worker (Pyodide dies with it) and respawn.
          try { currentWorker.terminate(); } catch (e) {}
          var fresh = spawnWorker();
          finishTest('',
            { time_ms: limitMs, mem_kb: null, timed_out: true, error: null },
            fresh);
        }, limitMs);

        currentWorker.onmessage = function (ev) {
          var d = ev.data || {};
          finishTest(
            d.stdout != null ? d.stdout : '',
            { time_ms: d.time_ms || 0,
              mem_kb: d.mem_kb != null ? d.mem_kb : null,
              timed_out: false,
              error: d.stderr || null },
            currentWorker  // keep the warm worker alive for the next test
          );
        };

        currentWorker.onerror = function (e) {
          try { if (e && e.preventDefault) e.preventDefault(); } catch (_) {}
          // Worker error is unrecoverable — spawn fresh for remaining tests.
          try { currentWorker.terminate(); } catch (_) {}
          var fresh = spawnWorker();
          finishTest('',
            { time_ms: 0, mem_kb: null, timed_out: false,
              error: 'worker_error: ' + (e && e.message ? e.message : e) },
            fresh);
        };

        currentWorker.postMessage({ source: source, stdin: stdin });
      }

      worker = spawnWorker();
      runNext(0, worker);
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
        'coding-runtime: language "' + language + '" is not supported'));
    }
    if (!Array.isArray(stdins)) stdins = [];
    source = source == null ? '' : String(source);

    // Python: warm Pyodide worker — ONE worker for the whole batch.
    if (lang === 'python' || lang === 'py') {
      return runPythonBatch(source, stdins, limitMs);
    }

    // TypeScript: transpile to JS once, on the main thread, via the bundled
    // sucrase (window.transformTS from sucrase.bundle.js). The worker only ever
    // sees JS, so coding-worker.js needs no TS awareness. A compile error fails
    // every test with the compiler message (matches how runtime errors surface).
    if (lang === 'typescript' || lang === 'ts') {
      var transformTS = global.transformTS;
      if (typeof transformTS !== 'function') {
        return Promise.reject(new Error(
          'coding-runtime: TypeScript transpiler not loaded (sucrase.bundle.js)'));
      }
      try {
        source = String(transformTS(source));
      } catch (e) {
        var msg = 'TypeScript compile error: ' + (e && e.message ? e.message : e);
        var failOutputs = [], failMetrics = [];
        stdins.forEach(function () {
          failOutputs.push('');
          failMetrics.push({ time_ms: 0, mem_kb: null, timed_out: false, error: msg });
        });
        return Promise.resolve({ outputs: failOutputs, metrics: failMetrics });
      }
    }

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
