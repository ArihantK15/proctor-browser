#!/usr/bin/env python3
"""Generate Procta_Features.pdf — a sales-ready deck of everything the
product actually ships today. Sourced from the codebase: routers,
migrations, JSX components, recent commits.

Run:  python3 scripts/gen_features_pdf.py
Output: Procta_Features.pdf in the repo root.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)
from datetime import date


# ── Brand colours (mirrored from website/src/index.css) ───────────
ACCENT = HexColor("#5b6df0")
ACCENT_DARK = HexColor("#404bb8")
INK = HexColor("#0d1117")
MUTED = HexColor("#6b7280")
BORDER = HexColor("#e2e8f0")
BG_SOFT = HexColor("#f5f7fb")
EMERALD = HexColor("#10b981")


# ── Styles ────────────────────────────────────────────────────────
styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                    fontSize=22, leading=28, textColor=INK, spaceAfter=8)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=14, leading=20, textColor=ACCENT_DARK,
                    spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontName="Helvetica-Bold",
                    fontSize=11, leading=15, textColor=INK,
                    spaceBefore=10, spaceAfter=2)
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontName="Helvetica",
                      fontSize=10, leading=14, textColor=INK, spaceAfter=4)
MUTED_BODY = ParagraphStyle("Muted", parent=BODY, textColor=MUTED, fontSize=9)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=12, bulletIndent=2,
                        spaceBefore=0, spaceAfter=2)
LABEL = ParagraphStyle("Label", parent=BODY, fontName="Helvetica-Bold",
                       fontSize=8, textColor=ACCENT_DARK,
                       textTransform="uppercase")


def cover_page(elements):
    elements.append(Spacer(1, 80 * mm))
    elements.append(Paragraph(
        '<font color="#5b6df0">Procta</font>', H1))
    elements.append(Paragraph(
        "AI-Proctored Exam Platform", ParagraphStyle(
            "Subtitle", parent=BODY, fontSize=18, leading=24,
            textColor=MUTED, spaceAfter=20)))
    elements.append(Paragraph(
        "Feature inventory · "
        f"as of {date.today().strftime('%d %b %Y')}",
        MUTED_BODY))
    elements.append(Spacer(1, 60 * mm))
    elements.append(Paragraph(
        "What's actually shipped — sourced from the codebase, not the "
        "roadmap. Every entry below corresponds to working code in "
        "production today.", BODY))


def feature_table(elements, rows, widths=None):
    """Two-column feature table: name | description."""
    data = []
    for name, desc in rows:
        data.append([
            Paragraph(f"<b>{name}</b>", BODY),
            Paragraph(desc, BODY),
        ])
    t = Table(data, colWidths=widths or [55 * mm, 110 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), BG_SOFT),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 4 * mm))


def section(elements, title, body_html=None):
    elements.append(Paragraph(title, H2))
    if body_html:
        elements.append(Paragraph(body_html, BODY))


def build():
    doc = SimpleDocTemplate(
        "Procta_Features.pdf",
        pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title="Procta — Feature Inventory",
        author="Procta",
    )
    elements = []

    # ── Cover ──
    cover_page(elements)
    elements.append(PageBreak())

    # ── 1. Proctoring engine ──
    section(elements, "1. Live Proctoring Engine",
            "Multi-signal AI runs in the locked Electron browser on the "
            "student's machine. Frames stream to the teacher dashboard "
            "in real time over WebSocket; events stream over Server-Sent "
            "Events.")
    feature_table(elements, [
        ("Face presence", "Detects when the student leaves the camera. "
                          "InsightFace embedding model (CPU). Logged as "
                          "<i>face_missing</i> violation."),
        ("Wrong-person detection",
         "Embedding compared against the ID-verification selfie at calibration. "
         "Triggers if a different person sits at the keyboard mid-exam. "
         "<i>wrong_person</i> violation, weighted heaviest in risk score."),
        ("Multi-face detection",
         "Two faces in frame for >2 s → <i>multiple_faces</i> violation. "
         "Catches another person looking over the shoulder."),
        ("Gaze tracking",
         "MediaPipe face-mesh + 9-point calibration grid. Logs sustained "
         "off-screen gaze. Per-student calibration quality tier "
         "(Tight / Loose / Suspicious) shown in Results."),
        ("Head-pose estimation",
         "Yaw / pitch / roll from face landmarks; gating threshold tuned "
         "in calibration to per-student baseline (hardware-aware)."),
        ("Cheat-object detection (YOLOv8 + SAHI)",
         "Phone, headphones, earbuds (custom ear-crop classifier). "
         "Slicing-aided inference for objects partially out of frame. "
         "GPU auto-detect: CUDA → MPS → CPU."),
        ("Audio anomaly (RMS)",
         "Sustained voice / conversation-pattern detection via "
         "sounddevice RMS. Tuned to ignore typing and ambient noise. "
         "Three event types: voice_detected, sustained_voice, "
         "conversation_detected."),
        ("On-device speech-to-text keyword detection",
         "Vosk (en-IN + hi-IN) runs on the student's CPU and matches "
         "transcripts against a built-in cheat-phrase list plus per-exam "
         "teacher-added keywords. Fires keyword_uttered on match with the "
         "matched phrase and a ~5 s transcript snippet. Audio never leaves "
         "the device."),
        ("Multi-voice detection",
         "Silero VAD gates voice-active segments; python_speech_features "
         "MFCC vectors clustered over a rolling 60 s window via a numpy-"
         "only 2-cluster silhouette check. Two distinct voices in the "
         "buffer fires multiple_voices_detected. Catches an off-camera "
         "helper that RMS alone would miss."),
        ("Behavioural correlation engine",
         "Multi-signal fusion: gaze + audio + face-pose patterns scored "
         "together to surface coordinated cheating (e.g. consistent gaze "
         "drift right + faint voice spikes)."),
    ])

    # ── 2. Lockdown browser ──
    section(elements, "2. Lockdown Browser (Electron)",
            "The student's exam runs in a custom Electron build. The OS "
            "session is locked while the exam is active.")
    feature_table(elements, [
        ("Tab-switch prevention", "Browser focus loss for >1 s → "
                                  "<i>tab_switch</i> violation."),
        ("Process integrity scan",
         "Live scan for VPN, remote-desktop, screen-share, debugger, AI "
         "assistant tools. Hyphen-aware regex (e.g. parsec-cloud no "
         "longer false-positives parsec)."),
        ("Hardware-locked session",
         "Camera + microphone permissions granted at calibration; lost "
         "permission mid-exam ends the session."),
        ("Auto-update via electron-updater",
         "Hits GitHub Releases on launch. Existing installs upgrade on "
         "next relaunch with no manual intervention."),
        ("Offline answer resilience",
         "Localstorage backup of every keystroke. On reconnect, "
         "background bulk-save catches up. Students never lose answers "
         "to a network blip."),
        ("Mac code-signed",
         "Notarised .dmg distributed via /download/mac and "
         "/download/mac-x64. Windows code-signing pending Azure Trusted "
         "Signing."),
    ])

    elements.append(PageBreak())

    # ── 3. Teacher dashboard ──
    section(elements, "3. Teacher Dashboard",
            "Real-time control plane at app.procta.net. Single-page app "
            "with sticky chrome and single-scroll content per pattern.")
    feature_table(elements, [
        ("Live sessions",
         "47-student grid view with live face-frames over WebSocket, "
         "auto-updating risk scores, severity badges, force-submit, "
         "broadcast messages."),
        ("AI live-risk triage",
         "One-line LLM summary per session, cached 60 s in Redis. "
         "Surfaces the 3 students most likely to be cheating right now."),
        ("Forensics timeline",
         "Per-session scrub-through of every violation event with linked "
         "evidence screenshots. Downloadable PDF report."),
        ("Results table",
         "Sortable, filterable, with per-row risk scores, calibration "
         "tier, and CSV / Excel / PDF / scorecard-ZIP exports."),
        ("Post-exam analytics",
         "Score distribution (10 buckets), per-question difficulty + "
         "discrimination index, violation heatmap, risk band breakdown. "
         "Cached 60 s."),
        ("Real-time risk alerts",
         "SSE toast notifications when a student's risk score crosses "
         "thresholds, with deep-link to the timeline."),
        ("Question editor + lint",
         "MCQ / multi-select / true-false / short-answer types. AI "
         "lint runs each question pre-publish to surface ambiguity, "
         "leakage, or factual errors."),
        ("Question bank + import / export",
         "Reusable pool of questions across exams. CSV / JSON bulk "
         "import with preview. Tag-based filtering."),
        ("Exam templates",
         "Save an exam configuration + question set as a template; "
         "clone for next semester or section in one click."),
        ("Student groups / sections",
         "Restrict exam access by group membership. Bulk add from "
         "registered students."),
        ("Live chat with students",
         "Bidirectional WebSocket chat. Teacher broadcast or 1:1. "
         "Chat hub bounded with eviction (50 sockets/tenant, 4 h "
         "metadata TTL)."),
        ("Live teacher intervention (Warn / Pause / Resume / End)",
         "Three escalating actions on each live-session row. Warn pushes "
         "an amber banner + chime to the student with a chip-coded reason "
         "(eyes_off_screen / phone_visible / talking_to_someone / "
         "multiple_tabs / other). Pause locks the student UI with a "
         "full-screen overlay AND stops the exam timer; resume credits "
         "the paused interval back. End requires re-authentication and "
         "a chip-coded reason (academic_dishonesty / identity_fraud / "
         "environment_issue / repeated_violations / student_request / "
         "technical_failure / other); the reason persists on the session "
         "row, embeds in the audit-trail violation, and surfaces in the "
         "scorecard PDF + CSV export."),
        ("Cluster review (bulk false-positive triage)",
         "Groups not-yet-dismissed violations by (exam, type, severity) "
         "across an entire cohort. One-click bulk dismiss with a reason "
         "stamps dismissed_at + dismissed_reason on every row in scope. "
         "Critical at 3,500-student exam scale where per-session review "
         "doesn't fit a teacher's afternoon."),
        ("Onboarding wizard",
         "5-step intro for first-time teachers. Persists "
         "<i>procta_onboarded</i> in localStorage; ? button re-opens."),
    ])

    # ── 4. Student experience ──
    section(elements, "4. Student Experience",
            "Email invite → desktop install → calibration → exam. "
            "Calibration is mandatory and quality-scored.")
    feature_table(elements, [
        ("Invite-driven enrolment",
         "Teacher sends email invite; clicking the link auto-creates "
         "the student account on first /validate-student call. No "
         "pre-registration needed."),
        ("9-point gaze calibration",
         "Mandatory before exam start. Quality tier baked into the "
         "session record so teachers can flag suspicious-tight "
         "calibrations later."),
        ("Continuous identity verification",
         "Face embedding sampled throughout calibration; mismatch "
         "aborts the exam with a typed <i>calibration_abort</i> event "
         "that pages the teacher."),
        ("ID-document verification",
         "Selfie + photo-ID upload. Stored as evidence; teacher "
         "reviews in dashboard pending-verifications panel."),
        ("In-exam chat with invigilator",
         "Floating action button on the exam window. Encrypted, "
         "auth-scoped to the session."),
        ("Practice mode",
         "Sandbox session_id (PRACTICE_…) that exercises the full "
         "flow against canned questions. No DB writes."),
        ("Multi-exam lobby",
         "Single auth → list of available exams → pick one → "
         "calibrate → write."),
        ("Student performance history",
         "Longitudinal view across all exams the student has taken "
         "with this teacher (when student account is connected)."),
    ])

    elements.append(PageBreak())

    # ── 5. Grading & assessment ──
    section(elements, "5. Grading and Assessment")
    feature_table(elements, [
        ("Auto-graded MCQ / multi-select / T-F",
         "Scored at submit. Multi-select uses set-equality. Shuffled-"
         "option mappings preserved per-session for accurate grading."),
        ("Short-answer with AI-suggested grading",
         "Teacher writes reference + rubric + max_score. LLM proposes "
         "score + 1-2 sentence rationale + confidence (high/med/low). "
         "Teacher confirms or overrides; only <i>teacher_score</i> "
         "counts in the gradebook."),
        ("Rolled-up scoring",
         "On grade-confirm, MCQ correct + sum(teacher_score) "
         "auto-recomputes the session total + percentage. Idempotent — "
         "safe to re-run."),
        ("Per-student scorecard PDF",
         "Header, score summary, per-question table (student answer "
         "vs reference, correct/incorrect), violation summary, "
         "calibration tier. When the session was force-submitted by a "
         "teacher, a Termination row surfaces the reason chip + the "
         "free-text note so the document is defensible in a later "
         "grade-challenge or DPDP review."),
        ("Bulk scorecard ZIP",
         "Streams scorecards for an entire exam as a ZIP via "
         "BytesIO. Doesn't load all PDFs into memory."),
        ("Email scorecards",
         "Idempotent per <i>scorecard_emailed_at</i>. Teacher can "
         "click twice; already-emailed students skipped."),
        ("Composite risk score (0–100)",
         "Log-saturating per-violation-type weights, "
         "duration-normalised. Cached. Recomputable via admin "
         "backfill endpoint."),
        ("AI session narrative",
         "1-paragraph human-readable summary of a student's session "
         "(LLM over the last 80 violation events). Cached 60 s."),
    ])

    # ── 6. Operations & infrastructure ──
    section(elements, "6. Operations and Infrastructure")
    feature_table(elements, [
        ("FastAPI + async stack",
         "All hot-path endpoints async via <code>_atable</code>; "
         "supabase client wraps run in a worker thread for sync calls. "
         "300+ concurrent students target on $6 droplet."),
        ("Hardware governor (CPU-adaptive proctor cadence)",
         "Reads CPU% via psutil every 5 s on the student machine. "
         "When CPU > 85% for two consecutive samples, ML inference "
         "drops to 1 frame per 2 s (event-only mode) and the audio "
         "worker skips every other ASR pass; rampback when CPU < 60%. "
         "Keeps the exam UI responsive on Rs. 30,000 budget Lenovo "
         "IdeaPad-class laptops instead of freezing under thermal "
         "throttling. Logs client_throttled (info severity) so "
         "teachers can correlate 'student exam felt slow' with hardware."),
        ("On-device model download bootstrap",
         "scripts/download_audio_models.py invoked from Electron after "
         "pip install completes. Pulls Vosk en-IN + hi-IN + Silero VAD "
         "into ./weights/ with SHA-256 verification. Idempotent; cached "
         "after first run. Falls through to the existing RMS-only voice "
         "path if a download fails so the proctor never blocks on it."),
        ("Router decomposition",
         "8 domain routers: auth / exam / admin / question_bank / "
         "grading / public / sse / chat. Single 7,000-line main.py "
         "shrunk to 382."),
        ("Pydantic strict-mode validation",
         "<code>ConfigDict(strict=True)</code> on every request body "
         "model. Rejects type-coercion attacks (e.g. \"123\" for an "
         "int field) at the boundary."),
        ("Redis + in-memory caching",
         "Two-layer: per-process LRU + Redis. Sorted-set LRU eviction "
         "for live frames (50-session cap). Cache-keyed risk scores, "
         "analytics, AI triage."),
        ("Atomic invite cap",
         "Postgres RPC <code>claim_invite_cap</code> uses conditional "
         "UPDATE under row lock. Concurrent senders can't overshoot "
         "the daily cap."),
        ("Streaming CSV / PDF exports",
         "<code>StreamingResponse</code> + async generators. "
         "Doesn't OOM on 5,000-row results tables."),
        ("Structured JSON logs + request IDs",
         "Every request gets an <code>X-Request-ID</code>; logs are "
         "JSON for grep + log-aggregation tooling."),
        ("Sentry integration (optional)",
         "<code>SENTRY_DSN</code> env var enables capture; off by "
         "default for local dev."),
        ("Security headers",
         "CSP, HSTS, X-Frame-Options DENY, Permissions-Policy "
         "(camera + mic blocked at the app level for non-exam paths)."),
        ("ETag + conditional requests",
         "Middleware adds ETags to JSON responses ≤1 MB; 304 on "
         "matching <code>If-None-Match</code>."),
        ("GZip response compression",
         "60-80% reduction on JSON >500 B. Static assets pre-gzipped "
         "at Docker build, served by Caddy."),
        ("Auto-update pipeline",
         "GitHub Releases auto-discovered by backend (10 min TTL). "
         "Download buttons resolve to the latest release without "
         "redeploy."),
        ("Migration discipline",
         "15 numbered SQL migrations, all idempotent. PGRST204 schema-"
         "cache drift handled with runtime fallback."),
        ("Test suite",
         "14 pytest files: alerts, sessions, history, summary, "
         "behavioural analysis, e2e proctor, forensics timeline, "
         "invites, supporting modules. CI excludes heavy proctor "
         "tests via marker."),
    ])

    elements.append(PageBreak())

    # ── 7. Communication ──
    section(elements, "7. Communication and Workflow")
    feature_table(elements, [
        ("Email invites with reminders",
         "1 h + 24 h reminder cadence for unaccepted invites. Tracked "
         "via <code>opened_at</code>, <code>clicked_at</code>, "
         "<code>accepted_at</code>."),
        ("Real-time teacher↔student chat",
         "Reconnection-tolerant WS. Per-session message history "
         "bounded by deque + cleanup loop."),
        ("Broadcast announcements",
         "Teacher → all live students in an exam. Persists in chat "
         "history."),
        ("Force-submit + clear session",
         "Time-windowed clear tokens (60 s TTL); audit-trailed "
         "violation event for every clear."),
        ("Webhook outbound (planned)",
         "Schools' SIS systems. Stub exists; payload schema pending."),
    ])

    # ── 8. Privacy and compliance ──
    section(elements, "8. Privacy and Compliance Posture")
    feature_table(elements, [
        ("RLS policies on all tables",
         "Every table enforces teacher_id filtering at the DB level "
         "via Supabase RLS. Even if the API has a bug, cross-tenant "
         "data is unreachable."),
        ("JWT-scoped session ownership",
         "Session keys embed teacher_id UUID; access validated against "
         "JWT <code>tid</code> claim on every authenticated call."),
        ("Encrypted at rest",
         "Supabase Postgres encryption + DigitalOcean volume "
         "encryption."),
        ("HTTPS-only + HSTS",
         "Caddy auto-provisions Let's Encrypt; HSTS preload-eligible."),
        ("Zero raw-video storage",
         "Camera frames are processed on the student's device by the "
         "on-device ML stack. Only JPEG snapshots of flagged moments "
         "are uploaded as evidence; no continuous video reaches "
         "Procta servers. Materially reduces both the DPDP-aligned "
         "consent surface and the operating cost of storage."),
        ("Zero raw-audio storage",
         "Microphone capture is analysed by the on-device Vosk + "
         "Silero VAD + MFCC pipeline. Only event metadata "
         "(keyword_uttered with the matched phrase, "
         "multiple_voices_detected with a confidence score) and the "
         "synchronously-captured camera JPEG are uploaded. Raw audio "
         "never leaves the student's machine."),
        ("Auto-delete evidence retention",
         "Cron in entrypoint.sh auto-purges evidence JPEGs older than "
         "SCREENSHOT_RETENTION_DAYS (default 90). Aligned with DPDP "
         "storage limitation and the common 90-day exam-record "
         "retention norm in Indian higher education."),
    ])

    # ── 9. What's next ──
    section(elements, "9. Roadmap Snapshot",
            "Items with working code or migrations queued. Prioritised by "
            "school-procurement leverage.")
    feature_table(elements, [
        ("Windows code signing",
         "Azure Trusted Signing $10/mo. Removes SmartScreen warning "
         "for Windows downloads — currently deal-blocker for school "
         "IT."),
        ("Mobile PWA (lite proctoring)",
         "Camera-based proctoring still works; tab-switch prevention "
         "doesn't. Position as low-stakes-quiz tool."),
        ("Inbuilt coding questions",
         "Server-side sandboxed execution (Firecracker + isolate) judges "
         "JS/TS/Python/C/C++/Java against hidden tests. Design approved; build pending."),
        ("Per-criterion rubric grading",
         "Extend short-answer to (clarity / correctness / depth) × "
         "marks. Universities specifically asked."),
        ("Cross-cohort plagiarism check",
         "Cosine sim on embeddings of every short-answer pair. Cheap "
         "with a small embedding model."),
        ("Two-factor for teacher login",
         "TOTP via authenticator app."),
        ("Bulk exam scheduling",
         "Clone exam to N sections × M time slots. Coaching-institute "
         "ergonomic win."),
    ])

    elements.append(PageBreak())

    # ── 10. Future vision (ambitious / multi-quarter bets) ──
    section(elements, "10. Future Vision",
            "Bigger bets — 6–24 months out. Not on the immediate sprint, "
            "but the direction Procta evolves to become a category-"
            "defining platform rather than a proctoring tool.")
    feature_table(elements, [
        ("React dashboard rewrite",
         "The current 7,000-line vanilla-JS dashboard is functional but "
         "ages every time a feature lands. Migrate to a React + Vite "
         "SPA matching the marketing site's design system. Wins: "
         "component reuse, dark/light theme switching, keyboard "
         "shortcuts, fluid loading states, drag-to-reorder, "
         "auto-escaping (kills the entire XSS class), and a real "
         "design language for sales screenshots."),
        ("Inbuilt coding questions",
         "Server-side sandboxed execution (Firecracker microVM + isolate, "
         "network-isolated) runs JS/TS/Python/C/C++/Java against hidden tests "
         "and grades authoritatively. CodeMirror editor; Run = sample tests, "
         "Submit = hidden graded tests. Ships as a self-hostable on-prem "
         "appliance. Design approved; build pending."),
        ("AI exam generator from syllabus",
         "Upload a PDF syllabus or paste a topic list; LLM produces a "
         "balanced 40-question exam tagged by Bloom's level + "
         "difficulty. Teacher tweaks → publish. Saves the 2-hour exam "
         "authoring session that's currently every teacher's worst job."),
        ("On-device speaker identification (deferred)",
         "Today's multi-voice detection answers \"two voices yes/no\" "
         "via MFCC clustering. Speaker identification would add a "
         "stable label per voice (\"voice A\" / \"voice B\") and "
         "track when each appears. Requires either a pyannote ONNX "
         "export (~30 MB additional model) or a custom siamese-network "
         "fingerprint head. Worth doing only if customer feedback shows "
         "the yes/no signal is leaving evidence on the table."),
        ("Native mobile apps (iOS + Android)",
         "React Native shell wrapping the same proctoring engine "
         "compiled for ARM. Position as the primary tool for "
         "low/medium-stakes assessments where the desktop install is "
         "friction. Camera-based proctoring still works; lockdown is "
         "weaker but compensated with stricter face-presence policy."),
        ("Teacher mobile app",
         "Push-notify on high-risk events. Swipe-right to acknowledge, "
         "swipe-left to force-submit. For invigilators monitoring "
         "remotely. Pairs with the desktop dashboard, doesn't replace."),
        ("LMS integrations",
         "Canvas, Moodle, Blackboard, Google Classroom plugins. "
         "Single-sign-on, exam launch from inside the LMS, scores "
         "auto-pushed back. The integration story is what unblocks "
         "university procurement deals."),
        ("Adaptive testing (IRT-based)",
         "Questions adjust difficulty to the student's demonstrated "
         "level mid-exam, using item-response theory. Same total time, "
         "more accurate measurement, harder to brute-force-cheat "
         "(no two students see the same question sequence)."),
        ("Teacher AI assistant",
         "Chat with your gradebook in natural language: "
         "<i>“Which students improved most this semester?”</i> "
         "<i>“Show me question #14's discrimination index across "
         "all sections.”</i> Backed by an LLM with read-only tool "
         "access to the teacher's data."),
        ("AI proctor explainer for students",
         "When a student is flagged, they see a plain-English "
         "explanation: <i>“Your gaze drifted off-screen for 12 s "
         "during Q14, which is longer than 95% of test-takers.”</i> "
         "Removes the “proctoring is a black box” hostility."),
        ("Cross-cohort plagiarism check",
         "Cosine similarity on embeddings of every short-answer pair "
         "across the cohort. Highlights suspicious clusters. Cheap "
         "with a small embedding model run nightly."),
        ("Live invigilator marketplace",
         "When AI flags a high-risk session, optionally route to a "
         "human reviewer (vetted, on demand, paid per minute). "
         "Schools that don't have invigilator capacity buy review "
         "time from the platform. Two-sided revenue stream."),
        ("Multi-monitor detection",
         "Browser screen-API + webcam reflection analysis to detect "
         "secondary monitors. Currently the loophole every prep-school "
         "student exploits."),
        ("rPPG attention + stress metrics",
         "Heart-rate variability inferred from sub-pixel facial colour "
         "changes (research-grade rPPG). Surface attention dropoff "
         "and stress to teachers without storing biometric data — "
         "computed live, never persisted."),
        ("Whiteboard / stylus mode",
         "Students show working on iPad / Wacom tablet for math + "
         "physics exams. Stroke-by-stroke recording for grading and "
         "review. OCR + LLM for partial credit on handwritten "
         "derivations."),
        ("Federated SSO for school districts",
         "SAML / OIDC integration so a district admin manages 50 "
         "schools' teachers from one console. Critical for K-12 "
         "district sales."),
        ("Public API + developer SDK",
         "Third-party integrations on top of Procta — e.g. coaching "
         "institutes plugging Procta into their custom LMS. Rate-"
         "limited, scoped tokens, OpenAPI spec. Becomes a moat."),
        ("On-device LLM for free tier",
         "Llama / Phi running locally for grading, lint, and risk "
         "narratives. Removes Groq dependency and cuts inference cost "
         "to zero for paying schools. Slower but private — useful "
         "selling point in EU."),
        ("Exam marketplace",
         "Teachers publish question banks; others subscribe. "
         "Teachers earn revenue on usage. 30/70 split, Procta keeps "
         "30%. Compounds the network effect once question-bank UX "
         "matures."),
    ])

    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph(
        "<i>Generated from the Procta codebase. Sections 1–8 list features "
        "with shipping code or tracked migrations. Section 9 is near-term "
        "roadmap (working code or designs queued). Section 10 is "
        "directional — bigger bets that define where Procta goes next.</i>",
        MUTED_BODY))

    doc.build(elements)
    print("Wrote Procta_Features.pdf")


if __name__ == "__main__":
    build()
