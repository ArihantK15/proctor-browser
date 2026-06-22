/* Edge Compiler — Pyodide / procta-pyodide:// CSP self-test (P2-S0 spike).
 *
 * PASTE THIS INTO THE ELECTRON DEVTOOLS CONSOLE while sitting on the EXAM page
 * (origin procta-lobby://) of a build made from feat/coding-phase2-python.
 *
 * What it settles:
 *   1. Can the worker load cross-scheme files from procta-pyodide:// via
 *      importScripts + loadPyodide({indexURL}) under the kiosk CSP?
 *   2. Does WASM compile without needing 'wasm-unsafe-eval'?
 *   3. Does student Python code execute and return the right stdout?
 *
 * No backend, no seeded question, no camera needed — just reach any exam page.
 * ✅ printed + ZERO CSP violations in console = gating spike passes.
 * ❌ = see the docs/CODING_PYTHON_ONDEVICE_PASS.md Stage A failure table.
 *
 * NOTE: The Pyodide cache must be provisioned in userData before running this
 * (either by launching the kiosk normally so ensurePyodide() fires, or by
 * manually copying the Pyodide files into the pyodide-cache dir). Otherwise
 * the worker will 404 on procta-pyodide://py/pyodide.js.
 */
(async () => {
  const TIMEOUT_MS = 30_000; // Pyodide init can take ~1-2 s on a warm cache

  function log(msg, style) {
    if (style) console.log('%c' + msg, style);
    else        console.log(msg);
  }

  log('[PYODIDE SELFTEST] spawning coding-worker-py.js under procta-lobby://...');

  let w;
  try {
    w = new Worker('coding-worker-py.js');   // same-origin under procta-lobby://
  } catch (e) {
    log('❌ FAIL — new Worker("coding-worker-py.js") threw synchronously: ' + e.message,
        'color:#c5221f;font-weight:700');
    log('This means the worker file is missing from the build or the CSP blocks worker-src self.',
        'color:#c5221f');
    return;
  }

  log('[PYODIDE SELFTEST] worker spawned — waiting for Pyodide to init (up to ' +
      (TIMEOUT_MS / 1000) + 's)...');

  const result = await new Promise((resolve) => {
    const timer = setTimeout(() => {
      resolve({ __timedOut: true });
    }, TIMEOUT_MS);

    w.onmessage = (e) => {
      clearTimeout(timer);
      resolve(e.data);
    };

    w.onerror = (e) => {
      clearTimeout(timer);
      resolve({ __error: e.message || 'worker onerror event' });
    };

    // Simple Python: read two ints (one per line), print sum.
    // Input "2\n3\n" → expected stdout "5"
    w.postMessage({
      source: 'a = int(input())\nb = int(input())\nprint(a + b)',
      stdin:  '2\n3\n',
    });
  });

  if (w) w.terminate();

  if (result.__timedOut) {
    log('❌ FAIL — worker timed out after ' + (TIMEOUT_MS / 1000) + 's. ' +
        'Pyodide may still be loading (cache cold?), or importScripts was blocked by CSP.',
        'color:#c5221f;font-weight:700');
    return;
  }

  if (result.__error) {
    log('❌ FAIL — worker error: ' + result.__error,
        'color:#c5221f;font-weight:700');
    log('Check for CSP violations in the console. If you see "Content Security Policy" ' +
        'errors referencing procta-pyodide:// or wasm-unsafe-eval, see the runbook.',
        'color:#c5221f');
    return;
  }

  log('[PYODIDE SELFTEST] raw worker reply: ' + JSON.stringify(result));

  if (result && String(result.stdout).trim() === '5') {
    log(
      '✅ PASS — Pyodide loaded from procta-pyodide://, WASM compiled, Python ran, ' +
      'lockdown applied — zero egress after init. 2+3=' + result.stdout.trim(),
      'color:#137333;font-weight:700'
    );
    log('Gating spike P2-S0 is settled. Check for zero CSP violations above.',
        'color:#137333');
  } else {
    log('❌ FAIL — worker replied but stdout is wrong. Expected "5", got: ' +
        JSON.stringify(result.stdout) + ' (stderr: ' + JSON.stringify(result.stderr) + ')',
        'color:#c5221f;font-weight:700');
  }
})();
