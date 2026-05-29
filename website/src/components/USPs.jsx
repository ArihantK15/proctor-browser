import { ShieldCheck, FileCheck2, Server, Users, Languages, Lock } from 'lucide-react'

/**
 * "Why Procta" outcomes section — 6 cards from the Claude design's
 * marketing.html, reframing capability bullets ("we have AI gaze
 * tracking") into operational outcomes that are visible in the product.
 *
 * Each card has a proof footer using monospaced text — the design's
 * pattern for surfacing measurement / specifics that build credibility
 * without leaning on logo-walls or fake testimonials.
 */
export default function USPs() {
  const usps = [
    {
      icon: ShieldCheck,
      title: 'Every suspicious signal is reviewable',
      desc: 'AI monitors gaze, head pose, face count, and audio in real time. False positives are flagged for human review — a machine never terminates a student session.',
      proof: 'Timeline + confidence + reviewer decision',
    },
    {
      icon: FileCheck2,
      title: 'Scorecards ready before students leave',
      desc: 'Automated grading runs the moment a student submits. Export a complete PDF scorecard per student, or a bulk CSV for your SIS, in one click.',
      proof: 'PDF scorecard + streaming CSV exports',
    },
    {
      icon: Server,
      title: 'Zero IT involvement for students',
      desc: "Students download a 12 MB desktop app — no VPN, no browser extension, no custom firewall rules. Runs on a ₹30,000 Lenovo IdeaPad as well as a MacBook Pro.",
      proof: 'Windows and macOS builds',
    },
    {
      icon: Users,
      title: 'Live monitoring from any device',
      desc: 'Teachers see every active session in a single real-time table. Severity, calibration, risk score, and camera feed — all without leaving one screen.',
      proof: 'Live sessions + queue/worker monitoring',
    },
    {
      icon: Languages,
      title: 'Hindi UI coming Q3 2026',
      desc: 'Devanagari support is baked into the type system. Question content in regional languages is supported today; full UI localisation ships this year.',
      proof: 'IBM Plex Sans Devanagari — no substitution fonts',
    },
    {
      icon: Lock,
      title: 'Privacy-first by design',
      desc: "Camera frames are processed locally on the student's machine. No raw video is stored on our servers. Violation snapshots are encrypted, institution-owned.",
      proof: 'DPA, retention summary, privacy workflows',
    },
  ]

  return (
    <section id="differentiators" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <div
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">Why Procta</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Built for the people who run exams,<br />
            not the people who built the software
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            No 3-day IT project. No PhD in proctoring software. Set up an exam, send a link,
            watch a live dashboard. That's it.
          </p>
        </div>

        <div className="mt-16 grid gap-4 md:grid-cols-3">
          {usps.map((item) => (
            <div
              key={item.title}
              className="relative flex flex-col gap-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 transition-colors hover:border-accent/20"
            >
              <div className="inline-flex w-fit rounded-lg border border-accent/20 bg-accent/5 p-2.5 accent-glow">
                <item.icon size={18} className="text-accent-light" />
              </div>
              <h3 className="text-lg font-semibold leading-tight text-white">{item.title}</h3>
              <p className="flex-1 text-sm leading-relaxed text-slate-400">{item.desc}</p>
              <div className="font-mono text-xs text-accent">{item.proof}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
