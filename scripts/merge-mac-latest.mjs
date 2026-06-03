// merge-mac-latest.mjs — combine per-arch latest-mac.yml manifests into one.
//
// WHY THIS EXISTS:
// The per-arch CI matrix builds arm64 (macos-14) and x64 (macos-13) in
// SEPARATE jobs. Each job's electron-builder writes its own latest-mac.yml
// listing only ITS arch's files. electron-updater, however, reads ONE
// latest-mac.yml and picks the entry matching the running Mac's arch — so a
// release needs a SINGLE manifest whose `files:` array contains BOTH arches'
// zip+dmg. If we uploaded the two per-job manifests to the release they'd
// clobber each other and half of all Macs would get "no update found" (or,
// worse, the wrong-arch binary). This script reconstructs the combined
// manifest the old single-invocation build used to emit.
//
// Artifact names are made deterministic by package.json build.mac.artifactName
// ("${productName}-${version}-${arch}-mac.${ext}"), so the two jobs can never
// collide on a filename and the merge is a straight union keyed by url.
//
// Usage:
//   node scripts/merge-mac-latest.mjs <in1.yml> <in2.yml> [...] -o <out.yml>
//
// Exits non-zero on any structural surprise (missing files, no zip entry) —
// a broken auto-update manifest must fail the release, not ship silently.

import { readFileSync, writeFileSync } from 'node:fs';
import yaml from 'js-yaml';

const args = process.argv.slice(2);
const outIdx = args.indexOf('-o');
if (outIdx === -1 || outIdx === args.length - 1) {
  console.error('usage: merge-mac-latest.mjs <in...> -o <out.yml>');
  process.exit(2);
}
const outPath = args[outIdx + 1];
const inputs = args.slice(0, outIdx);
if (inputs.length < 1) {
  console.error('merge-mac-latest: need at least one input manifest');
  process.exit(2);
}

const docs = inputs.map((p) => {
  const doc = yaml.load(readFileSync(p, 'utf8'));
  if (!doc || typeof doc !== 'object' || !Array.isArray(doc.files)) {
    console.error(`merge-mac-latest: ${p} is not a valid latest-mac.yml (no files[])`);
    process.exit(1);
  }
  return doc;
});

// All inputs must share one version (same release tag). A mismatch means we
// gathered manifests from two different builds — refuse rather than ship a
// Frankenstein release.
const versions = [...new Set(docs.map((d) => String(d.version)))];
if (versions.length !== 1) {
  console.error(`merge-mac-latest: version mismatch across manifests: ${versions.join(', ')}`);
  process.exit(1);
}

// Union the files[] arrays, de-duping by url (first writer wins). Keeps the
// per-file sha512+size electron-updater verifies the download against.
const byUrl = new Map();
for (const d of docs) {
  for (const f of d.files) {
    if (!f || typeof f.url !== 'string') {
      console.error('merge-mac-latest: a files[] entry is missing its url');
      process.exit(1);
    }
    if (!byUrl.has(f.url)) byUrl.set(f.url, f);
  }
}
const files = [...byUrl.values()];

// electron-updater's legacy top-level path/sha512 fallback. Prefer the arm64
// zip (Apple Silicon is the majority of new Macs); else the first zip; else
// the first file. Modern clients ignore this and match from files[] by arch.
const zips = files.filter((f) => f.url.endsWith('.zip'));
if (zips.length === 0) {
  console.error('merge-mac-latest: no .zip in merged files[] — electron-updater needs the zip');
  process.exit(1);
}
const head = zips.find((f) => f.url.includes('arm64')) || zips[0];

// Latest releaseDate across inputs (they should be seconds apart at most).
const releaseDate = docs
  .map((d) => d.releaseDate)
  .filter(Boolean)
  .sort()
  .pop() || new Date().toISOString();

const merged = {
  version: versions[0],
  files,
  path: head.url,
  sha512: head.sha512,
  releaseDate,
};

writeFileSync(outPath, yaml.dump(merged, { lineWidth: -1 }));
console.log(`merge-mac-latest: wrote ${outPath} with ${files.length} files:`);
for (const f of files) console.log(`  - ${f.url}`);
