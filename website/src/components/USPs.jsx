import { motion } from 'framer-motion'
import { FileCheck2, Languages, Lock, Server, ShieldCheck, Users } from 'lucide-react'

const reveal = {
  initial: { opacity: 0, y: 22 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: '-70px' },
  transition: { duration: 0.5, ease: 'easeOut' },
}

const usps = [
  {
    icon: ShieldCheck,
    title: 'Every suspicious signal is reviewable',
    desc: 'AI monitors gaze, head pose, face count, and audio in real time. False positives are flagged for human review — a machine never terminates a student session.',
    proof: 'Timeline + confidence + reviewer decision',
    accent: 'blue',
    stat: 'Human review',
  },
  {
    icon: FileCheck2,
    title: 'Scorecards ready before students leave',
    desc: 'Automated grading runs the moment a student submits. Export a complete PDF scorecard per student, or a bulk CSV for your SIS, in one click.',
    proof: 'PDF scorecard + streaming CSV exports',
    accent: 'green',
    stat: 'Instant closeout',
  },
  {
    icon: Server,
    title: 'Zero IT involvement for students',
    desc: 'Students download a 12 MB desktop app — no VPN, no browser extension, no custom firewall rules. Runs on a ₹30,000 Lenovo IdeaPad as well as a MacBook Pro.',
    proof: 'Windows and macOS builds',
    accent: 'amber',
    stat: '12 MB app',
  },
  {
    icon: Users,
    title: 'Live monitoring from any device',
    desc: 'Teachers see every active session in a single real-time table. Severity, calibration, risk score, and camera feed — all without leaving one screen.',
    proof: 'Live sessions + queue/worker monitoring',
    accent: 'violet',
    stat: 'One dashboard',
  },
  {
    icon: Languages,
    title: 'Hindi UI coming Q3 2026',
    desc: 'Devanagari support is baked into the type system. Question content in regional languages is supported today; full UI localisation ships this year.',
    proof: 'IBM Plex Sans Devanagari — no substitution fonts',
    accent: 'cyan',
    stat: 'Regional-ready',
  },
  {
    icon: Lock,
    title: 'Privacy-first by design',
    desc: "Camera frames are processed locally on the student's machine. No raw video is stored on our servers. Violation snapshots are encrypted, institution-owned.",
    proof: 'DPA, retention summary, privacy workflows',
    accent: 'red',
    stat: 'No raw video',
  },
]

function accentClasses(accent) {
  const map = {
    blue: 'border-accent/30 bg-accent/10 text-accent-light',
    green: 'border-emerald/30 bg-emerald/10 text-emerald',
    amber: 'border-amber/30 bg-amber/10 text-amber',
    violet: 'border-violet-400/30 bg-violet-400/10 text-violet-300',
    cyan: 'border-cyan-400/30 bg-cyan-400/10 text-cyan-300',
    red: 'border-red-400/30 bg-red-400/10 text-red-300',
  }
  return map[accent] || map.blue
}

export default function USPs() {
  return (
    <section id="differentiators" className="relative overflow-hidden py-24 md:py-32">
      <div className="pointer-events-none absolute inset-y-0 left-0 w-1/2 bg-accent/[0.025] blur-3xl" />
      <div className="relative mx-auto max-w-7xl px-6">
        <div className="grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:items-start">
          <motion.div {...reveal} className="lg:sticky lg:top-28">
            <span className="label-mono text-accent">Why Procta</span>
            <h2 className="mt-3 font-display text-3xl font-bold leading-tight text-white md:text-5xl">
              Built for the people who run exams, not the people who built the software.
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-slate-400">
              No 3-day IT project. No PhD in proctoring software. Set up an exam,
              send a link, watch a live dashboard, publish results.
            </p>
            <div className="mt-8 rounded-2xl border border-white/[0.06] bg-white/[0.025] p-5">
              <div className="label-mono text-slate-500">Buyer promise</div>
              <p className="mt-2 text-sm leading-relaxed text-slate-300">
                A faculty member can explain Procta in one sentence, an IT admin can deploy it without a project plan,
                and an exam cell can defend the evidence after the exam.
              </p>
            </div>
          </motion.div>

          <div className="space-y-4">
            {usps.map((item, index) => (
              <motion.article
                key={item.title}
                {...reveal}
                transition={{ ...reveal.transition, delay: Math.min(index * 0.04, 0.18) }}
                className="scroll-reveal-card grid gap-5 p-5 md:grid-cols-[auto_1fr_auto] md:items-center md:p-6"
              >
                <div className={`inline-flex h-14 w-14 items-center justify-center rounded-2xl border ${accentClasses(item.accent)}`}>
                  <item.icon size={22} />
                </div>
                <div>
                  <div className="mb-1 font-mono text-xs text-slate-500">{item.proof}</div>
                  <h3 className="text-xl font-semibold leading-tight text-white">{item.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-slate-400">{item.desc}</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-navy-950/60 px-4 py-3 md:min-w-32 md:text-right">
                  <div className="label-mono text-slate-500">Outcome</div>
                  <div className="mt-1 text-sm font-semibold text-slate-200">{item.stat}</div>
                </div>
              </motion.article>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
