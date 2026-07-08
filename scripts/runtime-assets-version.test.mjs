import { test } from 'node:test';
import assert from 'node:assert/strict';
import { needsRuntimeAssetFetch } from '../lib/runtime-assets-version.js';

function fakeFs(markerContents) {
  return {
    existsSync: (p) => markerContents !== null && String(p).endsWith('.version'),
    readFileSync: (p, enc) => {
      if (markerContents === null) throw new Error('ENOENT');
      return markerContents;
    },
  };
}

test('returns true (needs fetch) when no marker file exists at all', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs(null)), true);
});

test('returns false (cache is current) when the marker matches the expected version', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('3')), false);
});

test('returns true (needs fetch) when the marker is for an OLDER version', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('2')), true);
});

test('returns true (needs fetch) when the marker file is empty/corrupt', () => {
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('')), true);
});

test('returns true (needs fetch) when the marker has trailing whitespace mismatching the expected version', () => {
  // Guards against a marker file written with a trailing newline being
  // treated as a version mismatch forever — trim before comparing.
  assert.equal(needsRuntimeAssetFetch('3', '/fake/cache', fakeFs('3\n')), false);
});
