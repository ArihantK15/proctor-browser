import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, X, IndianRupee, Smartphone, Lock, MessageSquare } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

export default function CompareHonorlock() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Honorlock alternative — zero raw video at ₹80/student | Procta</title>
        <meta name="description" content="Universities switching from Honorlock get on-device ML (no cloud uploads), phone-cam monitoring, LTI 1.3, and transparent INR billing at ₹80/student from Procta — a fraction of flat-rate US pricing." />
        <link rel="canonical" href="https://www.procta.net/compare/honorlock-vs-procta" />
        <meta property="og:title" content="Honorlock alternative — ₹80/student with on-device ML | Procta" />
        <meta property="og:description" content="No raw video to the cloud. Phone-cam included. INR + GST invoicing. Migrate from Honorlock in under a week." />
        <meta property="og:url" content="https://www.procta.net/compare/honorlock-vs-procta" />
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
            <span className="label-mono text-accent">Honorlock alternative</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              An Indian-built alternative to Honorlock for online proctoring
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              Honorlock combines AI monitoring with live proctor pop-in, available 24/7/365 with
              US-based support. Their pricing is flat-rate per exam or per student but not publicly
              disclosed, and includes an implementation fee. Procta offers an alternative purpose-built
              for the Indian market: on-device machine learning that keeps raw video on the student's
              machine, transparent pricing at ₹80/student, INR + GST invoicing, and phone-camera room
              monitoring on every plan.
            </p>
          </div>

          <div className="mt-14 overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-900/60">
            <table className="w-full text-sm">
              <thead className="bg-navy-900/80 text-left">
                <tr>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Capability</th>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Honorlock</th>
                  <th className="px-5 py-4 font-semibold text-accent text-xs uppercase tracking-wider">Procta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06] text-slate-300">
                <ComparisonRow label="Per-student price (proctored exam)" honorlock="Flat rate — not publicly disclosed" procta="₹80" highlight />
                <ComparisonRow label="Implementation fee" honorlock="One-time fee (amount not public)" procta="No setup fee" />
                <ComparisonRow label="Billing currency" honorlock="USD" procta="INR + GST invoice" />
                <ComparisonRow label="Phone-camera room monitoring" honorlock="Complete View (side camera)" procta="Included on every plan" />
                <ComparisonRow label="On-device ML (no frames leaving student PC)" honorlock={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="AI face / gaze / object detection" honorlock="AI monitoring (no face recognition)" procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Live proctor pop-in" honorlock="AI + Live Pop-In tier" procta="Teacher alerts and chat" />
                <ComparisonRow label="LTI 1.3 integration (Canvas / Moodle)" honorlock={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="LLM-graded short answers" honorlock="Not publicly disclosed" procta="Included on Growth and above" />
                <ComparisonRow label="Live teacher webcam view" honorlock="Not publicly disclosed" procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Real-time chat with student during exam" honorlock={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Search & Destroy leaked exam content" honorlock={<Check className="text-emerald-400" size={18} />} procta="On roadmap" />
                <ComparisonRow label="Free trial without credit card" honorlock="Demo request only" procta="14-day trial on /signup" />
                <ComparisonRow label="Time to first proctored exam" honorlock="~2 days with project-managed setup" procta="10 minutes after signup" />
                <ComparisonRow label="Data residency (India / DPDP Act ready)" honorlock="US-based infrastructure" procta="Mumbai-first, DPDP-aligned" />
                <ComparisonRow label="Self-hosted option" honorlock="Not publicly disclosed" procta="Available on Pro / Enterprise" />
                <ComparisonRow label="UPI Autopay subscriptions" honorlock={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
              </tbody>
            </table>
          </div>

          <p className="mt-4 text-xs text-slate-500 leading-relaxed">
            Honorlock pricing is flat-rate per exam or per student but not publicly listed — each
            institution must contact sales. Third-party estimates suggest their TCO can be significantly
            higher than industry average, especially when full human review is required. Procta's
            ₹80/student is the published pay-as-you-go overage rate; volume plans work out cheaper.
          </p>

          <div className="mt-16 space-y-10">
            <div>
              <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Why Indian institutions choose Procta over Honorlock</h2>
              <p className="mt-3 text-slate-400 leading-relaxed">
                Honorlock is a well-regarded US-based proctoring platform with 24/7 live support and
                patented cell phone detection. But for Indian universities and coaching institutes,
                their US-dollar pricing, implementation fee, and cloud-recording architecture create
                cost and compliance friction that Procta was built to avoid:
              </p>
            </div>

            <FeatureBlock
              icon={<IndianRupee size={20} />}
              title="Published INR pricing vs flat-rate USD"
              body="Honorlock's flat-rate per-exam pricing is not disclosed on their website — every purchase starts with a sales conversation and a one-time implementation fee. Procta publishes ₹80/student transparently. At 10,000 student-exams per month that is ₹8,00,000 — no hidden implementation fee, no USD forex markup."
            />

            <FeatureBlock
              icon={<Lock size={20} />}
              title="No face recognition — but also no cloud uploads"
              body="Honorlock explicitly states it does not use face recognition, fingerprints, or voiceprints. They record HD video of every session to the cloud for review. Procta also does not upload raw video — all ML inference (gaze, object detection, head pose) runs on-device. Only structured violation events leave the student PC. This is a cleaner fit for DPDP Act compliance."
            />

            <FeatureBlock
              icon={<Smartphone size={20} />}
              title="Phone-cam is included, not an add-on"
              body="Honorlock's Complete View feature adds a side camera to monitor the workspace. Procta includes phone-camera room monitoring on every paid plan — students scan a QR code and their phone becomes the room camera, no separate app or hardware needed."
            />

            <FeatureBlock
              icon={<MessageSquare size={20} />}
              title="India-first, not US-first"
              body="Honorlock's support team is US-based and their communication workflow is email-centric. Procta delivers invite links and scorecards via WhatsApp, supports INR payments via UPI Autopay, and stores data in Mumbai — aligned with Indian regulatory expectations and communication habits."
            />
          </div>

          <div className="mt-16">
            <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Migrating from Honorlock to Procta</h2>
            <p className="mt-3 text-slate-400 leading-relaxed">
              Export your student roster and exam data from Honorlock's admin dashboard. Procta's
              bulk-import tool accepts CSV with automatic roll-format detection (CBSE, JEE, NTA
              recognised out of the box). Re-create your exam templates and reissue invite links.
              Most universities and coaching institutes with fewer than 5,000 students complete the
              migration in under one week — significantly faster than Honorlock's ~2-day setup
              timeline, since there is no implementation call or project-managed onboarding.
            </p>
            <ol className="mt-6 space-y-4 text-slate-300">
              <MigrationStep n="1" title="Export student data from Honorlock">
                Honorlock's admin panel supports CSV export of student rosters and exam sessions.
                Export your most recent batch — Procta imports email, full name, and roll number.
              </MigrationStep>
              <MigrationStep n="2" title="Sign up for Procta (no card)">
                <Link to="/signup" className="text-accent-light hover:text-accent">procta.net/signup</Link>. 14-day Starter trial.
                Your admin dashboard is ready in 90 seconds.
              </MigrationStep>
              <MigrationStep n="3" title="Bulk-import your roster">
                Members → Import → drop the CSV. Duplicate detection runs automatically.
              </MigrationStep>
              <MigrationStep n="4" title="Re-create your top exam template">
                Most institutions run 3-10 recurring exam formats. Recreate your most-used one in Procta
                as a smoke test. Question bank import is on the roadmap; paste in questions directly for now.
              </MigrationStep>
              <MigrationStep n="5" title="Run a parallel proctored sitting">
                Schedule the same exam on both platforms with 5-10 trusted students. Compare the
                flagged violation feeds side by side. Most institutions make the switch within 48 hours
                of that test.
              </MigrationStep>
            </ol>
          </div>

          <div className="mt-16 rounded-2xl border border-accent/30 bg-gradient-to-br from-accent/[0.08] to-transparent p-8 md:p-10">
            <h2 className="font-display text-2xl font-bold text-white">Ready to compare side by side?</h2>
            <p className="mt-3 text-slate-300 leading-relaxed">
              Start a free 14-day trial. No credit card. No sales-call gate. Or email us a request to run
              the parallel sitting against your current Honorlock contract and we will help wire it up.
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/signup"
                className="inline-flex justify-center rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
              >
                Start free trial
              </Link>
              <a
                href="mailto:arihantkaul@outlook.com?subject=Honorlock%20comparison%20enquiry"
                className="inline-flex justify-center rounded-xl border border-white/10 bg-white/[0.03] px-7 py-3.5 text-sm font-semibold text-slate-300 hover:border-accent/30 no-underline"
              >
                Email a migration request
              </a>
            </div>
          </div>
        </div>
      </article>

      <Footer />
    </div>
  )
}

function ComparisonRow({ label, honorlock, procta, highlight = false }) {
  return (
    <tr className={highlight ? 'bg-accent/[0.04]' : undefined}>
      <td className="px-5 py-3.5 text-slate-300">{label}</td>
      <td className="px-5 py-3.5 text-slate-400">{honorlock}</td>
      <td className={`px-5 py-3.5 ${highlight ? 'text-accent-light font-semibold' : 'text-slate-200'}`}>{procta}</td>
    </tr>
  )
}

function FeatureBlock({ icon, title, body }) {
  return (
    <div className="flex gap-4">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent-light">
        {icon}
      </div>
      <div>
        <h3 className="font-display text-lg font-semibold text-white">{title}</h3>
        <p className="mt-1.5 text-slate-400 leading-relaxed">{body}</p>
      </div>
    </div>
  )
}

function MigrationStep({ n, title, children }) {
  return (
    <li className="flex gap-4">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent-light">
        {n}
      </span>
      <div>
        <h4 className="font-display text-base font-semibold text-white">{title}</h4>
        <p className="mt-1 text-sm text-slate-400 leading-relaxed">{children}</p>
      </div>
    </li>
  )
}
