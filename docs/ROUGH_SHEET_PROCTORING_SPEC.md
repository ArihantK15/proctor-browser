# Rough-Sheet Proctoring — Spec

Status: Consolidated · 2026-06-21 (single source of truth)
Owner: Arihant
Scope: How the proctoring system handles candidate use of physical rough/scratch
paper during a remote exam — distinguishing *permitted* scratch work from a
hidden pre-written chit, without analysing student video on the server.

> **Supersedes `.claude/plans/rough-work-and-detection.md`** (removed). That plan's
> gaze-leniency design is folded in here; its Workstream B (detector reliability,
> YOLO26 swap, earbud model) is now obsolete — replaced by the custom
> earphone/headphone/phone YOLO being trained separately.

## 0. Reconciliation decisions (locked 2026-06-21)
1. **Room camera is MANDATORY whenever rough work is enabled.** The laptop sees
   the face, not the desk/lap (where a chit or lap-phone lives); the secondary
   (phone) camera gives the teacher a live desk view. `rough_work_allowed` cannot
   be saved unless `phone_camera_enabled` is on (enforced server-side + UI).
2. **One flag, not two.** `rough_work_allowed` turns on everything: the
   check-in/reconcile bookends, the laptop gaze/reach signals + down-gaze
   leniency, AND the mandatory room camera. (The old plan's separate
   `rough_work_allowed` gaze flag and this spec's `rough_sheets_allowed` are
   unified under `rough_work_allowed`.)
3. **No server-side analysis of room/phone frames** (§2). The old plan's idea of
   decoding the cached `roomframe:{sid}` JPEG and running YOLO on it **server-side
   is forbidden** — it violates the DPIA invariant. The room camera is *live human
   monitoring* in the MVP; automated desk-paper/device ML happens **on-device on
   the native phone app (Phase 2, §7 Option A)**, never on the server.
4. **Open questions resolved** — see §9.

---

## 1. Problem & non-goals

Many exams legitimately permit rough sheets. The cheating risk is a **pre-written
note disguised as a rough sheet**, or paper pulled from off-camera mid-exam. A
naive "paper detector" cannot separate an allowed scratch sheet from a chit —
they are the same pixels — so detecting *paper* yields constant false positives
on honest students.

**Goal:** control the *lifecycle* of paper (declare → verify → monitor →
reconcile) and let any ML provide a *count/appearance-change* signal against a
verified baseline, with humans adjudicating. Catch *intent* (hidden-note reading)
via on-device behavioural signals, not a chit classifier.

**Non-goals (explicit):**
- We do **not** auto-fail on a paper signal. Every signal is a teacher-reviewed flag.
- We do **not** ship a generic "cheat paper" classifier (unbuildable, high FP).
- We do **not** analyse student video on the server (see §2).

---

## 2. Privacy invariant (hard rule — do not violate)

Per [DPIA.md](DPIA.md) §"Data minimisation" and R7:

> Raw video is analysed **locally** on the candidate's device; only violation
> events + bounded evidence leave the device. Phone-camera frames are **transient**
> (Redis TTL) for live room monitoring and are **never** persisted or analysed
> server-side.

**Every feature in this spec runs inside that invariant.** No new server-side
analysis of frames. The only artefacts that may cross to the server are:
1. **Violation events** (structured, no raw media) — the existing flag path.
2. **Bounded, consented captures**: the ≤3-frame `ctx_*` strip on an
   appeal-critical flag, and the check-in/check-out sheet captures (§5a, modelled
   on the existing ID-verification image path).

Anything that would require streaming or batch-analysing a continuous student
feed on the server is **out of scope by policy**, not by preference.

---

## 3. Where the ML runs (architecture decision)

| Placement | Verdict | Why |
|---|---|---|
| **Server-side (real-time or async batch)** | ❌ Forbidden | Violates the §2 invariant — server analysis of student video. |
| **On-device — laptop (Electron)** | ✅ Primary today | Already the home of on-device YOLO + face detection ([DPIA.md:40](DPIA.md)). Capable; only needs <1 fps. Angle points at the face, not the desk. |
| **On-device — phone (native app)** | ✅ Phase 2 | Best *desk/paper* angle. Needs the roadmap native app (NNAPI/CoreML). Today's phone client is a thin browser feeding transient Redis frames — no on-device ML. |

**Decision:** All continuous analysis is **on-device**. The MVP leans on
*process controls* + *on-device behavioural signals* (laptop). The continuous
desk-angle **paper detector** is a tradeoff resolved in §7.

---

## 4. Lifecycle model

```
  CHECK-IN              DURING EXAM                 CHECK-OUT
  ─────────            ───────────                 ─────────
  Declare N sheets  →  On-device behavioural    →  Show sheets again
  Show each blank      signals (gaze/reach)        Count + nonce match
  Write ID + nonce     [Phase 2: on-device         → reconcile flag if
  Capture baseline      phone paper-count delta]      mismatch
```

The win is at **check-in**: it converts the unanswerable question "is this paper
bad?" into the answerable "is this one of the N blank sheets I verified?"

---

## 5. MVP components (buildable now)

### 5a. Check-in: declare + verify blank sheets
**UX (Electron client, pre-exam, after ID verification):**
1. Teacher config sets `rough_sheets_allowed: bool` and `rough_sheets_max: int`
   per exam (§5f).
2. If allowed, candidate is prompted: *"Hold up each blank rough sheet, front then
   back."* One capture per side.
3. Candidate writes **exam-ID + a server-issued nonce** on each sheet corner;
   captures that too. The nonce (today's, server-issued) means a pre-written chit
   cannot carry a valid mark.
4. Captures are stored exactly like **ID-verification images** today
   (`_save_id_verification_images` path, exam.py ≈ line 1913) — a **bounded,
   consented, purpose-limited** artefact, *not* a continuous feed. Same S3
   ap-south-1 + retention as ID images.

**Data produced:** `sheet_baseline_<n>_<side>.jpg` (teacher-only), `declared_count`,
`nonce`. No continuous capture.

### 5b. On-device behavioural signals (laptop face model)
These run in the **existing on-device detector** and feed the **existing flag
pipeline** (violation event + the 1 Hz RAM-ring `ctx_*` strip on an
appeal-critical flag). No new server analysis.

New on-device triggers (tune against sensitivity presets, cf. [DPIA.md](DPIA.md) R6):
- `gaze_offscreen_sustained` — gaze repeatedly to a fixed off-screen / off-desk
  point (where a hidden note sits) beyond a dwell threshold.
- `reach_offcamera` — hand/arm leaves frame (lap/pocket) then returns to writing
  posture.

These catch *hidden-note intent* without any paper classifier — the highest-value,
lowest-FP, privacy-cleanest near-term signal.

### 5c. Reconcile at submit
On exam submit: re-prompt *"Show all your rough sheets."* Capture once. Assert
`visible_count == declared_count` and (if used) nonce legible. Mismatch →
`rough_sheet_reconcile_mismatch` flag for teacher review. Bounded capture, same
retention as 5a.

### 5d. Event / flag schema
New violation `event_type` values (plug into the existing violations table + risk
score; reuse the `ctx_` label sanitiser in `_save_context_frames`, exam.py:1929):

| event_type | Source | Severity hint | Evidence attached |
|---|---|---|---|
| `rough_sheet_undeclared` | check-in skipped/failed | low | baseline capture |
| `rough_sheet_reconcile_mismatch` | §5c | med | reconcile capture |
| `gaze_offscreen_sustained` | on-device (5b) | med | `ctx_*` strip |
| `reach_offcamera` | on-device (5b) | low-med | `ctx_*` strip |
| `paper_count_exceeded` | Phase 2 (§7) | med | on-device crop |
| `new_paper_entered` | Phase 2 (§7) | med | on-device crop |

All are **teacher-only**, reviewed with the lead-up `ctx_*` strip so "a dropped
pen" is not mistaken for a chit (the R6 mitigation already in the DPIA).

### 5e. Review-queue surfacing
No new surface needed — these events land in the same per-session flagged-review
view teachers already use ([DPIA.md:43](DPIA.md)). Add a "Rough sheet" filter/group.

### 5f. Per-exam teacher config
Add to `exam_config`:
- `rough_work_allowed BOOLEAN DEFAULT FALSE` — the single master flag (§0.2):
  enables the bookends, the laptop gaze/reach signals + down-gaze leniency.
- `rough_sheets_max INT DEFAULT 1` — declared-sheet ceiling.
- `require_sheet_nonce BOOLEAN DEFAULT FALSE` — opt-in corner-mark (higher
  friction; recommend ON for high-stakes in the UI copy).

**Mandatory room-camera rule (§0.1):** the create/update exam endpoints MUST
reject `rough_work_allowed = true` when `phone_camera_enabled` is not also true
(400, clear message), and the dashboard greys out / auto-enables the room-camera
toggle. Rough-work leniency without the desk camera is unsafe (lap-phone reads
identically to down-gaze on the webcam), so the two are coupled at the config
layer, not just by convention. Opt-in per exam keeps the proportionality
posture (R7).

---

## 6. Retention & minimisation mapping

| Artefact | Class | Where | Retention |
|---|---|---|---|
| Sheet baseline / reconcile captures | Personal | S3 ap-south-1 SSE-S3, teacher-only, streamed via backend (no presigned URLs) | Same window as ID-verification images |
| `gaze_*` / `reach_*` events | Personal | violations table + `ctx_*` strip | Inherits the 30-day evidence window |
| Phone frames (if any) | Personal | Redis transient (TTL) | Auto-expire; **never analysed server-side** |
| `declared_count`, `nonce` | Personal | session record | Per session retention |

Update [DPIA.md](DPIA.md) and [PRIVACY.md](PRIVACY.md) retention matrices with the
new artefacts before shipping (compliance gate).

---

## 7. The desk-angle paper detector — tradeoff (decide here)

The *best* angle for paper is the **phone** (desk view); the laptop sees the face.
But the §2 invariant forbids analysing phone frames server-side, and today's phone
client is a thin browser. So a continuous "new sheet entered / count exceeded"
detector has two possible homes — pick one:

### Option A — Phase 2, on-device on the **native phone app**
Run the (quantised) model on the phone via NNAPI/CoreML when the roadmap native
app exists. Emits `paper_count_exceeded` / `new_paper_entered` locally; only the
event + a minimised crop crosses.
- **Pros:** best angle, true real-time, zero server compute, fully within §2,
  reuses the same `.onnx` we already train (only the model crosses the compliance
  boundary). Robust on native runtimes.
- **Cons:** gated on the native app (not shippable now). Until then, no continuous
  desk-angle detection — process controls (§5a/5c) + laptop behavioural signals
  (§5b) carry the load.

### Option B — In the **phone browser now** (onnxruntime-web / TF.js, WASM/WebGL)
Run a tiny int8 YOLO-n in the phone browser at **<1 fps** (paper doesn't move
fast). Still on-device — no §2 violation.
- **Pros:** ships before the native app; gets the desk angle sooner; same flag path.
- **Cons:** low-end Android perf is flaky (WASM/WebGL inconsistency, thermal,
  battery); model must be tiny (~3–6 MB int8), likely a *separate, weaker* export
  than the laptop model; real cross-device QA burden for a solo team; phone browser
  must hold a model + run a frame loop without dropping the live-view duties.

### Recommendation
**Option A (Phase 2, native).** Rationale: the near-term MVP (§5a–5f) already
gives a *defensible* rough-sheet policy without any continuous paper detector —
declare/verify/reconcile + on-device gaze/reach is enough to deter and to surface
the real tell (paper pulled from off-camera shows up as `reach_offcamera`). Option
B spends scarce solo-founder QA budget fighting the Android device zoo for a
*secondary* signal, and risks a flaky model undermining trust. Defer continuous
desk-angle detection to the native app, where it's robust and free.

**Trigger to revisit B:** if post-launch review data shows hidden-note cheating
slipping past §5b at a material rate *and* the native app is >2 quarters out.

---

## 8. Build order

1. **Exam config** (§5f) — `rough_sheets_allowed` / `_max` / `require_sheet_nonce`.
2. **Check-in declare/verify** (§5a) — reuse the ID-verification capture path; add
   nonce issuance + corner-mark capture. *Biggest deterrent, no ML risk.*
3. **Event schema** (§5d) — add the new `event_type`s to violations + risk score.
4. **On-device behavioural signals** (§5b) — `gaze_offscreen_sustained`,
   `reach_offcamera` in the existing detector; wire to the `ctx_*` flag path.
5. **Reconcile at submit** (§5c).
6. **Review-queue filter** (§5e) + **DPIA/PRIVACY matrix update** (§6).
7. **(Phase 2)** Native-app on-device paper detector (§7 Option A).

Steps 1–2 alone constitute a shippable, defensible v1.

---

## 9. Resolved decisions (was: open questions)
- **Laptop desk view?** No — assume the laptop never sees enough desk; the desk
  is strictly the (now-mandatory) phone's job: human-monitored live in the MVP,
  on-device-analysed at native (Phase 2). Do **not** build a laptop paper-counter.
- **Nonce friction:** keep `require_sheet_nonce` opt-in (default off), but the UI
  recommends it ON for high-stakes exams. Teacher chooses per exam.
- **Retention:** sheet baseline/reconcile captures get a **shorter window than ID
  images** — delete at the exam's appeal-window close (or 30d, whichever first);
  they're only needed through review. More data-minimising; update the
  DPIA/PRIVACY matrices accordingly.

---

## 10. Implementation status (2026-06-21)

**Built + tested (this pass) — the config foundation:**
- `exam_config` columns `rough_work_allowed` / `rough_sheets_max` /
  `require_sheet_nonce` — migration `phase139_rough_work_config.sql`, plus
  `schema/columns.json` + `integration_tests/schema.sql`.
- `CreateExamIn.rough_work_allowed` (app/models/exam.py).
- **The mandatory-room-camera rule (§0.1):** `create_exam` rejects
  `rough_work_allowed=true` without `phone_camera=true` (400).
- Tests: `tests/test_admin_exams_coverage.py::TestRoughWorkRequiresRoomCamera`
  (reject without cam / ok with cam / off needs no cam).

**Remaining (next phases — need an Electron build + on-device testing, per build
order §8):**
1. **set_rough_work_config** toggle endpoint (mirror `set_phone_camera_config`,
   same validation) + return `rough_work_allowed` from the config reads
   (`get_questions` / validate) + dashboard toggle (greyed unless room cam on).
2. **Check-in flow** (§5a) — renderer: declare N sheets, show blank front/back,
   server-issued nonce + corner-mark capture (reuse the ID-verification image
   path). Nonce-issuance endpoint.
3. **Reconcile at submit** (§5c) — renderer re-prompt + capture + count/nonce
   assertion → `rough_sheet_reconcile_mismatch` flag.
4. **On-device behavioural signals** (§5b) — `gaze_offscreen_sustained`,
   `reach_offcamera` in proctor.py + down-gaze leniency into the calibrated
   paper zone; wire to the existing `ctx_*` flag path.
5. **Event schema** (§5d) — register the new `event_type`s in the violations
   table + risk classification (review flags, not auto-fail).
6. **Review-queue filter** (§5e) + **DPIA/PRIVACY retention matrices** (§6,
   shorter window per §9) — compliance gate before shipping the capture flows.
7. **(Phase 2)** native-app on-device desk paper detector (§7 Option A).
