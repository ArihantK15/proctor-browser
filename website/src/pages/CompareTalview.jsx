import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, X, IndianRupee, Smartphone, Lock, MessageSquare } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

export default function CompareTalview() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Talview alternative — ₹80/student with zero raw video storage | Procta</title>
        <meta name="description" content="Indian coaching institutes switching from Talview Proview get AI proctoring, phone-cam room monitoring, on-device ML (no cloud upload), LTI 1.3, and INR billing at ₹80/student." />
        <link rel="canonical" href="https://www.procta.net/compare/talview-vs-procta" />
        <meta property="og:title" content="Talview Proview alternative — ₹80/student with on-device ML | Procta" />
        <meta property="og:description" content="No raw video uploaded to the cloud. Phone-cam included. INR + GST invoicing. Migrate from Talview in under a week." />
        <meta property="og:url" content="https://www.procta.net/compare/talview-vs-procta" />
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
            <span className="label-mono text-accent">Talview alternative</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              Looking for an Indian-built alternative to Talview Proview?
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              Talview positions itself as an agentic AI platform for secure proctoring and interviewing, trusted by
              enterprise clients across 120+ countries. Their pricing is contact-only and starts at enterprise
              tiers. Procta takes a different approach: on-device machine learning that keeps raw video on the
              student's machine, transparent pricing at ₹80/student, INR + GST invoicing, and phone-camera room
              monitoring included on every plan — not locked behind an enterprise upsell.
            </p>
          </div>

          <div className="mt-14 overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-900/60">
            <table className="w-full text-sm">
              <thead className="bg-navy-900/80 text-left">
                <tr>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Capability</th>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Talview</th>
                  <th className="px-5 py-4 font-semibold text-accent text-xs uppercase tracking-wider">Procta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06] text-slate-300">
                <ComparisonRow label="Per-student price (proctored exam)" talview="Contact sales only" procta="₹80" highlight />
                <ComparisonRow label="Billing currency" talview="USD (enterprise)" procta="INR + GST invoice" />
                <ComparisonRow label="Phone-camera room monitoring" talview="Included (dual-camera)" procta="Included on every plan" />
                <ComparisonRow label="On-device ML (no frames leaving student PC)" talview={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="AI face / gaze / object detection" talview={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Agentic AI proctoring agent" talview="Alvy AI Agent" procta="Behavioural pattern engine" />
                <ComparisonRow label="LTI 1.3 integration (Canvas / Moodle)" talview={<Check className="text-emerald-400" size={18} />} procta={<span className="text-amber-400 text-sm font-semibold">Beta</span>} />
                <ComparisonRow label="LLM-graded short answers" talview="Not publicly disclosed" procta="Included on Growth and above" />
                <ComparisonRow label="Live teacher webcam view" talview={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Real-time chat with student during exam" talview="Not publicly disclosed" procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Free trial without credit card" talview="Demo request only" procta="14-day trial on /signup" />
                <ComparisonRow label="Time to first proctored exam" talview="Enterprise onboarding (weeks)" procta="10 minutes after signup" />
                <ComparisonRow label="Data residency (India / DPDP Act ready)" talview="Multi-region" procta="Mumbai-first, DPDP-aligned" />
                <ComparisonRow label="Self-hosted option" talview="Enterprise contract only" procta="Available on Pro / Enterprise" />
                <ComparisonRow label="UPI Autopay subscriptions" talview={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
              </tbody>
            </table>
          </div>

          <p className="mt-4 text-xs text-slate-500 leading-relaxed">
            Talview pricing is not publicly listed — typical enterprise contracts start at approximately
            $25,000/year based on publicly available third-party estimates. Procta's ₹80/student is the
            published pay-as-you-go overage rate; volume plans work out cheaper still.
          </p>

          <div className="mt-16 space-y-10">
            <div>
              <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Why Indian institutes choose Procta over Talview</h2>
              <p className="mt-3 text-slate-400 leading-relaxed">
                Talview built a global enterprise product with agentic AI features and a US-dollar pricing
                model. For Indian coaching institutes and universities running high-stakes exams every
                semester, the cost and complexity are often disproportionate to the need. Procta was
                designed specifically for the Indian assessment market:
              </p>
            </div>

            <FeatureBlock
              icon={<IndianRupee size={20} />}
              title="Published pricing — no sales call required"
              body="Talview's website lists no prices; every purchase starts with a demo request and enterprise negotiation. Procta publishes ₹80/student up front. At 2,000 student-exams per month that is ₹1,60,000 — predictable and self-serve."
            />

            <FeatureBlock
              icon={<Lock size={20} />}
              title="Zero raw video leaves the student's machine"
              body="Talview streams proctoring video to their cloud for AI analysis and human review. Procta runs face detection, gaze tracking, and object detection entirely on-device. Only structured violation events — no raw frames — leave the student PC. This matters for DPDP Act compliance and parent consent."
            />

            <FeatureBlock
              icon={<Smartphone size={20} />}
              title="Phone-cam included, not a premium add-on"
              body="Talview offers dual-camera proctoring across their product line. Procta ships phone-camera room monitoring on every paid plan. Students scan a QR code from their invite email and their phone becomes the room camera — no MDM, no separate app install."
            />

            <FeatureBlock
              icon={<MessageSquare size={20} />}
              title="Built for how Indian students actually communicate"
              body="WhatsApp invite links, WhatsApp scorecard delivery, and real-time in-exam chat with the invigilating teacher. Talview's workflow is email-first, which has lower engagement rates in Indian edtech."
            />
          </div>

          <div className="mt-16">
            <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Migrating from Talview to Procta</h2>
            <p className="mt-3 text-slate-400 leading-relaxed">
              Export your question bank and student roster from Talview as CSV. Import into Procta using
              our bulk-import tool with roll-format auto-detection (CBSE, JEE, NTA patterns recognised
              automatically). Reissue invite emails — or WhatsApp invites when available. Most institutes
              with fewer than 5,000 students complete the migration in under one week.
            </p>
            <ol className="mt-6 space-y-4 text-slate-300">
              <MigrationStep n="1" title="Export student data from Talview">
                Talview's admin panel supports CSV export of student rosters and exam results. Export
                your most recent batch — Procta's import expects email, full name, and roll number.
              </MigrationStep>
              <MigrationStep n="2" title="Sign up for Procta (no card)">
                <Link to="/signup" className="text-accent-light hover:text-accent">procta.net/signup</Link>. 14-day Starter trial.
                You will have a working admin dashboard in 90 seconds.
              </MigrationStep>
              <MigrationStep n="3" title="Bulk-import your roster">
                Members → Import → drop the CSV. Duplicate detection runs automatically.
              </MigrationStep>
              <MigrationStep n="4" title="Re-create your top exam template">
                Most institutes run 3-10 recurring exam formats. Recreate your most-used one in Procta
                as a smoke test. Import your questions from a PDF or Word paper, generate them from your
                notes, or paste them in — then reuse them from the question bank across exams.
              </MigrationStep>
              <MigrationStep n="5" title="Run a parallel proctored sitting">
                Schedule the same exam on both platforms with 5 trusted students. Compare the flagged
                violation feeds side by side. Most institutes switch within 48 hours of that test.
              </MigrationStep>
            </ol>
          </div>

          <div className="mt-16 rounded-2xl border border-accent/30 bg-gradient-to-br from-accent/[0.08] to-transparent p-8 md:p-10">
            <h2 className="font-display text-2xl font-bold text-white">Ready to compare side by side?</h2>
            <p className="mt-3 text-slate-300 leading-relaxed">
              Start a free 14-day trial. No credit card. No sales-call gate. Or email us a request to run
              the parallel sitting against your current Talview contract and we will help wire it up.
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/signup"
                className="inline-flex justify-center rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
              >
                Start free trial
              </Link>
              <a
                href="mailto:support@procta.net?subject=Talview%20comparison%20enquiry"
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

function ComparisonRow({ label, talview, procta, highlight = false }) {
  return (
    <tr className={highlight ? 'bg-accent/[0.04]' : undefined}>
      <td className="px-5 py-3.5 text-slate-300">{label}</td>
      <td className="px-5 py-3.5 text-slate-400">{talview}</td>
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
