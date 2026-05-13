import { Helmet } from 'react-helmet-async'
import useInView from '../hooks/useInView'
import { Link } from 'react-router-dom'
import { UserPlus, Monitor, ShieldCheck, FileText } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

const steps = [
  {
    icon: UserPlus,
    title: '1. Teacher Creates Exam & Invites Students',
    body: 'Teachers log into the Procta dashboard, create an exam with duration, pass percentage, and optional access code. Questions can be MCQs (single/multi-select) or true/false. Students are registered individually or via bulk CSV upload — each receives an email invite with a unique link. The system supports optional exam scheduling with automatic 24-hour and 1-hour email reminders.'
  },
  {
    icon: Monitor,
    title: '2. Student Launches the Proctored Browser',
    body: 'Students click the invite link, enter their roll number, and download the Procta browser — a custom Electron app that enforces full-screen kiosk mode. The app runs a calibration step (gaze tracking, face detection, and object recognition models load locally). The student\'s webcam activates for identity verification: a selfie and ID card photo are sent to the teacher for manual approval before the exam begins.'
  },
  {
    icon: ShieldCheck,
    title: '3. AI Monitoring During the Exam',
    body: 'Throughout the exam, all AI processing runs on the student\'s device — no video is streamed to the cloud. The system tracks gaze direction (flags prolonged off-screen looks), face presence (detects absence or multiple faces), objects (phones, books, earphones via YOLOv8n), and audio (sustained speech patterns). Violations are logged with severity and confidence scores. The teacher sees a live risk dashboard with real-time updates via SSE.'
  },
  {
    icon: FileText,
    title: '4. Automated Scoring & Reporting',
    body: 'When the timer expires or the student submits, answers are graded instantly. Scorecards with question-wise results are generated as PDFs and emailed to each student. Teachers can export CSV/Excel reports with violation counts, risk scores, and time analytics. A forensics timeline provides timestamped evidence for every flagged event, including screenshot attachments for high-severity violations.'
  },
]

export default function HowItWorksPage() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>How AI Proctoring Works — 4-Step Exam Flow | Procta</title>
        <meta name="description" content="See how Procta's AI proctoring works in 4 steps: exam creation, kiosk-mode browser launch, real-time AI monitoring, and automated scoring. On-device processing ensures privacy." />
        <link rel="canonical" href="https://procta.net/how-it-works" />
        <meta property="og:title" content="How AI Proctoring Works — 4-Step Exam Flow | Procta" />
        <meta property="og:description" content="Learn how Procta's AI proctoring works: exam creation, secure browser launch, real-time AI monitoring, and automated scoring." />
        <meta property="og:url" content="https://procta.net/how-it-works" />
      </Helmet>
      <Navbar />
      <section className="pt-32 pb-20 md:pt-44 md:pb-32">
        <div className="mx-auto max-w-7xl px-6">
          <div initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }} className="mx-auto max-w-3xl text-center">
            <h1 className="font-display text-4xl font-bold text-white md:text-5xl lg:text-6xl">
              How{' '}
              <span className="bg-gradient-to-r from-accent to-accent-light bg-clip-text text-transparent">AI Proctoring</span>{' '}
              Works
            </h1>
            <p className="mt-6 text-lg leading-relaxed text-slate-400 md:text-xl">
              From exam creation to automated scorecards — four steps that take the stress out of online exams.
            </p>
          </div>

          <div className="mt-20 space-y-16">
            {steps.map((step, i) => (
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
