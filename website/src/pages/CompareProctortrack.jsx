import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, X, IndianRupee, Smartphone, Lock, MessageSquare } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

export default function CompareProctortrack() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Proctortrack alternative — on-device ML at ₹80/student | Procta</title>
        <meta name="description" content="Universities switching from Proctortrack (Verificient) get zero raw-video storage, phone-cam monitoring, LTI 1.3, and transparent INR billing at ₹80/student from Procta." />
        <link rel="canonical" href="https://www.procta.net/compare/proctortrack-vs-procta" />
        <meta property="og:title" content="Proctortrack alternative — ₹80/student with on-device AI | Procta" />
        <meta property="og:description" content="No raw video uploaded. Phone-cam included. INR + GST invoicing. Migrate from Proctortrack in under a week." />
        <meta property="og:url" content="https://www.procta.net/compare/proctortrack-vs-procta" />
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
            <span className="label-mono text-accent">Proctortrack alternative</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              An Indian-built alternative to Proctortrack by Verificient
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              Proctortrack positions itself as the world's most advanced proctoring solution, offering four
              tiers from browser lockdown to live human proctoring with AI. Pricing is custom-quoted per
              institution. Procta offers an alternative designed for the Indian market: on-device ML that
              keeps raw video off the wire, transparent pricing at ₹80/student, INR + GST invoicing, and
              phone-camera room monitoring on every plan — no tiered upgrades required.
            </p>
          </div>

          <div className="mt-14 overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-900/60">
            <table className="w-full text-sm">
              <thead className="bg-navy-900/80 text-left">
                <tr>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Capability</th>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Proctortrack</th>
                  <th className="px-5 py-4 font-semibold text-accent text-xs uppercase tracking-wider">Procta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06] text-slate-300">
                <ComparisonRow label="Per-student price (proctored exam)" proctortrack="Custom pricing on request" procta="₹80" highlight />
                <ComparisonRow label="Billing currency" proctortrack="USD (custom contracts)" procta="INR + GST invoice" />
                <ComparisonRow label="Phone-camera room monitoring" proctortrack="Included (room scan feature)" procta="Included on every plan" />
                <ComparisonRow label="On-device ML (no frames leaving student PC)" proctortrack={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="AI face / gaze / object detection" proctortrack={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Live human proctoring" proctortrack="1:4 proctor ratio (XL tier)" procta="AI with teacher pop-in" />
                <ComparisonRow label="LTI 1.3 integration (Canvas / Moodle)" proctortrack={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="LLM-graded short answers" proctortrack="Not publicly disclosed" procta="Included on Growth and above" />
                <ComparisonRow label="Live teacher webcam view" proctortrack="Not publicly disclosed" procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Real-time chat with student during exam" proctortrack="Not publicly disclosed" procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Mobile app for test-takers" proctortrack="Flutter-based mobile app" procta="Mobile web + QR room camera" />
                <ComparisonRow label="Free trial without credit card" proctortrack="Demo request only" procta="14-day trial on /signup" />
                <ComparisonRow label="Time to first proctored exam" proctortrack="Onboarding process varies" procta="10 minutes after signup" />
                <ComparisonRow label="Data residency (India / DPDP Act ready)" proctortrack="Multi-region" procta="Mumbai-first, DPDP-aligned" />
                <ComparisonRow label="Self-hosted option" proctortrack="Not publicly disclosed" procta="Available on Pro / Enterprise" />
                <ComparisonRow label="UPI Autopay subscriptions" proctortrack={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
              </tbody>
            </table>
          </div>

          <p className="mt-4 text-xs text-slate-500 leading-relaxed">
            Proctortrack pricing is not publicly listed — every plan requires contacting sales for a
            custom quote. Procta's ₹80/student is the published pay-as-you-go overage rate;
            volume plans (Starter ₹2,400/mo for 30 students) work out cheaper still.
          </p>

          <div className="mt-16 space-y-10">
            <div>
              <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Why Indian institutions choose Procta over Proctortrack</h2>
              <p className="mt-3 text-slate-400 leading-relaxed">
                Proctortrack by Verificient offers four tiers of proctoring — from browser lockdown to
                full live proctoring — each priced via custom contract. For an Indian university running
                semester exams, the cost and complexity of their multi-tier model can be disproportionate.
                Procta was built specifically for this market:
              </p>
            </div>

            <FeatureBlock
              icon={<IndianRupee size={20} />}
              title="Transparent pricing vs custom-quote model"
              body="Proctortrack's website quotes no prices — each institution must schedule a consultation to get a quote. Procta publishes ₹80/student on every page. At 5,000 student-exams per month that is ₹4,00,000 — predictable, budgetable, and self-serve."
            />

            <FeatureBlock
              icon={<Lock size={20} />}
              title="No raw video uploads to any cloud"
              body="Proctortrack records video, audio, and desktop screenshots during exams for later review. Procta runs all ML inference on-device — face detection, gaze tracking, object detection. Only structured violation events are transmitted. For DPDP Act compliance, this is a meaningful architectural difference."
            />

            <FeatureBlock
              icon={<Smartphone size={20} />}
              title="Phone-cam is standard, not a tier upgrade"
              body="Proctortrack's room-scan feature is available across their product line, but the pricing model means you pay for the tier that includes it. Procta includes phone-camera room monitoring on every paid plan — no upgrade path required."
            />

            <FeatureBlock
              icon={<MessageSquare size={20} />}
              title="India-first communication channels"
              body="WhatsApp invite delivery and scorecard distribution. Real-time in-exam chat with invigilators. Proctortrack's workflow is primarily email and browser-based. Indian students have higher engagement on messaging platforms."
            />
          </div>

          <div className="mt-16">
            <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Migrating from Proctortrack to Procta</h2>
            <p className="mt-3 text-slate-400 leading-relaxed">
              Export your student roster and exam templates from Proctortrack's admin panel. Procta's
              bulk-import tool accepts standard CSV formats with automatic roll-format detection
              (CBSE, JEE, NTA). Re-create your exam templates and reissue invite links. Most
              universities and coaching institutes with fewer than 5,000 students complete the
              migration in under one week.
            </p>
            <ol className="mt-6 space-y-4 text-slate-300">
              <MigrationStep n="1" title="Export your data from Proctortrack">
                Proctortrack's admin console allows CSV export of student rosters and session data.
                Export your most recent batch — Procta imports email, full name, and roll number.
              </MigrationStep>
              <MigrationStep n="2" title="Sign up for Procta (no card)">
                <Link to="/signup" className="text-accent-light hover:text-accent">procta.net/signup</Link>. 14-day Starter trial.
                Your admin dashboard is ready in 90 seconds.
              </MigrationStep>
              <MigrationStep n="3" title="Bulk-import your roster">
                Members → Import → drop the CSV. Duplicate detection and format recognition run automatically.
              </MigrationStep>
              <MigrationStep n="4" title="Re-create your top exam template">
                Most institutions have 3-10 recurring exam formats. Recreate the most-used one in Procta.
                Import your questions from a PDF or Word paper, generate them from your notes, or paste
                them in — then reuse them from the question bank across exams.
              </MigrationStep>
              <MigrationStep n="5" title="Run a parallel proctored sitting">
                Schedule the same exam on both platforms with a small group of students. Compare the
                violation feeds side by side. Most institutions make the switch within 48 hours of that test.
              </MigrationStep>
            </ol>
          </div>

          <div className="mt-16 rounded-2xl border border-accent/30 bg-gradient-to-br from-accent/[0.08] to-transparent p-8 md:p-10">
            <h2 className="font-display text-2xl font-bold text-white">Ready to compare side by side?</h2>
            <p className="mt-3 text-slate-300 leading-relaxed">
              Start a free 14-day trial. No credit card. No sales-call gate. Or email us a request to run
              the parallel sitting against your current Proctortrack contract and we will help wire it up.
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/signup"
                className="inline-flex justify-center rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
              >
                Start free trial
              </Link>
              <a
                href="mailto:arihantkaul@outlook.com?subject=Proctortrack%20comparison%20enquiry"
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

function ComparisonRow({ label, proctortrack, procta, highlight = false }) {
  return (
    <tr className={highlight ? 'bg-accent/[0.04]' : undefined}>
      <td className="px-5 py-3.5 text-slate-300">{label}</td>
      <td className="px-5 py-3.5 text-slate-400">{proctortrack}</td>
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
