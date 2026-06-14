import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, Smartphone, IndianRupee, Users, ShieldCheck } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

/**
 * /coaching — SEO landing for the ICP.
 *
 * Long-tail target: "AI proctoring for coaching institutes", "online test
 * proctoring for coaching India", "JEE/NEET mock test proctoring software",
 * "remote exam proctoring for coaching centres". Coaching chains run more
 * proctored mock tests per month than universities run per year — this is
 * the highest-intent, lowest-competition query space for Procta.
 */
export default function CoachingInstitutes() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>AI Proctoring for Coaching Institutes in India | Procta</title>
        <meta name="description" content="Online proctoring built for Indian coaching institutes — run secure JEE/NEET-style mock tests at scale with Procta Secure Browser (PSB), on-device AI monitoring, phone-cam room scan and auto-grading. ₹80/student, INR + GST billing." />
        <link rel="canonical" href="https://www.procta.net/coaching" />
        <meta property="og:title" content="AI Proctoring for Coaching Institutes in India | Procta" />
        <meta property="og:description" content="Run secure mock tests at scale — PSB lockdown browser, on-device AI monitoring, phone-cam room scan, auto-grading. ₹80/student, INR + GST." />
        <meta property="og:url" content="https://www.procta.net/coaching" />
        <meta property="og:type" content="article" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>
      <Navbar />

      <article className="pt-32 pb-20 md:pt-44 md:pb-32">
        <div className="mx-auto max-w-4xl px-6">
          <div className="animate-fadeIn">
            <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-accent-light hover:text-accent no-underline mb-8">
              <ArrowLeft size={14} /> Back to home
            </Link>
            <span className="label-mono text-accent">AI proctoring for coaching institutes</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              Online proctoring built for Indian coaching institutes
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              Coaching chains run more proctored mock tests in a month than most universities run in a year.
              Procta runs secure online exams at that volume — JEE/NEET-style numerical papers, weekly mocks,
              and full-syllabus tests — with the <strong className="text-white">Procta Secure Browser (PSB)</strong> lockdown,
              on-device AI proctoring, phone-camera room monitoring, and automatic grading. Priced for India at
              <strong className="text-white"> ₹80/student</strong> with INR + GST invoicing.
            </p>
          </div>

          <div className="mt-14 grid gap-5 sm:grid-cols-2">
            <Feature icon={<ShieldCheck size={20} />} title="PSB lockdown browser"
              body="Students sit the exam inside Procta Secure Browser (PSB) — fullscreen lock, copy/paste blocked, app-switch, VM and remote-desktop detection. Works on Windows and macOS." />
            <Feature icon={<Smartphone size={20} />} title="Phone-camera room scan"
              body="The student's own phone becomes a second camera covering the room — catching off-screen notes and helpers that a single webcam misses. Included on every plan." />
            <Feature icon={<Users size={20} />} title="Built for scale"
              body="Bulk-import rosters by batch/cohort, assign a mock to a whole batch, and monitor every live session from one dashboard. Architecture headroom for thousands of concurrent students." />
            <Feature icon={<IndianRupee size={20} />} title="Priced for India"
              body="₹80/student, INR with GST invoices, 14-day free trial without a credit card. A fraction of Mercer Mettl / ProctorU enterprise pricing for the same AI stack." />
          </div>

          <div className="mt-14 rounded-2xl border border-white/[0.08] bg-navy-900/60 p-8">
            <h2 className="font-display text-2xl font-bold text-white">Why coaching institutes pick Procta</h2>
            <ul className="mt-5 space-y-3 text-slate-300">
              {[
                'Run weekly mock tests with the same secure setup every time — assign exams to a batch, students get standing access, no re-registration.',
                'On-device AI: camera frames are analysed on the student PC. No raw video is recorded or uploaded — DPDP-friendly and bandwidth-light.',
                'Import existing question papers from PDF/Word, including JEE/NEET numerical-value questions with tolerance ranges.',
                'Self-host in Docker for data sovereignty, or let us run it. Either way the exam-day workflow is the same.',
              ].map((t, i) => (
                <li key={i} className="flex gap-3">
                  <Check className="mt-1 shrink-0 text-emerald-400" size={18} />
                  <span>{t}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-14 text-center">
            <h2 className="font-display text-2xl font-bold text-white">Run your first proctored mock this week</h2>
            <p className="mt-3 text-slate-400">14-day free trial. No credit card. Live in a day.</p>
            <Link to="/signup" className="mt-6 inline-flex items-center gap-2 rounded-xl bg-accent px-6 py-3 font-semibold text-navy-950 no-underline accent-glow hover:opacity-90">
              Start free trial
            </Link>
          </div>
        </div>
      </article>

      <Footer />
    </div>
  )
}

function Feature({ icon, title, body }) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-navy-900/60 p-6">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/15 text-accent">{icon}</div>
      <h3 className="mt-4 font-display text-lg font-bold text-white">{title}</h3>
      <p className="mt-2 text-sm text-slate-400 leading-relaxed">{body}</p>
    </div>
  )
}
