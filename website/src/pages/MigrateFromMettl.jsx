import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, X, IndianRupee, Smartphone, Lock, MessageSquare } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

/**
 * /migrate-from-mettl
 *
 * Long-tail SEO target: every coaching-institute IT head who Googles
 * "Mettl alternative", "Mettl pricing too expensive", "Mercer Mettl
 * vs", or "cheaper than Mettl proctoring" should land here.
 *
 * Editorial tone — factual side-by-side, no smack-talk. Pricing
 * claims (~₹500-1000/student for Mettl, ₹80 for Procta) cited from
 * publicly available Mercer Mettl rate cards and Indian-edtech
 * industry reporting. Update the numbers if Mettl publishes new
 * rate cards.
 */
export default function MigrateFromMettl() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Switching from Mercer Mettl? Procta is ₹80/student with phone-cam included | Procta</title>
        <meta name="description" content="Coaching institutes paying ₹500-1,000 per student for Mercer Mettl proctoring. Procta delivers AI proctoring, phone-cam room monitoring, LTI 1.3, and INR billing at ₹80/student. Migrate in under a week." />
        <link rel="canonical" href="https://www.procta.net/migrate-from-mettl" />
        <meta property="og:title" content="Mercer Mettl alternative for Indian coaching institutes — ₹80/student | Procta" />
        <meta property="og:description" content="One-eighth the cost of Mercer Mettl. Phone-cam included. INR + GST invoicing. Deploy in 10 minutes." />
        <meta property="og:url" content="https://www.procta.net/migrate-from-mettl" />
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
            <span className="label-mono text-accent">Mercer Mettl alternative</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              Switching from Mercer Mettl to Procta in under a week
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              If your coaching institute, university, or assessment company is paying ₹500-1,000 per
              student to Mercer Mettl, you are paying enterprise-tier pricing for technology that has been
              commoditised. Procta delivers the same AI proctoring stack — face detection, gaze tracking,
              object detection, phone-camera room monitoring, LTI 1.3 — at <strong className="text-white">₹80/student</strong>.
              Customers run the same exam-day workflow, in INR with GST invoicing, with a 10-minute deploy.
            </p>
          </div>

          {/* Comparison table — main attraction. */}
          <div className="mt-14 overflow-hidden rounded-2xl border border-white/[0.08] bg-navy-900/60">
            <table className="w-full text-sm">
              <thead className="bg-navy-900/80 text-left">
                <tr>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Capability</th>
                  <th className="px-5 py-4 font-semibold text-slate-300 text-xs uppercase tracking-wider">Mercer Mettl</th>
                  <th className="px-5 py-4 font-semibold text-accent text-xs uppercase tracking-wider">Procta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06] text-slate-300">
                <ComparisonRow label="Per-student price (proctored exam)" mettl="₹500-1,000" procta="₹80" highlight />
                <ComparisonRow label="Billing currency" mettl="USD invoicing common" procta="INR + GST invoice" />
                <ComparisonRow label="Phone-camera room monitoring" mettl="Premium add-on" procta="Included on every plan" />
                <ComparisonRow label="AI face / gaze / object detection" mettl={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="On-device ML (no frames leaving student PC)" mettl={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="LTI 1.3 integration (Canvas / Moodle)" mettl={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="LLM-graded short answers" mettl="Add-on" procta="Included on Growth and above" />
                <ComparisonRow label="Live teacher webcam view" mettl={<Check className="text-emerald-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Real-time chat with student during exam" mettl={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
                <ComparisonRow label="Free trial without credit card" mettl="Sales-call gated" procta="14-day trial on /signup" />
                <ComparisonRow label="Time to first proctored exam" mettl="2-4 weeks of onboarding" procta="10 minutes after signup" />
                <ComparisonRow label="Data residency (India / DPDP Act ready)" mettl="Multi-region" procta="Mumbai-first, DPDP-aligned" />
                <ComparisonRow label="Self-hosted option" mettl="Enterprise contract only" procta="Available on Pro / Enterprise" />
                <ComparisonRow label="UPI Autopay subscriptions" mettl={<X className="text-rose-400" size={18} />} procta={<Check className="text-emerald-400" size={18} />} />
              </tbody>
            </table>
          </div>

          <p className="mt-4 text-xs text-slate-500 leading-relaxed">
            Pricing references: Mettl rate-card numbers vary by enterprise contract size; the
            ₹500-1,000/student range is what mid-size Indian coaching institutes report paying
            (2025-2026). Procta's ₹80/student is the published pay-as-you-go overage rate;
            volume plans (Starter ₹2,400/mo for 30 students) work out cheaper still.
          </p>

          {/* Why-switch reasons */}
          <div className="mt-16 space-y-10">
            <div>
              <h2 className="font-display text-2xl font-bold text-white md:text-3xl">Why Indian institutes are switching</h2>
              <p className="mt-3 text-slate-400 leading-relaxed">
                Mercer Mettl was built for a global enterprise market. Their pricing reflects their cost
                of sales — long discovery calls, US-based account managers, multi-region infrastructure
                you do not need if every one of your students is in India. We built Procta to land in
                between "Google Forms with a webcam" (free but useless for high-stakes exams) and
                "enterprise contract that needs board approval" (Mettl). Specifically:
              </p>
            </div>

            <FeatureBlock
              icon={<IndianRupee size={20} />}
              title="One-eighth the price"
              body="At 1,000 student-exams per month your Procta bill is ₹80,000. Mettl, on a conservative ₹500/student midpoint, is ₹500,000. The savings cover salaries of two part-time content creators or three months of paid advertising."
            />

            <FeatureBlock
              icon={<Smartphone size={20} />}
              title="Phone-cam is included, not an upsell"
              body="Mettl bundles secondary-camera room monitoring into their highest tier. Procta ships it on every paid plan. Students scan a QR code from your invite email and their phone becomes the room camera — no separate app, no MDM, no hardware procurement."
            />

            <FeatureBlock
              icon={<Lock size={20} />}
              title="On-device ML keeps frames off the wire"
              body="Procta runs face detection, gaze tracking, and head-pose estimation locally on the student's machine. Only the violation events plus optional low-rate JPEG snapshots reach our servers. Mettl streams full video to their cloud. For DPDP Act compliance and parent objections, this matters."
            />

            <FeatureBlock
              icon={<MessageSquare size={20} />}
              title="Procta talks to students the way they actually communicate"
              body="Invite link via WhatsApp (rolling out). Scorecard PDF on WhatsApp (rolling out). Real-time chat with the invigilating teacher during the exam itself. Mettl emails. Indian students do not check email."
            />
          </div>

          {/* Migration playbook */}
          <div className="mt-16">
            <h2 className="font-display text-2xl font-bold text-white md:text-3xl">The 5-step migration</h2>
            <p className="mt-3 text-slate-400 leading-relaxed">
              No PoC. No three-month evaluation. Most institutes go from Mettl to Procta in under a week.
            </p>
            <ol className="mt-6 space-y-4 text-slate-300">
              <MigrationStep n="1" title="Export your student roster from Mettl">
                Settings → User Management → Export CSV. Procta accepts the same CSV format
                (email, full name, roll number).
              </MigrationStep>
              <MigrationStep n="2" title="Sign up for Procta (no card)">
                <Link to="/signup" className="text-accent-light hover:text-accent">procta.net/signup</Link>. 14-day Starter trial.
                You will have a working admin dashboard in 90 seconds.
              </MigrationStep>
              <MigrationStep n="3" title="Bulk-import your roster">
                Members → Import → drop the CSV. We auto-deduplicate against any existing roster.
              </MigrationStep>
              <MigrationStep n="4" title="Re-create your most-used exam">
                Most coaching institutes have 3-10 recurring exam templates. Re-create the top one
                in Procta as a smoke-test. Import your existing question papers straight from PDF or
                Word — extraction handles JEE/NEET-style numbered papers with answer keys, and preserves
                math and diagrams as images — or generate fresh questions from your notes. Everything
                lands in a reusable question bank.
              </MigrationStep>
              <MigrationStep n="5" title="Run a 5-student parallel proctored sitting">
                Pick 5 students you trust. Schedule the same exam on Mettl and Procta on the
                same day. Compare the flagged-violation feed side by side. Most institutes
                make the switch within 48 hours of that test.
              </MigrationStep>
            </ol>
          </div>

          {/* CTA */}
          <div className="mt-16 rounded-2xl border border-accent/30 bg-gradient-to-br from-accent/[0.08] to-transparent p-8 md:p-10">
            <h2 className="font-display text-2xl font-bold text-white">Ready to compare side by side?</h2>
            <p className="mt-3 text-slate-300 leading-relaxed">
              Start a free 14-day trial. No credit card. No sales-call gate. Or email us a request to run
              the parallel sitting against your current Mettl contract and we will help wire it up.
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/signup"
                className="inline-flex justify-center rounded-xl bg-accent-dark px-7 py-3.5 text-sm font-semibold text-white glow-btn no-underline"
              >
                Start free trial
              </Link>
              <a
                href="mailto:support@procta.net?subject=Mettl%20migration%20enquiry"
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

function ComparisonRow({ label, mettl, procta, highlight = false }) {
  return (
    <tr className={highlight ? 'bg-accent/[0.04]' : undefined}>
      <td className="px-5 py-3.5 text-slate-300">{label}</td>
      <td className="px-5 py-3.5 text-slate-400">{mettl}</td>
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
