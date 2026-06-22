#!/usr/bin/env node
/* Concurrent-load simulator for the P0-T1 spike (RUNBOOK §"Establish the
 * concurrent load", option B). Spawns N worker threads pinning CPU to APPROXIMATE
 * the contention proctor.py creates on a weak box AFTER governor throttling
 * (usually 1-2 cores of steady inference work).
 *
 * THIS IS A FALLBACK. The faithful test is option A: run the REAL kiosk proctor
 * (webcam + YOLO + gaze) alongside the harness, because the simulator does NOT
 * reproduce camera I/O, GPU load, or the proctor's ~hundreds-of-MB RAM footprint —
 * all of which matter for G3 (OOM). Note in the Results which method you used.
 *
 * Usage:  node cpu-load.js --cores=2 --duty=0.8 --seconds=120
 *   --cores   number of busy worker threads (default: 2)
 *   --duty    fraction of each 50ms slice spent busy, 0..1 (default: 0.85)
 *   --seconds how long to run before auto-stopping (default: 180)
 */
const { Worker, isMainThread, workerData } = require('worker_threads');
const os = require('os');

function parseArgs() {
  const a = { cores: 2, duty: 0.85, seconds: 180 };
  for (const arg of process.argv.slice(2)) {
    const m = /^--(\w+)=(.+)$/.exec(arg);
    if (m) a[m[1]] = Number(m[2]);
  }
  return a;
}

function busyLoop(duty) {
  // Busy `duty` of each 50ms window; sleep the rest. Keeps a core hot without
  // 100%-pegging so it loosely mimics a throttled inference loop's duty cycle.
  const SLICE = 50;
  const spin = (untilMs) => { while (performance.now() < untilMs) { Math.sqrt(Math.random() * 1e9); } };
  const tick = () => {
    const start = performance.now();
    spin(start + SLICE * duty);
    const rest = SLICE - (performance.now() - start);
    setTimeout(tick, Math.max(0, rest));
  };
  tick();
}

if (!isMainThread) {
  busyLoop(workerData.duty);
} else {
  const { cores, duty, seconds } = parseArgs();
  const n = Math.max(1, Math.min(cores, os.cpus().length));
  console.log(`[cpu-load] ${n} busy thread(s), duty=${duty}, ${seconds}s on a ${os.cpus().length}-core box.`);
  console.log('[cpu-load] FALLBACK only — option A (real proctor) is the true gate. Ctrl-C to stop early.');
  const workers = [];
  for (let i = 0; i < n; i++) workers.push(new Worker(__filename, { workerData: { duty } }));
  setTimeout(() => { workers.forEach((w) => w.terminate()); console.log('[cpu-load] done.'); process.exit(0); }, seconds * 1000);
}
