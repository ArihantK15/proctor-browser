# Edge Compiler — coding-question UI/UX findings (2026-06-22)

From a live test on kiosk v2.3.51 + a code review of `renderer/coding-ui.js`, the
CodeMirror build, and the `renderQ` integration. The engine works; the UX reads as
"debug output," not a HackerRank/LeetCode-grade assessment. Grouped + prioritized.

## P0 — kills confidence / the core complaint
1. **Results are a plain text dump, not test-case cards.** `res.textContent` =
   `"Test 1: ✓ pass\n input:…\n expected:…\n actual:…"`. Needs per-case cards/tabs,
   colour-coded status, labelled input/expected/actual boxes.
2. **No visible test cases before Run.** No "Testcase" panel showing the sample
   inputs/expected up front — the student can't see what they're judged against.
3. **Values are `JSON.stringify`'d** → `"2 3\n"` / `"5\n"` (escaped quotes + literal
   `\n`). Render raw values in monospace boxes.
4. **Problem statement renders as raw markdown** — `renderQ` sets
   `qtxt.textContent = q.question`, so `#`/```` ``` ````/lists show literally. Needs
   rendered markdown (headings, examples, constraints).
5. **Submit verdict is bare text** (`Passed 2/2`) — no Accepted/Wrong-Answer styling,
   no runtime/memory line, no green/red.
6. **Client/server normalization can drift.** Run grades client-side with `_normOut`
   (`/[ \t]+$/` per line); server `normalize_output` does `rstrip()` + CRLF→LF. On
   `\r`/other trailing whitespace they diverge → "Run passes, Submit fails" — a
   trust-killer. Must match exactly.

## P1 — polish / efficiency
7. **Cramped stacked layout**, editor clamped 150–260px (a workaround for the
   Submit-Exam nav). Needs a taller editor + a proper results console (the sticky nav
   already keeps Submit reachable). Full split-pane (problem | editor) is a later
   enhancement.
8. **No run/submit "feel"** — plain buttons (no Run▷/Submit distinction or icons),
   only "Running…" text, no spinner/per-case progress, no Ctrl/Cmd+Enter to run.
9. **Test cases re-fetched on every Run and Submit** (no cache) → latency + calls.
10. **Language dropdown is cosmetic** — the editor is created once; changing the
    `<select>` has no listener, so highlighting/starter don't update.
11. **No problem metadata** — marks, time/memory limits, languages, and
    "attempts left" (submit cap is 10 but invisible to the student).
12. **No "reset to starter"**, and statuses don't distinguish Wrong-Answer vs
    Runtime-Error vs Timeout (metrics carry `timed_out`/`error` but the UI lumps them).

## Hardening (server/runtime)
13. Cap worker stdout size (only time is watchdog'd; a runaway print balloons RAM).
14. Surface AC/WA/RE/TLE statuses end-to-end.
15. Confirm-before-Submit (it burns a capped attempt).

## Target (what "LeetCode-grade" means here)
Rendered problem statement → a taller editor with language switching + reset → a
results console with per-case cards (status chip + raw input/expected/actual) for
Run, and an Accepted/Wrong-Answer verdict banner (+ counts, runtime) for Submit;
grading is authoritative server-side (Run/Submit POST source; the server runs +
judges — see the 2026-06-23 server-side-execution spec); attempts-left + limits visible.

## Implementation order
P0 (1–6) + the P1 items that ride along (7–12) in one coding-ui.js rewrite + injected
CSS + statement markdown. Server hardening (13–15) follows. Split-pane layout is a
later enhancement once the panel UX is in.
