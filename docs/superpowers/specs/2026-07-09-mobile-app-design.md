# Mobile App — Design

## Goal

Extend Procta to phones: teachers can author and schedule exams from a
native mobile app, and students can see their exam schedule/history/appeals/
profile and take exams with lightweight proctoring (no heavy on-device AI,
unlike the desktop Electron client) — widening the addressable exam-taking
surface beyond the desktop-only kiosk experience for teachers who want that
tradeoff. Same backend, same accounts, same billing — this is a new client,
not a new product.

## Background

The existing product is a desktop Electron app with a heavy on-device
Python AI proctoring pipeline (RetinaFace/YOLO/gaze, full kiosk lockdown)
serving high-stakes exams, backed by a FastAPI + Postgres backend that
already has a React teacher dashboard, a REST API surface for schedule/
history/appeals/profile, an existing DPDP-compliance module
(`app/routers/privacy.py` — export/delete/consent), and an existing
JWT-bearer auth pattern used by the Electron lobby client
(`procta_native`/`lobby_preload.js`). The mobile app reuses all of this
backend infrastructure; it is a new, lighter client for a different use
case (lower-stakes exams where full kiosk lockdown is unnecessary or
unwanted), not a parallel system.

## Scope

**In scope:**
- Two native clients built in parallel: iOS (Swift) and Android (Kotlin).
  Chosen over React Native/Flutter/single-codebase for camera/proctoring
  performance and platform integration quality, accepting the larger
  engineering surface (two codebases) as a deliberate tradeoff — same
  bus-factor-1 solo-maintenance model as the rest of this project, not a
  new decision to revisit here.
- **Teacher side**: full exam/question authoring (same capability as the
  desktop dashboard), scheduling, and monitoring/results — not a
  view-only or scheduling-only subset.
- **Student side**: exam schedule (upcoming/past), history, appeals,
  profile, notifications, and taking exams with light proctoring.
- **Light proctoring**: on-device face-presence + app-backgrounding
  detection using each platform's native, lightweight face-detection API
  (Apple Vision on iOS, ML Kit on Android) — explicitly NOT the desktop's
  custom-trained YOLO/RetinaFace pipeline, no model download, no gaze
  estimation, no audio-keyword spotting. Voice/audio presence is checked,
  but this is deliberately a lighter tier than desktop, not a phone port of
  the same AI suite.
- **Event-triggered data capture only**: no continuous video/audio
  recording. A frame is captured and uploaded only when an on-device check
  flags something (no face, multiple faces, app backgrounded) — minimum
  data by construction, matching the explicit DPDP/minimum-data commitment
  this product stands on.
- Push notifications (APNs/FCM) for exam-starting-soon and results-posted
  nudges.

**Out of scope:**
- Full desktop-grade AI proctoring on mobile (heavy models, gaze tracking,
  object detection for phones/calculators/etc.) — this tier is explicitly
  lighter by design, not a temporary limitation to close later.
- Continuous video/audio recording and storage — explicitly rejected in
  favor of event-triggered capture for data-minimization compliance.
- Any change to the desktop Electron client or its proctoring pipeline —
  this spec adds a new client, it doesn't touch the existing one.
- Shadow-mode/parallel-run verification — not applicable, this is new
  functionality being built, not a refactor of existing behavior with a
  "don't regress this" constraint.

## Architecture

Same backend (FastAPI + Postgres), same auth/billing/exam data model, two
new native clients talking to a mix of existing and new endpoints:

- **Reused as-is** (existing REST endpoints the dashboard/student-app
  already use): exam schedule, history, appeals, profile, answer autosave,
  exam submission. These are backend-agnostic-to-client already; the
  mobile clients just need to speak the same REST contract the web
  clients do.
- **New**: a mobile auth issuance path following the existing JWT-bearer
  pattern (`procta_native`'s model) rather than a new auth scheme — same
  tokens, same backend verification, issued to a phone client instead of
  desktop. A new violation-event ingestion endpoint for the event-triggered
  proctoring model (no equivalent exists today, since desktop's evidence
  model is continuous-capture-based, not event-triggered). A new
  lightweight `mobile_proctoring_events` table (violation type + timestamp
  + one evidence frame per flagged event), under the same per-tenant RLS
  pattern the rest of the schema already uses (mirroring `coding_
  submissions`' existing RLS policy shape, per the pattern noted in this
  session's memory of prior RLS work). Push-notification device-token
  registration/dispatch (APNs/FCM), new infrastructure.

## Data flow — exam-taking on the phone

1. Student opens the app, sees upcoming exams (existing schedule API),
   taps to start.
2. Camera/mic permission check — denial blocks entry with a clear message
   (proctoring is a hard requirement, matching desktop's posture).
3. On-device face-detection loop starts alongside the exam UI (native OS
   API, no model download); an app-lifecycle listener watches for
   backgrounding.
4. Student answers questions, synced via the same local-first autosave
   pattern the desktop/web clients already use.
5. On any flagged event (no face / multiple faces / app backgrounded), the
   on-device detector captures one frame, uploads it + the violation type
   to the new endpoint; the exam continues uninterrupted for non-fatal
   violations (severity tiering — which violations pause vs. just log — to
   be finalized during implementation, mirroring desktop's existing
   tiered-severity model rather than inventing a new one).
6. Submit reuses the existing submit-exam flow.

## Error handling — phone-specific failure modes

Two risks desktop doesn't have to weigh as heavily:

- **Connectivity drops mid-exam** are far more likely on a phone
  (cellular, movement, weak wifi) than a desktop on stable wifi/ethernet.
  Answers must autosave locally first and sync opportunistically — a
  network hiccup must never block the student's ability to keep
  answering, in the same spirit as this session's `load_questions()`
  resilience fix (retry transparently, never silently fail the user's
  actual progress).
- **OS backgrounding/kill**: mobile OSes aggressively kill backgrounded
  apps to save battery — an incoming call or notification could kill the
  process entirely, not just background it. The app needs a
  resume-from-cold-start path that restores exam state (remaining time,
  saved answers) rather than losing the attempt — conceptually similar to
  desktop's `resumedStartedAt` resume logic, but triggered by OS process
  death rather than an app restart.

## Compliance

The event-triggered, frame-only capture model is the primary
data-minimization decision (see Scope). Retention, export, and deletion
for the new `mobile_proctoring_events` table extend the existing DPDP
machinery in `app/routers/privacy.py` rather than building a parallel
compliance path — a student's data-export/deletion request must cover
mobile proctoring events exactly as it already covers desktop evidence.

## Testing strategy

Platform-native testing per client (XCTest for iOS, JUnit + Espresso for
Android) for on-device logic (face-detection integration, violation-event
triggering, resume-from-cold-start). Backend tests (pytest, this repo's
existing convention) for every new endpoint: violation-event ingestion,
the new table's RLS policies (matching this repo's existing per-table RLS
test pattern), push-token registration, and the DPDP export/delete
extension. Standard test-first development per new endpoint/screen — no
shadow-mode needed since this is new functionality, not a behavior-
preserving refactor.

## Success criteria

- Teachers can author, schedule, and monitor exams from both iOS and
  Android apps with the same capability as the desktop dashboard.
- Students can view schedule/history/appeals/profile and take a
  light-proctored exam end-to-end on both platforms.
- No continuous video/audio is ever recorded or stored — only
  event-triggered single frames tied to a specific flagged violation.
- The new `mobile_proctoring_events` table has RLS policies matching this
  repo's existing per-tenant isolation pattern, with tests proving
  cross-tenant access is denied.
- DPDP export/delete requests correctly include mobile proctoring events.
- A dropped connection or OS-killed app during an exam never loses a
  student's already-answered progress.
