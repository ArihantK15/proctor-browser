import { motion } from 'framer-motion'
import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { UserPlus, Monitor, ShieldCheck, FileText, Smartphone, MessageSquare } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

const steps = [
  {
    icon: UserPlus,
    title: '1. Teacher sets up the exam',
    body: 'Create the exam and set duration and access rules. Add questions by importing an existing PDF or Word paper, generating them from your notes, or typing them in — MCQ, multi-select, true/false, numeric/integer with a tolerance range, and AI-graded short answer, all reusable from a question bank. Import students by CSV with auto-detection of CBSE, JEE, and NTA roll-number formats, assign groups, duplicate exams across batches, and send invite/reminder emails. Org admins can manage members, billing, security, and support without touching teacher-only exam tools.'
  },
  {
    icon: Monitor,
    title: '2. Student launches Procta Secure Browser',
    body: 'Students install Procta Secure Browser (PSB) for Windows or macOS, enter the exam, complete camera and face calibration, and continue inside a locked-down runtime that watches fullscreen, copy/paste, app switching, remote-desktop tools, VMs, and crashes. Answers autosave locally and sync to the server.'
  },
  {
    icon: Smartphone,
    title: '3. Optional phone camera pairs by QR',
    body: 'For higher-stakes exams, the student scans a QR code and uses their phone as a room camera. Teachers get desk and side-device context without forcing the laptop camera to do everything.'
  },
  {
    icon: ShieldCheck,
    title: '4. AI monitoring runs during the exam',
    body: 'Face, gaze, head pose, eye state, object detection, and audio analysis run on the student machine. The audio stack does more than RMS: on-device speech-to-text (Vosk en-IN + hi-IN) flags spoken cheat phrases, and Silero VAD + MFCC clustering catches a second voice in the room. No raw audio or continuous video ever leaves the device. ML and ASR inference auto-throttle on hot CPUs so budget laptops stay responsive. The server receives violation events, confidence, risk score, and low-rate evidence snapshots, while the teacher sees a live dashboard with sub-1s camera pop-in on flagged sessions.'
  },
  {
    icon: MessageSquare,
    title: '5. Teachers intervene when needed',
    body: 'A three-button intervention bar sits on every live session. Warn pushes an amber banner and chime to the student with a chip-coded reason. Pause locks the student screen and stops their exam timer so a teacher-investigation window does not cost the student exam time; resume credits the paused interval back. End is the destructive action — it requires re-authentication, a chip-coded reason from a fixed allowlist, and persists that reason on the session row, the audit trail, and the scorecard PDF. Teachers can also broadcast instructions, chat with individual students even during a pause, triage violation clusters by type and severity for bulk dismissal, inspect the timeline, and file issue reports from the dashboard. AI flags inform a human decision instead of automatically punishing a student.'
  },
  {
    icon: FileText,
    title: '6. Results, grading, and evidence are ready',
    body: 'MCQs score immediately. Short-answer AI suggestions are generated in parallel and confirmed by teachers. The institution gets CSV exports, branded PDFs, violation timelines, risk explanations, screenshots, scorecards, and appeal-ready evidence packets. Billing runs on Razorpay UPI Autopay subscriptions with INR- and GST-ready plans.'
  },
]

export default function HowItWorksPage() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>How Procta Works — Complete Secure Exam Workflow | Procta</title>
        <meta name="description" content="See Procta's secure exam workflow: exam setup, Procta Secure Browser (PSB), phone camera room scan, on-device AI proctoring, live teacher intervention, AI grading, scorecards, and evidence packets." />
        <link rel="canonical" href="https://www.procta.net/how-it-works" />
        <meta property="og:title" content="How AI Proctoring Works — 4-Step Exam Flow | Procta" />
        <meta property="og:description" content="Learn how Procta's AI proctoring works: exam creation, secure browser launch, real-time AI monitoring, and automated scoring." />
        <meta property="og:url" content="https://www.procta.net/how-it-works" />
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
              How{' '}
              <span className="bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">AI Proctoring</span>{' '}
              Works
            </h1>
            <p className="mt-6 text-lg leading-relaxed text-slate-400 md:text-xl">
              From roster import to evidence packets - the full exam-day workflow your faculty, IT team, and students need.
            </p>
          </motion.div>

          <div className="mt-20 space-y-16">
            {steps.map((step) => (
              <div
                key={step.title}
                className="flex flex-col gap-6 md:flex-row md:items-start md:gap-10"
              >
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-accent/10 border border-accent/20">
                  <step.icon size={28} className="text-accent-light" />
                </div>
                <div>
                  <h2 className="font-display text-xl font-bold text-white md:text-2xl">{step.title}</h2>
                  <p className="mt-3 text-base leading-relaxed text-slate-400 max-w-3xl">{step.body}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-20 text-center">
            <p className="text-slate-400 mb-4">Ready to run your first proctored exam?</p>
            <Link to="/signup" className="inline-flex items-center gap-2 rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline">
              Get Started Free
            </Link>
            <p className="mt-4 text-xs text-slate-500">No credit card required. Takes 2 minutes to set up.</p>
          </div>
        </div>
      </section>
      <Footer />
    </div>
  )
}
