/* Node unit test for renderer/coding-worker.js's JS adapter (P1-T6).
 * Run: node scripts/test-coding-worker.cjs
 *
 * Verifies executeJs() — the risky part (untrusted-source execution + stdin/stdout
 * shims). The Worker message plumbing, network nullification, and the watchdog
 * terminate/respawn are browser-only and verified on-device under the real kiosk
 * CSP (per the plan's P1-T6 locked decision). Lives in scripts/ (NOT renderer/) so
 * it never ships in the packaged kiosk bundle.
 */
const path = require('path');
const fs = require('fs');
const ROOT = path.join(__dirname, '..');
const { executeJs } = require(path.join(ROOT, 'renderer', 'coding-worker.js'));

let pass = 0, fail = 0;
function check(name, got, exp) {
  const ok = got === exp;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}` + (ok ? '' : `  got=${JSON.stringify(got)} exp=${JSON.stringify(exp)}`));
  ok ? pass++ : fail++;
}

const sumProg = `const p = readline().trim().split(/\\s+/).map(Number); const n = p.shift(); print(p.slice(0,n).reduce((a,b)=>a+b,0));`;
check('sum (readline)', executeJs(sumProg, '3 1 2 3'), '6');
check('console.log capture', executeJs('console.log(40+2)', ''), '42');
check('multiline readline', executeJs(`let l; while ((l = readline()) !== '') { print(l.toUpperCase()); }`, 'ab\ncd'), 'AB\nCD');
check('readAll', executeJs('print(readAll().length)', 'abcde'), '5');

let threw = false;
try { executeJs('throw new Error("boom")', ''); } catch (e) { threw = /boom/.test(e.message); }
console.log(threw ? 'PASS  runtime error throws (worker reports ok:false)' : 'FAIL  runtime error throws');
threw ? pass++ : fail++;

// The real workload (JS port) vs the spike's expected.json — same I/O contract a
// student program uses.
const wl = `const p = readline().trim().split(/\\s+/).map(Number); const n = p.shift(); const a = p.slice(0,n);
let best = a[0], cur = a[0]; for (let i=1;i<a.length;i++){cur=Math.max(a[i],cur+a[i]);best=Math.max(best,cur);}
const d = new Set(a).size; const s = [...a].sort((x,y)=>x-y); print(s.join(' ')); print(d); print(best);`;
const stdin = fs.readFileSync(path.join(ROOT, 'spikes', 'clang-wasm', 'workload.stdin'), 'utf8').split('\n').filter((l) => l.trim());
const expected = require(path.join(ROOT, 'spikes', 'clang-wasm', 'workload.expected.json'));
stdin.forEach((line, i) => check(`workload c${i}`, executeJs(wl, line), expected[i]));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
