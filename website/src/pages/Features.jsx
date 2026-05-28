import { motion } from 'framer-motion'
import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { CheckCircle } from 'lucide-react'
import Navbar from '../components/Navbar'
import CTA from '../components/CTA'
import Footer from '../components/Footer'

const featureGroups = [
  {
    title: 'Exam Setup & Operations',
    desc: 'The administrative layer that makes a real college exam run smoothly before students ever open the browser.',
    features: [
      'Create exams with windows, durations, pass marks, access codes, and optional calculator/scratchpad rules',
      'Bulk CSV roster import with dry-run validation and roll-number format detection',
      'Student groups, exam cloning, scheduling, reminders, and controlled invite links',
      'Role-based dashboards for teachers, org admins, and superadmins',
      'Issue reporting flow for teachers to flag bugs, feature requests, or session problems',
    ]
  },
  {
    title: 'AI-Powered Proctoring',
    desc: 'Real-time detection that runs entirely on the student\'s device — no cloud latency, no privacy exposure.',
    features: [
      'Gaze tracking detects prolonged off-screen looks that indicate external references',
      'Face detection with MediaPipe ensures the registered candidate stays present',
      'Object detection (YOLOv8n) identifies phones, books, earphones, and other unauthorized items',
      'Audio analysis flags sustained speech patterns suggesting dictation or collaboration',
      'VM and remote desktop detection prevents proxy-test-taker attacks',
      'Phone-camera room monitoring via QR pairing for desk and side-device visibility',
      'Violation-triggered camera pop-in lets teachers inspect a flagged session quickly',
    ]
  },
  {
    title: 'Automated Grading & Scorecards',
    desc: 'From exam end to published results in seconds — no manual marking, no spreadsheet errors.',
    features: [
      'Instant MCQ/scoring with support for single-choice, multi-choice, and true/false',
      'Automated scorecard PDFs emailed to each student with question-wise breakdown',
      'AI-generated personalized insight on every scorecard (optional, uses LLM)',
      'Parallel short-answer grading suggestions reduce large review batches from minutes to seconds',
      'Cluster review — triage thousands of flagged events by type and severity, bulk-dismiss systemic false positives in one click',
      'CSV/Excel export with risk scores, violation counts, and time analytics',
      'Configurable pass thresholds and percentage-based grading',
      'Teacher confirmation before AI-suggested grades become final',
    ]
  },
  {
    title: 'Kiosk-Mode Lockdown',
    desc: 'Full-screen exam environment that prevents cheating at the OS level.',
    features: [
      'Forces full-screen mode and detects alt-tab, window-switch, and screenshot attempts',
      'Blocks right-click, copy-paste, and keyboard shortcuts during the exam',
      'Continuous 60-second auto-save prevents data loss on crash',
      '5-second answer save path with offline resilience for unstable student networks',
      'Automatic submission when the timer expires — no grace period loopholes',
      'Adaptive hardware governor — auto-reduces ML inference rate on heat-stressed CPUs to keep budget laptops responsive',
      'Offline resilience: answers saved locally if connectivity drops, synced on reconnect',
    ]
  },
  {
    title: 'Identity Verification',
    desc: 'Multi-step verification before the exam starts, with manual teacher approval.',
    features: [
      'Student selfie capture at exam start for facial matching',
      'ID card photo upload (college ID, driver\'s license, etc.)',
      'Teacher dashboard for side-by-side comparison and manual approve/reject',
      'All verification images are stored for audit trails',
      'Re-verification triggers on suspicious behavior during the exam',
    ]
  },
  {
    title: 'Live Monitoring Dashboard',
    desc: 'Real-time visibility into every active exam session across your institution.',
    features: [
      'Live camera feed — click to view any student\'s webcam in real time',
      'Risk score heatmap (0-100) with color-coded alerts for high-risk sessions',
      'Live violation timeline showing every detected anomaly as it happens',
      'AI triage: one-line LLM summary of each session\'s risk posture',
      'Configurable alert thresholds and push notifications',
      'Teacher-student chat, broadcast announcements, force-submit, and stale-session controls',
      'Sub-1s camera pop-in — violation triggers pre-warmed thumbnail, click shows instant frame',
      '3,500-student architecture target with 6,500 live-frame cache headroom',
    ]
  },
  {
    title: 'Forensics & Audit Trail',
    desc: 'Comprehensive evidence for academic integrity committees.',
    features: [
      'Timeline view of every violation with severity, type, and timestamp',
      'Screenshot evidence automatically captured for high/medium severity events',
      'Confidence scores for every detection to minimize false positives',
      'Exportable PDF reports with violation summaries and visual evidence',
      'Complete session replay with answers, timing, and proctor events',
      'Appeal-ready language that separates detector confidence from teacher decision',
      'Request IDs and audit trails for support and incident reconstruction',
    ]
  },
  {
    title: 'Student Management & Scheduling',
    desc: 'Bulk operations that scale to thousands of students.',
    features: [
      'Bulk registration via CSV upload with automatic email invites',
      'Smart access codes per exam for controlled entry',
      'Configurable exam windows with start/end times and duration limits',
      'Automated email reminders (24h and 1h before exam start)',
      'Student groups for managing access to specific exams',
    ]
  },
  {
    title: 'Integrations, Billing & Deployment',
    desc: 'Everything an institution asks about after the demo: LMS fit, payments, support, and operational reliability.',
    features: [
      'LTI 1.3 launch path for Canvas and Moodle',
      'Google Classroom course sync paths',
      'Razorpay Checkout and UPI Autopay subscription workflow',
      'INR + GST-ready plan packaging with quota and overage enforcement',
      'Docker-based deployment, Caddy TLS, Redis/RQ workers, and CI security gates',
    ]
  },
]

export default function FeaturesPage() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>AI Proctoring Features — Gaze Tracking, Object Detection &amp; More | Procta</title>
        <meta name="description" content="Explore Procta's complete exam platform: secure Electron browser, on-device AI proctoring, phone camera room scan, live dashboard, AI grading, evidence packets, LTI, Razorpay billing, and 3,500-student architecture target." />
        <link rel="canonical" href="https://www.procta.net/features" />
        <meta property="og:title" content="AI Proctoring Features — Gaze Tracking, Object Detection & More | Procta" />
        <meta property="og:description" content="Complete AI proctoring feature set: gaze tracking, face detection, object recognition, kiosk-mode lockdown, automated grading, live monitoring, and forensics." />
        <meta property="og:url" content="https://www.procta.net/features" />
        <meta property="og:type" content="website" />
        <meta property="og:image" content="https://www.procta.net/og-image.png" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>
      <Navbar />
      <section className="pt-32 pb-20 md:pt-44 md:pb-32">
        <div className="mx-auto max-w-7xl px-6">
          <motion.div initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }} className="mx-auto max-w-3xl text-center">
            <h1 className="font-display text-4xl font-bold text-white md:text-5xl lg:text-6xl">
              Everything You Need for{' '}
              <span className="bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">Secure Online Exams</span>
            </h1>
            <p className="mt-6 text-lg leading-relaxed text-slate-400 md:text-xl">
              A complete exam integrity platform — not just a webcam plugin. From
              roster import and secure browser launch to phone-cam monitoring,
              AI grading, evidence packets, LMS integration, and billing,
              Procta covers every phase of the exam lifecycle.
            </p>
          </motion.div>

          <div className="mt-20 space-y-24">
            {featureGroups.map((group) => (
              <div
                key={group.title}
                className="border-b border-white/[0.06] pb-16 last:border-0 last:pb-0"
              >
                <h2 className="font-display text-2xl font-bold text-white md:text-3xl">{group.title}</h2>
                <p className="mt-3 text-base leading-relaxed text-slate-400 max-w-2xl">{group.desc}</p>
                <ul className="mt-6 grid gap-3 sm:grid-cols-2">
                  {group.features.map((f, fi) => (
                    <li key={fi} className="flex items-start gap-3 text-sm text-slate-300">
                      <CheckCircle size={18} className="mt-0.5 shrink-0 text-accent" />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="mt-20 text-center">
            <p className="text-slate-400 mb-6">Ready to see Procta in action?</p>
            <Link to="/signup" className="inline-flex items-center gap-2 rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline">
              Request a Demo
            </Link>
          </div>
        </div>
      </section>
      <Footer />
    </div>
  )
}
