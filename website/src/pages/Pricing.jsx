import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { Check, ArrowRight } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'
import { APP_URL } from '../config'

// IMPORTANT — these tiers MUST stay in sync with the server-side
// source of truth at app/constants.py:PLANS. If you bump a price
// here, bump it there too (and vice versa). The /api/v1/billing/usage
// endpoint reads PLANS at request time, so a drift would surface as
// "your plan says 30 students but the page advertises 50" UX bugs.

const plans = [
  {
    id: 'starter',
    name: 'Starter',
    price: '₹2,400',
    period: '/mo',
    students: 30,
    desc: 'For small classes & tutorials',
    features: [
      'Up to 30 students',
      'AI proctoring (face, gaze, object detection)',
      'Real-time risk scoring (0-100)',
      'Auto-save & offline resilience',
      'PDF scorecards per student',
      'CSV export of results',
      'Email invites & access codes',
      'Email support',
    ],
    cta: 'Start Free Trial',
    href: '/signup',
    popular: false,
  },
  {
    id: 'growth',
    name: 'Growth',
    price: '₹12,000',
    period: '/mo',
    students: 150,
    desc: 'For departments & mid-size programs',
    features: [
      'Up to 150 students',
      'Everything in Starter, plus:',
      'Phone camera room monitoring',
      'AI short-answer grading',
      'LTI 1.3 integration (Canvas, Moodle)',
      'Student groups & scheduling',
      'Duplicate exams across batches',
      'Priority email support',
    ],
    cta: 'Start Free Trial',
    href: '/signup',
    popular: true,
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '₹30,000',
    period: '/mo',
    students: 500,
    desc: 'For large universities & institutions',
    features: [
      'Up to 500 students',
      'Everything in Growth, plus:',
      'Live camera view (teacher peek)',
      'Real-time chat with students',
      'Bulk exam scheduling',
      'Advanced analytics & violation breakdown',
      'Self-hosted option available',
      'Phone & email support',
    ],
    cta: 'Start Free Trial',
    href: '/signup?plan=pro',
    popular: false,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: 'Custom',
    period: '',
    students: '∞',
    desc: 'For exam boards, govt bodies, large coaching networks',
    features: [
      'Unlimited students',
      'Everything in Pro, plus:',
      'Dedicated infrastructure',
      'SLA + 24×7 support',
      'On-prem / private cloud deployment',
      'Custom integrations (LMS, ERP, ATS)',
      'Aadhaar e-KYC + DPDP Act compliance package',
      'Onboarding + training included',
    ],
    cta: 'Contact Sales',
    href: 'mailto:arihantkaul@outlook.com?subject=Procta%20Enterprise%20enquiry',
    popular: false,
  },
]

const faqs = [
  {
    q: 'Is there a free trial?',
    a: 'Yes — every new account gets a 7-day free trial on the Starter plan with full access to all features. No credit card required.',
  },
  {
    q: 'Can I switch plans mid-month?',
    a: 'Yes. Upgrades take effect immediately. The difference is prorated to your current billing period.',
  },
  {
    q: 'What happens when I exceed my student limit?',
    a: 'You cannot register new students beyond your plan limit. The dashboard will show an upgrade prompt. Existing students and exams are unaffected.',
  },
  {
    q: 'Do you offer educational discounts?',
    a: 'Yes — we offer discounted pricing for qualifying institutions. Contact our sales team for a custom quote.',
  },
  {
    q: 'Can I self-host Procta?',
    a: 'Yes. The Pro plan supports self-hosting on your own infrastructure. We provide Docker images and deployment guides.',
  },
  {
    q: 'Is there a setup fee?',
    a: 'No. All plans include free setup and onboarding support. Most institutions are running their first exam within a day.',
  },
]

export default function Pricing() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Pricing — Procta AI Exam Proctoring</title>
        <meta name="description" content="Simple, transparent pricing for AI-powered exam proctoring. Starter ₹2,400/mo, Growth ₹12,000/mo, Pro ₹30,000/mo. Free 7-day trial, no credit card required." />
        <link rel="canonical" href="https://procta.net/pricing" />
        <meta property="og:title" content="Pricing — Procta AI Exam Proctoring" />
        <meta property="og:description" content="Affordable AI proctoring for Indian higher education. Plans start at ₹2,400/month. Free 7-day trial." />
        <meta property="og:url" content="https://procta.net/pricing" />
      </Helmet>

      <Navbar />

      {/* Hero */}
      <section className="pt-36 pb-16 md:pt-44 md:pb-20">
        <div className="mx-auto max-w-7xl px-6 text-center">
          <span className="label-mono text-accent">Pricing</span>
          <h1 className="mt-3 font-display text-4xl font-bold text-white md:text-5xl">
            Simple pricing. No hidden fees.
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-lg text-slate-400">
            Start free, upgrade when you need more. All plans include full AI proctoring, real-time monitoring, and automated scorecards.
          </p>
        </div>
      </section>

      {/* Plan cards */}
      <section className="pb-16">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid gap-6 md:grid-cols-3 md:items-start">
            {plans.map(p => (
              <div
                key={p.id}
                className={`relative rounded-2xl border ${p.popular ? 'border-accent/40 bg-accent/[0.03]' : 'border-white/[0.08] bg-white/[0.02]'} p-8 flex flex-col`}
              >
                {p.popular && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-accent px-4 py-1 text-xs font-semibold text-white label-mono">
                    Most popular
                  </div>
                )}
                <div className="mb-6">
                  <h2 className="text-lg font-semibold text-white">{p.name}</h2>
                  <p className="mt-1 text-sm text-slate-400">{p.desc}</p>
                  <div className="mt-4 flex items-baseline gap-1">
                    <span className="font-display text-4xl font-bold text-white">{p.price}</span>
                    <span className="text-sm text-slate-500">{p.period}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">Up to {p.students} students</p>
                </div>

                <ul className="mb-8 flex-1 space-y-3">
                  {p.features.map((f, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-slate-300">
                      <Check size={16} className="mt-0.5 shrink-0 text-accent" />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>

                {p.href.startsWith('mailto:') ? (
                  <a
                    href={p.href}
                    className={`flex items-center justify-center gap-2 rounded-xl py-3.5 text-sm font-semibold no-underline transition-all ${
                      p.popular
                        ? 'bg-accent-dark text-white glow-btn hover:bg-accent'
                        : 'border border-white/10 bg-white/[0.03] text-slate-300 hover:border-accent/30'
                    }`}
                  >
                    {p.cta}
                    <ArrowRight size={16} />
                  </a>
                ) : (
                  <Link
                    to={p.href}
                    className={`flex items-center justify-center gap-2 rounded-xl py-3.5 text-sm font-semibold no-underline transition-all ${
                      p.popular
                        ? 'bg-accent-dark text-white glow-btn hover:bg-accent'
                        : 'border border-white/10 bg-white/[0.03] text-slate-300 hover:border-accent/30'
                    }`}
                  >
                    {p.cta}
                    <ArrowRight size={16} />
                  </Link>
                )}
              </div>
            ))}
          </div>

          {/* Enterprise note */}
          <div className="mt-12 text-center">
            <p className="text-sm text-slate-400">
              Need more than 500 students?{' '}
              <a href={`${APP_URL}/dashboard`} className="text-accent-light hover:text-accent no-underline font-medium">
                Contact us for enterprise pricing
              </a>
            </p>
          </div>
        </div>
      </section>

      {/* Feature comparison table */}
      <section className="py-16 bg-navy-900/30">
        <div className="mx-auto max-w-5xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            What's included
          </h2>
          <div className="table-scroll mt-10 rounded-2xl border border-white/[0.06]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] bg-white/[0.02]">
                  <th className="px-6 py-4 text-left text-sm font-medium text-slate-400">Feature</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-accent-light">Starter</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-accent-light">Growth</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-accent-light">Pro</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['AI proctoring (face, gaze, object detection)', '✓', '✓', '✓'],
                  ['Real-time risk scoring', '✓', '✓', '✓'],
                  ['Auto-save & offline resilience', '✓', '✓', '✓'],
                  ['PDF scorecards & CSV export', '✓', '✓', '✓'],
                  ['Phone camera room monitoring', '—', '✓', '✓'],
                  ['AI short-answer grading', '—', '✓', '✓'],
                  ['LTI 1.3 integration', '—', '✓', '✓'],
                  ['Live camera view', '—', '—', '✓'],
                  ['Student chat & broadcast', '—', '—', '✓'],
                  ['Self-hosted option', '—', '—', '✓'],
                  ['Priority support', 'Email', 'Email', 'Phone + Email'],
                ].map((row, i) => (
                  <tr key={i} className={`border-b border-white/[0.04] ${i % 2 === 0 ? 'bg-white/[0.01]' : ''}`}>
                    <td className="px-6 py-3.5 text-slate-300">{row[0]}</td>
                    <td className={`px-6 py-3.5 text-center ${row[1] === '✓' ? 'text-accent' : 'text-slate-600'}`}>{row[1]}</td>
                    <td className={`px-6 py-3.5 text-center ${row[2] === '✓' ? 'text-accent' : 'text-slate-600'}`}>{row[2]}</td>
                    <td className={`px-6 py-3.5 text-center ${row[3] === '✓' ? 'text-accent' : 'text-slate-600'}`}>{row[3]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* How Procta compares to Mettl + Talview — sales-objection killer for
          coaching-institute IT heads who are doing side-by-side evaluations. */}
      <section className="py-16">
        <div className="mx-auto max-w-5xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            How we compare
          </h2>
          <p className="mt-3 text-center text-slate-400 max-w-2xl mx-auto">
            We are a fraction of the price of incumbent Indian proctoring vendors.
            Same AI stack — face, gaze, object, room-camera — at a price coaching
            institutes can actually afford to ship every month.
          </p>
          <div className="table-scroll mt-10 rounded-2xl border border-white/[0.06] overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.02]">
                <tr className="border-b border-white/[0.06]">
                  <th className="px-6 py-4 text-left text-sm font-medium text-slate-400">Capability</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-accent-light">Procta</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-slate-400">Mercer Mettl</th>
                  <th className="px-6 py-4 text-center text-sm font-semibold text-slate-400">Talview</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['Per-student proctored exam', '₹80', '₹500–1,000', '₹400–800'],
                  ['Phone-cam room monitoring', '✓ included', 'Premium add-on', 'Premium add-on'],
                  ['On-device ML (no full-video upload)', '✓', '—', '—'],
                  ['LLM-graded short answers', '✓ (Growth+)', 'Add-on', '—'],
                  ['Live teacher webcam view', '✓', '✓', '✓'],
                  ['In-exam chat with student', '✓', '—', '—'],
                  ['INR + GST invoicing', '✓ (built-in)', 'Enterprise contracts', 'Enterprise contracts'],
                  ['UPI Autopay subscription', '✓', '—', '—'],
                  ['Data residency: Mumbai-first', '✓', 'Multi-region', 'Multi-region'],
                  ['Self-hosted option', '✓ (Pro / Enterprise)', 'Enterprise only', '—'],
                  ['Free trial without sales call', '✓ 14-day', '—', '—'],
                  ['Deploy in 10 minutes', '✓', '2–4 wk onboarding', '2–4 wk onboarding'],
                ].map((row, i) => (
                  <tr key={i} className={`border-b border-white/[0.04] ${i % 2 === 0 ? 'bg-white/[0.01]' : ''}`}>
                    <td className="px-6 py-3.5 text-slate-300">{row[0]}</td>
                    <td className="px-6 py-3.5 text-center text-accent-light font-medium">{row[1]}</td>
                    <td className="px-6 py-3.5 text-center text-slate-400">{row[2]}</td>
                    <td className="px-6 py-3.5 text-center text-slate-400">{row[3]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-slate-500 text-center max-w-2xl mx-auto">
            Competitor prices are 2025–2026 reported ranges from mid-size Indian institutes; actual
            contracts vary. <Link to="/migrate-from-mettl" className="text-accent-light hover:text-accent">Detailed Mettl comparison →</Link>
          </p>
        </div>
      </section>

      {/* FAQ */}
      <section className="py-16 md:py-20">
        <div className="mx-auto max-w-3xl px-6">
          <h2 className="font-display text-2xl font-bold text-white text-center md:text-3xl">
            Frequently asked questions
          </h2>
          <div className="mt-10 space-y-0 rounded-2xl border border-white/[0.06] divide-y divide-white/[0.06]">
            {faqs.map(item => (
              <details key={item.q} className="group">
                <summary className="flex cursor-pointer list-none items-center justify-between px-6 py-5 text-sm font-medium text-white transition-colors hover:text-accent-light">
                  {item.q}
                  <ArrowRight size={14} className="shrink-0 text-slate-500 transition-transform group-open:rotate-90" />
                </summary>
                <p className="px-6 pb-5 text-sm leading-relaxed text-slate-400">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 md:py-24">
        <div className="mx-auto max-w-2xl px-6 text-center">
          <h2 className="font-display text-3xl font-bold text-white md:text-4xl">
            Ready to get started?
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Free 7-day trial. No credit card. No commitment.
          </p>
          <Link
            to="/signup"
            className="mt-8 inline-flex items-center gap-2 rounded-xl bg-accent-dark px-8 py-4 text-base font-semibold text-white glow-btn no-underline transition-all hover:bg-accent"
          >
            Start Free Trial
            <ArrowRight size={18} />
          </Link>
        </div>
      </section>

      <Footer />
    </div>
  )
}
