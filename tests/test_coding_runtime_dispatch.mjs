import { test } from 'node:test';
import assert from 'node:assert';

// Minimal Worker mock: echoes a deterministic stdout per message.
globalThis.self = globalThis;
globalThis.Worker = class {
  constructor() {}
  postMessage(m) {
    setTimeout(() => this.onmessage({ data: { stdout: 'ok:' + (m.stdin || ''), time_ms: 1, mem_kb: null } }), 0);
  }
  terminate() {}
};

// coding-runtime.js is an IIFE that exports via module.exports (CJS interop).
// Under ESM `import()`, Node provides the default export as `module.exports`.
const mod = await import('../renderer/coding-runtime.js');
const runTestCases = mod.runTestCases || (mod.default && mod.default.runTestCases);

test('python is a supported language and returns the contract shape', async () => {
  const r = await runTestCases('python', 'print(1)', ['a', 'b']);
  assert.strictEqual(r.outputs.length, 2);
  assert.strictEqual(r.metrics.length, 2);
  assert.ok('time_ms' in r.metrics[0] && 'timed_out' in r.metrics[0]);
});

test('unknown language still rejects', async () => {
  await assert.rejects(() => runTestCases('ruby', 'puts 1', ['x']));
});
