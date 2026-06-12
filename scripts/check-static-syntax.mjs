#!/usr/bin/env node
// check-static-syntax.mjs — fail CI on a syntax error in any served browser JS.
//
// WHY: app/static/*.js (dashboard-app.js, student-app.js, register-app.js, …)
// is shipped straight to the browser but was NOT in the eslint/lint targets,
// so a syntax error sailed through CI. A duplicate top-level `const esc`
// declaration once did exactly that and took down the ENTIRE dashboard
// (login included — the whole script fails to parse). `node --check` catches
// that class (redeclarations, stray braces, bad tokens) without the noise of
// full eslint style rules on this legacy code.
//
//   node scripts/check-static-syntax.mjs
//
import { execFileSync } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

// Directories whose *.js are loaded directly by a browser (no bundler).
const DIRS = ['app/static', 'renderer'];

const failures = [];
let checked = 0;

for (const dir of DIRS) {
  let entries;
  try { entries = readdirSync(dir); } catch { continue; }   // dir may not exist
  for (const f of entries) {
    if (!f.endsWith('.js')) continue;
    const p = join(dir, f);
    try { if (!statSync(p).isFile()) continue; } catch { continue; }
    try {
      execFileSync(process.execPath, ['--check', p], { stdio: ['ignore', 'ignore', 'pipe'] });
      checked++;
    } catch (e) {
      failures.push(`✗ ${p}\n${(e.stderr || e.message || '').toString().trim().split('\n').slice(0, 3).map(l => '    ' + l).join('\n')}`);
    }
  }
}

if (failures.length) {
  console.error('Static JS syntax check FAILED:\n');
  failures.forEach(f => console.error(f + '\n'));
  console.error('A syntax error in served browser JS breaks the whole page (including login). Fix before merge.');
  process.exit(1);
}
console.log(`✓ static JS syntax OK (${checked} file(s) checked)`);
process.exit(0);
