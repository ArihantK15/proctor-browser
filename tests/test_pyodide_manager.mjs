import { test } from 'node:test';
import assert from 'node:assert';
import { cacheFilePath, isManifestSatisfied, MANIFEST, resolvePyodideRequest } from '../lib/pyodide-manager.js';

test('cacheFilePath stays inside the cache root', () => {
  const root = '/u/pyodide-cache';
  assert.strictEqual(cacheFilePath(root, 'pyodide.asm.wasm'), '/u/pyodide-cache/pyodide.asm.wasm');
});
test('cacheFilePath rejects traversal', () => {
  assert.throws(() => cacheFilePath('/u/pyodide-cache', '../secrets'));
  assert.throws(() => cacheFilePath('/u/pyodide-cache', 'a/../../b'));
});
test('isManifestSatisfied false when a file is missing from the on-disk set', () => {
  const present = new Set(MANIFEST.slice(1).map(m => m.name)); // drop one
  assert.strictEqual(isManifestSatisfied(present), false);
});
test('isManifestSatisfied true when every manifest file is present', () => {
  const present = new Set(MANIFEST.map(m => m.name));
  assert.strictEqual(isManifestSatisfied(present), true);
});
test('resolvePyodideRequest maps a clean path under the root', () => {
  assert.strictEqual(resolvePyodideRequest('/u/pyc', 'procta-pyodide://py/pyodide.asm.wasm'),
                     '/u/pyc/pyodide.asm.wasm');
});
test('resolvePyodideRequest refuses traversal', () => {
  assert.throws(() => resolvePyodideRequest('/u/pyc', 'procta-pyodide://py/../../etc/passwd'));
});
