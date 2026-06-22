// Sucrase TypeScript transpiler for the kiosk coding question — bundled to a
// single IIFE (../../renderer/sucrase.bundle.js) for offline same-origin
// loading in the Electron kiosk under the page CSP (script-src 'self').
//
// Sucrase is pure JS (no WASM), so unlike esbuild-wasm it needs NO CSP
// relaxation. We only need TYPE-STRIPPING (+ enums / namespaces / decorators,
// which the "typescript" transform handles) — not bundling or minification —
// so this is intentionally the lightest possible TS→JS step.
//
// Runs on the MAIN thread: coding-runtime.js transpiles TS source to JS here,
// then runs the resulting JS through the existing sandboxed JS worker path, so
// coding-worker.js stays JS-only and untouched.
import { transform } from "sucrase";

window.transformTS = function (src) {
  // "typescript" transform only: strip types, keep everything else as plain
  // script the worker can run via new Function. No "imports" transform — coding
  // exam programs are self-contained scripts, and there is no module loader in
  // the worker, so an `import` would (correctly) surface as an error.
  return transform(String(src == null ? "" : src), {
    transforms: ["typescript"],
  }).code;
};
