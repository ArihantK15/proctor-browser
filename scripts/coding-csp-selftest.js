/* Edge Compiler — procta-lobby:// worker/CSP self-test.
 *
 * PASTE THIS INTO THE ELECTRON DEVTOOLS CONSOLE while sitting on the EXAM page
 * (origin procta-lobby://) of a build made from feat/edge-phase1. It answers the
 * ONE residual the off-device Chromium run couldn't: does
 *   new Worker('coding-worker.js')
 * spawn AND eval student code under the real custom scheme + the kiosk CSP
 * (default-src 'self', no 'unsafe-eval', no worker-src)?
 *
 * No backend, no seeded coding question, no camera needed beyond reaching the
 * exam page. ✅ + zero CSP violations in the console = residual settled.
 * ❌ (worker blocked) = the one-line worker-src CSP fix is genuinely needed.
 */
(async () => {
  try {
    const w = new Worker('coding-worker.js');          // same-origin under procta-lobby://
    const res = await new Promise((resolve, reject) => {
      w.onmessage = (e) => resolve(e.data);
      w.onerror   = (e) => reject(new Error(e.message || 'worker error event'));
      w.postMessage({
        source: "const a = readline().split(' ').map(Number); print(a[0] + a[1]);",
        stdin: "2 3",
      });
      setTimeout(() => reject(new Error('timeout — no worker reply in 3s')), 3000);
    });
    w.terminate();
    console.log('[CSP SELFTEST] raw worker reply:', res);
    if (res && String(res.stdout).trim() === '5') {
      console.log('%c✅ PASS — worker spawned + new Function eval ran under procta-lobby:// (2+3=5)',
                  'color:#137333;font-weight:700');
    } else {
      console.log('%c❌ FAIL — worker replied but output is wrong: ' + JSON.stringify(res),
                  'color:#c5221f;font-weight:700');
    }
  } catch (e) {
    console.error('%c❌ FAIL — worker did not run (likely CSP-blocked under procta-lobby://): ' + e.message,
                  'color:#c5221f;font-weight:700');
    console.error('If this is a CSP error, the fix is worker-src self in the kiosk CSP — ping the team before relaxing it.');
  }
})();
