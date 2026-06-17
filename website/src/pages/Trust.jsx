import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, CheckCircle2, ShieldCheck } from 'lucide-react'
import { motion, useReducedMotion } from 'framer-motion'
import { fadeUp, stagger, inViewProps, pick } from '../lib/motion'
import Footer from '../components/Footer'

const sections = [
  {
    title: 'Security Controls',
    items: [
      'TLS for all production traffic',
      'Supabase/Postgres encryption at rest',
      'HttpOnly cookie authentication with server-side CSRF tokens for browser mutations',
      'Rate limits on auth, exam, admin, and API routes',
      'Email-OTP 2FA, email verification, suspicious-login alerts, and re-auth gates for destructive actions',
      'Structured JSON logs, request IDs, audit events, and CI security scanning',
    ],
  },
  {
    title: 'Privacy & Retention',
    items: [
      'Zero raw-video storage by default — camera frames are analysed on the student device and discarded; only JPEG snapshots of flagged moments are uploaded as evidence',
      'Zero raw-audio storage — the on-device speech-to-text and multi-voice detection pipelines run client-side; only event metadata (matched keyword, confidence score) and a synchronously-captured camera JPEG ever leave the device',
      'Room-camera frames are short-lived operational evidence with retention controls (SCREENSHOT_RETENTION_DAYS, default 90)',
      'Student account privacy export and deletion flows are available for Procta-managed accounts',
      'LTI learners are identity-managed by the LMS; LMS privacy workflows remain the source of truth',
      'Institutions control exam data retention requirements',
      'Risk scores and AI grading are review aids; teachers make final decisions',
    ],
  },
  {
    title: 'Operational Readiness',
    items: [
      'Docker deployment with Caddy reverse proxy and automatic HTTPS',
      'Health checks for API, Redis, worker heartbeat, disk, memory, storage writes, and queues',
      'Background jobs for scorecard and email workflows',
      'Deploy runbook with migration, rollback, smoke-test, and backup steps',
      'CI checks for tests, builds, dependency audits, secret scanning, SAST, and filesystem CVEs',
      '1,500-student clean load test and 3,500-student architecture target documented for capacity planning',
    ],
  },
  {
    title: 'Subprocessors',
    items: [
      'Postgres/Supabase-compatible database infrastructure',
      'Hostinger KVM for application hosting',
      'Cloudflare Turnstile for bot protection when configured',
      'Razorpay for subscriptions and payment processing',
      'Email provider for transactional delivery',
      'LMS platforms for LTI-managed learner identity and launch flows',
    ],
  },
]

export default function Trust() {
  const reduced = useReducedMotion()
  const childVar = pick(reduced, fadeUp)
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Trust Center — Procta</title>
        <meta name="description" content="Security, privacy, subprocessors, retention, and operational controls for Procta AI exam proctoring." />
        <link rel="canonical" href="https://www.procta.net/trust" />
        <meta property="og:title" content="Trust Center — Procta" />
        <meta property="og:description" content="Security, privacy, subprocessors, retention, and operational controls for Procta AI exam proctoring." />
        <meta property="og:url" content="https://www.procta.net/trust" />
        <meta property="og:type" content="website" />
        <meta property="og:image" content="https://www.procta.net/og-image.png" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>

      <main className="mx-auto max-w-5xl px-6 pt-24 pb-16">
        <Link to="/" className="mb-8 inline-flex items-center gap-2 text-sm text-slate-500 transition-colors hover:text-white no-underline">
          <ArrowLeft size={16} />
          Back to home
        </Link>

        <motion.div className="max-w-3xl" variants={childVar} {...inViewProps}>
          <span className="label-mono text-accent">Trust Center</span>
          <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-5xl">
            Security and privacy controls for institutional exams.
          </h1>
          <p className="mt-5 text-base leading-relaxed text-slate-400 md:text-lg">
            Procta is built for high-stakes exam operations: authenticated access,
            auditable proctoring evidence, controlled retention, and deployment
            checks designed to keep live exams stable.
          </p>
        </motion.div>

        <motion.div className="mt-12 grid gap-4 md:grid-cols-3" variants={childVar} {...inViewProps}>
          <Proof label="Production Controls" value="CI + Docker + health checks" />
          <Proof label="Authentication" value="HttpOnly + CSRF + 2FA" />
          <Proof label="Scale Planning" value="3,500-student target" />
        </motion.div>

        <motion.div className="mt-12 grid gap-6 md:grid-cols-2" variants={stagger(0.08)} {...inViewProps}>
          {sections.map(section => (
            <motion.section key={section.title} variants={childVar} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
              <div className="mb-4 flex items-center gap-3">
                <ShieldCheck size={18} className="text-accent-light" />
                <h2 className="text-lg font-semibold text-white">{section.title}</h2>
              </div>
              <ul className="space-y-3">
                {section.items.map(item => (
                  <li key={item} className="flex gap-3 text-sm leading-relaxed text-slate-400">
                    <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-accent" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </motion.section>
          ))}
        </motion.div>

        <motion.section className="mt-12 rounded-xl border border-accent/20 bg-accent/[0.04] p-6" variants={childVar} {...inViewProps}>
          <h2 className="text-lg font-semibold text-white">Institution Review Packet</h2>
          <p className="mt-3 text-sm leading-relaxed text-slate-400">
            For procurement or IT review, request Procta's DPA, incident response
            summary, retention configuration, security questionnaire, and sample
            scorecard packet from{' '}
            <a href="mailto:security@procta.net" className="text-accent-light hover:text-white no-underline">
              security@procta.net
            </a>.
          </p>
        </motion.section>
      </main>

      <Footer />
    </div>
  )
}

function Proof({ label, value }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
      <div className="label-mono text-slate-500">{label}</div>
      <div className="mt-2 text-lg font-semibold text-white">{value}</div>
    </div>
  )
}
