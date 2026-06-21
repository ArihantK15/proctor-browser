/* Spike worker — hosts the C++ toolchain in a Web Worker (matches the real kiosk
 * architecture). The MAIN thread (harness.html) owns the per-run wall-clock
 * watchdog and terminates+respawns this worker on overrun, exactly like the
 * production design — so this file just loads / compiles / runs and reports timings.
 *
 * Protocol (postMessage):
 *   main -> worker: {cmd:'load', adapter}        worker -> main: {ev:'loaded', loadMs, payloadBytes}
 *   main -> worker: {cmd:'compile', source}      worker -> main: {ev:'compiled', compileMs, ok, err}
 *   main -> worker: {cmd:'run', stdin}           worker -> main: {ev:'ran', runMs, stdout, ok, err}
 *
 * ADAPTER SEAM: implement these three for the toolchain under test. A 'stub'
 * adapter (pure-JS reimplementation of workload.cpp) ships so the harness wiring,
 * timing, and pass/fail comparison can be verified BEFORE vendoring the ~tens-of-MB
 * Clang assets. Switch adapters via the {adapter} field on the 'load' message.
 */

// ---- ADAPTER: wasm-clang (binji/wasm-clang) — vendor assets into ./vendor/ -----
const wasmClangAdapter = {
  _api: null,
  async load() {
    // ADAPTER: import the vendored wasm-clang API and construct it. The demo API
    // captures stdout via a hostWrite callback. Record the total bytes fetched
    // (clang.wasm + lld.wasm + sysroot.tar) as payloadBytes for the size budget.
    // Example (adjust to the vendored build's exports):
    //   importScripts('./vendor/shared.js', './vendor/api.js');
    //   this._buf = [];
    //   this._api = new API({ hostWrite: (s) => this._buf.push(s),
    //                         clang: './vendor/clang.wasm', lld: './vendor/lld.wasm',
    //                         sysroot: './vendor/sysroot.tar' });
    //   await this._api.ready;            // if the build exposes a ready promise
    //   return { payloadBytes: <sum of asset sizes> };
    throw new Error('wasm-clang assets not vendored — see RUNBOOK §"Vendor the toolchain"');
  },
  async compile(source) {
    // ADAPTER: compile ONCE, keep the produced module. If the vendored build only
    // exposes compileLinkRun(source), keep `source` here and do the work in run()
    // (record that you measured combined compile+run — note it in Results).
    this._source = source;
  },
  async run(stdin) {
    // ADAPTER: run the compiled program against `stdin`, return captured stdout.
    //   this._buf = [];
    //   await this._api.compileLinkRun(this._source, { stdin });   // demo API
    //   return this._buf.join('');
    throw new Error('wasm-clang run not wired — see ADAPTER notes');
  },
};

// ---- ADAPTER: stub (pure JS) — for verifying the harness without the toolchain --
const stubAdapter = {
  async load() { await _sleep(5); return { payloadBytes: 0 }; },
  async compile(source) { this._ok = source.includes('int main'); await _sleep(2); },
  async run(stdin) {
    // Reimplements workload.cpp so the harness end-to-end (timing + comparison)
    // can be smoke-tested. NOT a substitute for the real measurement.
    const parts = stdin.trim().split(/\s+/).map(Number);
    const n = parts.shift();
    const a = parts.slice(0, n);
    let best = a.length ? a[0] : 0, cur = best;
    for (let i = 1; i < a.length; i++) { cur = Math.max(a[i], cur + a[i]); best = Math.max(best, cur); }
    const distinct = new Set(a).size;
    const sorted = [...a].sort((x, y) => x - y);
    await _sleep(1);
    return `${sorted.join(' ')}\n${distinct}\n${best}`;
  },
};

const ADAPTERS = { 'wasm-clang': wasmClangAdapter, 'stub': stubAdapter };
let active = null;

function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function _now() { return (self.performance && performance.now) ? performance.now() : Date.now(); }

self.onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.cmd === 'load') {
      active = ADAPTERS[m.adapter] || stubAdapter;
      const t = _now();
      const info = await active.load();
      self.postMessage({ ev: 'loaded', loadMs: _now() - t, payloadBytes: (info && info.payloadBytes) || 0 });
    } else if (m.cmd === 'compile') {
      const t = _now();
      await active.compile(m.source);
      self.postMessage({ ev: 'compiled', compileMs: _now() - t, ok: true });
    } else if (m.cmd === 'run') {
      const t = _now();
      const stdout = await active.run(m.stdin);
      self.postMessage({ ev: 'ran', runMs: _now() - t, stdout, ok: true });
    }
  } catch (err) {
    self.postMessage({ ev: m.cmd === 'compile' ? 'compiled' : (m.cmd === 'run' ? 'ran' : 'loaded'),
                       ok: false, err: String(err && err.message || err) });
  }
};
