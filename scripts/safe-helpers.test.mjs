// scripts/safe-helpers.test.mjs — drift guard for duplicated _safe.js helpers.
//
// WHY THIS EXISTS
// ----------------
// `_detailText` (renders FastAPI error bodies into readable text) lives
// canonically in app/static/_safe.js. Two standalone pages deliberately do NOT
// load _safe.js — privacy.html and the server-rendered invite-accept page are
// compliance/onboarding-critical and must render even if the shared bundle
// fails, and they don't want _safe.js's theme bootstrap / escape helpers. So
// each ships its own copy of _detailText.
//
// Three copies is fine; three copies that silently DIVERGE is not — a bugfix to
// the canonical version would leave the standalone pages mis-rendering 422s.
// This test fails the build if any copy's logic drifts from the canonical one
// (whitespace/formatting differences are ignored; logic changes are not).
//
//   node --test scripts/safe-helpers.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC = join(__dirname, '..', 'app', 'static');

// Extract `function <name>(...) { ... }` from source by brace-matching from the
// first '{' after the signature. Returns the full declaration text.
function extractFn(src, name) {
  const start = src.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `expected to find function ${name}`);
  const open = src.indexOf('{', start);
  assert.notEqual(open, -1, `no body brace for ${name}`);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces extracting ${name}`);
}

// Normalize away insignificant whitespace so formatting-only differences (line
// wraps, indentation, spaces around punctuation like `})\n.filter` vs
// `}).filter`) don't trip the guard — only real logic changes do. Spaces
// BETWEEN word chars (e.g. `return det`, `typeof det`) are preserved, since
// removing those would change meaning.
function normalize(fnText) {
  return fnText
    .replace(/\s+/g, ' ')             // collapse whitespace runs to one space
    .replace(/ *([^\w$ ]) */g, '$1')  // drop spaces around punctuation
    .trim();
}

const read = (f) => readFileSync(join(STATIC, f), 'utf8');

describe('_detailText drift guard', () => {
  const canonical = normalize(extractFn(read('_safe.js'), '_detailText'));

  // Standalone pages that carry their own copy (intentionally do not load
  // _safe.js). If a new standalone page copies _detailText, add it here.
  const copies = ['privacy-app.js', 'invite-accept.js'];

  for (const file of copies) {
    test(`${file} copy matches the canonical _safe.js implementation`, () => {
      const copy = normalize(extractFn(read(file), '_detailText'));
      assert.equal(copy, canonical,
        `${file}::_detailText has drifted from _safe.js::_detailText. ` +
        `Re-sync the copy (logic must stay identical) or update _safe.js.`);
    });
  }
});
