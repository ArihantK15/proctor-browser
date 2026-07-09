// Regression test for the real bug found testing on an actual Windows CI
// runner: archiveRuntimeAssets() used to rename a non-"python-runtime"
// source directory into the archive via GNU tar's `-s` transform flag,
// which Windows' bundled bsdtar rejects outright ("tar -s is not supported
// by this version of bsdtar"). Fixed by staging a real python-runtime/
// directory via a plain recursive copy instead — this test proves the
// archive it produces actually contains a top-level python-runtime/ entry
// (not the original source dir name) via a real tar round-trip, so a
// regression here fails locally instead of only on a real Windows runner.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { archiveRuntimeAssets } from '../bundle-python.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(__dirname, '..');

test('archiveRuntimeAssets renames a non-"python-runtime" source dir inside the archive without relying on tar -s', async (t) => {
  const fakeSrcName = '.test-fake-runtime-src';
  const fakeSrcDir = path.join(repoRoot, fakeSrcName);
  const archiveName = `test-runtime-archive-verify-${Date.now()}.tar.gz`;
  const archivePath = path.join(repoRoot, archiveName);
  const extractDir = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-verify-'));

  fs.mkdirSync(fakeSrcDir, { recursive: true });
  fs.writeFileSync(path.join(fakeSrcDir, 'marker.txt'), 'fake interpreter file');

  t.after(() => {
    fs.rmSync(fakeSrcDir, { recursive: true, force: true });
    fs.rmSync(archivePath, { force: true });
    fs.rmSync(extractDir, { recursive: true, force: true });
  });

  archiveRuntimeAssets(archiveName, fakeSrcName);

  assert.ok(fs.existsSync(archivePath), 'archive was not produced');

  execFileSync('tar', ['-xzf', archivePath, '-C', extractDir], { stdio: 'inherit' });

  assert.ok(
    fs.existsSync(path.join(extractDir, 'python-runtime', 'marker.txt')),
    'archive should contain python-runtime/marker.txt (renamed from the fake source dir), not the original source dir name'
  );
  assert.ok(!fs.existsSync(path.join(extractDir, fakeSrcName)),
    'archive should NOT contain the original source dir name');
  assert.ok(fs.existsSync(path.join(extractDir, 'weights')),
    'archive should also contain weights/');
});
