# Addendum — deployment reality + features to integrate (2026-06-23)

> This **supersedes the Firecracker references** in
> `2026-06-23-server-side-coding-execution-design.md` (spec) and
> `2026-06-23-server-side-coding-execution.md` (plan). Read this first.

## A. Sandbox change: isolate + gVisor (NOT Firecracker)

**Why:** the Hostinger KVM VPS has **no nested virtualization** — verified on the
box: `ls /dev/kvm` → not found, `grep -c -E 'svm|vmx' /proc/cpuinfo` → `0`.
Firecracker needs `/dev/kvm`, so it cannot run here.

**New isolation stack (still hostile-by-default, defense-in-depth):**
- **Inner sandbox:** `isolate` (namespaces + cgroups + seccomp + rlimits) — runs
  fine in a normal Linux guest, no nested virt. Per-run: no network
  (`--share-net` absent), CPU 1s, wall ~2s, mem 128 MB, output cap, pid cap.
- **Outer layer:** **gVisor (`runsc`)** — a userspace kernel that intercepts
  syscalls; contains an `isolate`/kernel-bug escape without needing a VM. Runs in
  a normal guest.
- **Host posture unchanged:** dedicated non-root user, no DB/S3/secret access, no
  egress, app→service over localhost only. Executor returns **stdout only**.

**Plan deltas:**
- Plan **Phase 0** (host): drop the KVM/Firecracker steps; install `isolate` +
  `runsc` (gVisor) instead. `ls /dev/kvm` check is removed.
- Plan **Phase 2** ("Firecracker microVM wrapper") → **"gVisor wrapper"**:
  `microvm.py` becomes `gvisor.py`; `build_vm_config` → a `runsc` OCI/run spec
  with no network namespace. The Mac-built `execsvc/` skeleton (Group A, done)
  stays — `runner.py`/`app.py`/`languages.py`/`isolate_cmd.py` are unchanged;
  only the outer-layer file swaps.
- Server is healthy + idle (load 0.2 / 4 vCPU, 13 GB free) → 1s-timeout +
  2-worker queue + 128 MB cap is trivial load. Confirmed adequate.

## B. Worthwhile features to integrate (curated from Gemini convos)

Prioritised. Each gets its own spec/plan when picked up.

### B1. Envelope-encrypt answer keys + coding expected outputs  ⭐ HIGH
**What:** AES-256-GCM encrypt the secret fields (MCQ `correct`, and
`coding_test_cases.expected_output`) before they hit Postgres; DB stores
ciphertext; the key lives in the app process env, never in the DB.
**Why:** a stolen `pg_dump` or a compromised DB role then can't read answer keys —
defends the exact secret our server-side judge holds. Complements RLS.
**How:** an `app/services/secrets_crypto.py` (encrypt/decrypt helpers); encrypt on
write in the authoring + seed paths; decrypt under `system_context` only at
compare time in `/coding/judge`. Migration to widen the columns to bytea/text-b64.

**Status: implemented.** `app/services/secrets_crypto.py` provides AES-256-GCM
envelope encryption (token format `enc:v1:<base64(nonce||ciphertext+tag)>`),
keyed by the process-only env var `CODING_SECRETS_KEY` (base64, 32 bytes).
Write paths (`app/routers/admin_coding.py`'s coding-question upsert,
`scripts/seed_coding_question.py`) encrypt `coding_test_cases.expected_output`
before insert; read paths (`app/routers/coding.py`'s `/coding/run` and
`/coding/judge`, `app/repositories/questions.py:load_questions` for MCQ
`correct`) decrypt transparently. Existing rows written before this feature
remain readable as legacy plaintext via `decrypt()`'s passthrough — no
migration is required for correctness. Once `CODING_SECRETS_KEY` is set in
prod, run the one-off backfill script described in
`docs/coding-secrets-backfill.md` to encrypt existing rows
at rest — not required for grading to keep working, but required for the
actual security benefit ("a stolen pg_dump can't read answer keys") to apply
to data written before the key existed.

### B2. Gated audio + live Vosk transcription telemetry  ⭐ HIGH
**What:** dashboard audio that is **muted by default**; Silero VAD (already in
`proctor.py`) flashes a mic indicator on a student's card; Vosk (already present)
transcribes locally and streams the **text** to the dashboard ticker
(`[VOSK] "what's the answer to Q2" ⚠`).
**Why:** leverages existing VAD+Vosk; near-zero server cost (sends ~100 B of text,
not audio); gives searchable, dispute-proof evidence. Teacher clicks "Listen" to
unmute on demand (WebRTC).
**How:** add a `voice_transcript` event from `proctor.py` → existing event
pipeline → dashboard ticker; add the muted `<audio>`/WebRTC track + a VAD-driven
highlight on the grid card.

### B3. Live Pop-In (WebRTC human-proctor escalation)  ⭐ HIGH (hybrid model)
**What:** on a critical AI flag, the teacher one-click opens a bi-directional
WebRTC audio/video overlay onto that student's kiosk; speaks, then exits back to
background monitoring. Few proctors cover many students.
**Why:** the economically-viable hybrid model competitors use; reuses our existing
WebRTC + signaling; P2P so the server only does signaling (flat cost).
**How:** dashboard "Pop-In" → IPC `INITIALIZE_RTC_CHAT` to the kiosk → temporary
peer connection (SDP/ICE via existing signaling).

### B4. Apple Handoff / Universal Clipboard kill-switch  ⭐ MED (macOS)
**What:** during an exam, `proctor.py` disables Handoff/Universal Clipboard (so a
phone-copied answer can't `Cmd+V` into the editor); re-enable on clean exit.
**Why:** a real macOS cheat vector our clipboard blocks miss.
**How (native hook):** `defaults write com.apple.coreservices.useractivityd
ActivityAdvertisingAllowed/ActivityReceivingAllowed -bool false` + `killall
useractivityd` at exam start; restore on exit. Guard with a clean-exit handler so
it never leaves the student's machine altered.

### B5. Database hardening on the Hostinger box  ⭐ MED (ops)
**What:** unix-socket-only Postgres (`listen_addresses='localhost'` or sockets),
UFW deny-incoming except 80/443/22 (close 5432/PgBouncer), least-privilege roles
(`procta_app` runtime with no DDL; separate migration owner; read-only analyst),
`pg_audit`/`log_statement='mod'` to a **root-owned** log for tamper-proof
grade-dispute trails.
**Why:** defense-in-depth under RLS; tamper-proof audit defeats "the system
changed my score" appeals. Note: RLS cutover to `procta_app` is already underway
([[audit_rls_cutover_2026_06]]) — fold this in.

### B6. LTI 1.3 (LMS integration)  ⭐ HIGH (procurement gateway)
**What:** LTI 1.3 (OIDC + JWT) so a student launches the exam from
Canvas/Moodle/Blackboard, gets auto-provisioned, and grades sync back to the LMS
gradebook.
**Why:** universities won't buy without it — bypasses manual rosters + grade
copy-paste; the strategic audit already flagged LMS integration as a top item
([[strategic_audit_2026_05]]).
**How:** its own project (OIDC login, deep-link launch → kiosk, AGS grade
passback). Sizeable; schedule deliberately.

## C. Explicitly rejected / deferred
- **Honeypot secondary-device trap** — clever but legally/ethically fraught (fake
  answer sites, IP capture + NAT-unreliable session matching, Honorlock patent);
  high risk for a solo founder. **Skip.**
- **Search & Destroy DMCA crawler** — real but operationally heavy; **park as an
  enterprise upsell**, not core.
