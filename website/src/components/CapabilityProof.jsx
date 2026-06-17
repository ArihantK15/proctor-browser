import { motion } from 'framer-motion'
import CountUp from './CountUp'
import {
  Activity,
  BadgeCheck,
  Brain,
  Camera,
  Cpu,
  Database,
  FileSpreadsheet,
  GraduationCap,
  Landmark,
  Layers,
  MessageSquare,
  PauseOctagon,
  ReceiptIndianRupee,
  ServerCog,
  ShieldCheck,
  Smartphone,
  Zap,
} from 'lucide-react'

const reveal = {
  initial: { opacity: 0, y: 28 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: '-80px' },
  transition: { duration: 0.55, ease: 'easeOut' },
}

const proofStats = [
  {
    value: '1,500',
    label: 'clean concurrent-student load test',
    detail: '0% errors across a full real-exam run',
  },
  {
    value: '3,500',
    label: 'concurrent-student architecture target',
    detail: 'KVM + Redis live-frame cache sized with headroom',
  },
  {
    value: '6,500',
    label: 'live-frame cache capacity',
    detail: 'env-tunable cap for camera thumbnails and pop-in review',
  },
  {
    value: '890+',
    label: 'backend tests passing',
    detail: 'plus CI security scans and zero open CodeQL alerts',
  },
  {
    value: '<1s',
    label: 'camera pop-in latency',
    detail: 'thumbnail pre-warms before teacher clicks',
  },
  {
    value: '~3s',
    label: '50-answer batch grading',
    detail: 'semaphore-batched parallel grading path',
  },
  {
    value: '1/2s',
    label: 'ML inference rate on hot CPU',
    detail: 'drops to 1 frame per 2 seconds when CPU exceeds 85%',
  },
]

const storyPanels = [
  {
    kicker: 'Operate the exam',
    icon: GraduationCap,
    title: 'One workflow from roster to scorecard.',
    body: 'Procta is not another monitoring widget. Teachers create an exam, import the roster, schedule the window, send invites, watch the room, and export branded results from the same product surface.',
    bullets: [
      'CSV roster dry-runs, student groups, reminders, and access codes',
      'Live teacher dashboard with session state, risk, chat, and controls',
      'Scorecards, CSV exports, and evidence packets after submission',
    ],
    visual: 'workflow',
  },
  {
    kicker: 'Secure the runtime',
    icon: ShieldCheck,
    title: 'A locked exam browser that survives real student machines.',
    body: 'The Procta Secure Browser handles kiosk mode, fullscreen enforcement, process checks, VM and remote-desktop detection, local autosave, and crash-resilient submission before the server ever has to guess.',
    bullets: [
      'Fullscreen lockdown, process integrity, and tab-switch detection',
      'Local autosave and reconnect-safe answer sync',
      'Adaptive hardware governor for budget laptops',
    ],
    visual: 'browser',
  },
  {
    kicker: 'Detect with context',
    icon: Brain,
    title: 'On-device AI turns behavior into reviewable evidence.',
    body: 'Face, gaze, head pose, object detection, speech keywords, and multi-voice signals run on the student machine. Teachers see evidence and confidence, not automatic punishment.',
    bullets: [
      'Face, gaze, head pose, object, and eye-state monitoring',
      'Vosk speech keywords and Silero VAD + MFCC multi-voice detection',
      'Phone-camera room scan for desk and side-device context',
    ],
    visual: 'signals',
  },
  {
    kicker: 'Command the room',
    icon: Activity,
    title: 'Live invigilation without alert floods.',
    body: 'A single command center shows risk, calibration, violations, thumbnails, stale sessions, force-submit controls, and per-session evidence so faculty can act while the exam is still recoverable.',
    bullets: [
      'Risk, severity, camera pop-in, timeline, and session controls',
      'Warn, pause, resume, end, and chat in one review surface',
      '6,500-session live-frame cache sized for high-scale exam days',
    ],
    visual: 'command',
  },
  {
    kicker: 'Close the loop',
    icon: FileSpreadsheet,
    title: 'Results and audit packets are ready while the room is still warm.',
    body: 'MCQ scoring, short-answer grading suggestions, clustered review, PDF scorecards, evidence exports, and appeal-ready timelines turn proctoring into a defensible institutional workflow.',
    bullets: [
      'Parallel AI grading suggestions with teacher confirmation',
      'Evidence packets with screenshots, answers, risk, and reviewer notes',
      'Fairness and appeals language for academic committees',
    ],
    visual: 'evidence',
  },
]

const capabilities = [
  { icon: Smartphone, title: 'Phone-camera room scan', body: 'QR pairing turns a student phone into a second room camera for desk and side-device context.' },
  { icon: Zap, title: 'Parallel AI grading', body: 'Short-answer grading suggestions run in parallel and stay human-reviewed.' },
  { icon: MessageSquare, title: 'Mid-exam student chat', body: 'Broadcast or reply without breaking the proctored session.' },
  { icon: Landmark, title: 'LMS integrations', body: 'LTI 1.3 deep-link launch for Canvas and Moodle, plus Google Classroom sync paths.' },
  { icon: ReceiptIndianRupee, title: 'India-ready billing', body: 'Razorpay Checkout, UPI Autopay, INR/GST plans, quotas, and overage logic.' },
  { icon: BadgeCheck, title: 'Fairness and appeals', body: 'Explainable evidence, confidence, screenshots, issue reports, and teacher decisions.' },
  { icon: ServerCog, title: 'Production operations', body: 'Docker, Caddy TLS, Redis/RQ workers, health checks, request IDs, backups, and scans.' },
  { icon: Layers, title: 'Cluster & batch review', body: 'Group violations by type and severity across cohorts, then bulk-dismiss false positives.' },
  { icon: Cpu, title: 'Adaptive hardware governor', body: 'ML cadence throttles automatically on heat-stressed CPUs.' },
  { icon: Database, title: 'Live-frame cache', body: '6,500-session LRU with frame caps and admin observability.' },
]

function VisualPanel({ type }) {
  if (type === 'workflow') {
    return (
      <div className="visual-console">
        <div className="visual-console__bar">
          <span />
          <span />
          <span />
        </div>
        <div className="space-y-3 p-4">
          {['Import roster', 'Schedule window', 'Send invite', 'Monitor live', 'Export scorecards'].map((step, i) => (
            <div key={step} className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.035] p-3">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent/10 font-mono text-xs text-accent-light">
                0{i + 1}
              </div>
              <div className="h-2 flex-1 rounded-full bg-white/[0.08]">
                <div className="h-full rounded-full bg-accent/70" style={{ width: `${44 + i * 10}%` }} />
              </div>
              <span className="hidden text-xs text-slate-400 sm:block">{step}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (type === 'browser') {
    return (
      <div className="visual-console">
        <div className="rounded-xl border border-accent/20 bg-navy-950/80 p-5">
          <div className="mb-5 flex items-center justify-between">
            <div className="label-mono text-accent-light">Procta Secure Browser</div>
            <div className="rounded-full border border-emerald/20 bg-emerald/10 px-3 py-1 font-mono text-xs text-emerald">LOCKED</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {['Fullscreen', 'Process scan', 'Local autosave', 'VM check'].map((item) => (
              <div key={item} className="rounded-lg border border-white/[0.06] bg-white/[0.035] p-4">
                <div className="mb-3 h-8 w-8 rounded-lg bg-accent/10" />
                <div className="text-sm font-semibold text-white">{item}</div>
                <div className="mt-2 h-1.5 rounded-full bg-accent/35" />
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (type === 'signals') {
    return (
      <div className="visual-console">
        <div className="grid gap-3 p-4">
          {[
            ['Face present', '98%', 'bg-emerald'],
            ['Gaze confidence', '74%', 'bg-accent'],
            ['Object scan', 'clear', 'bg-accent-light'],
            ['Voice anomaly', 'low', 'bg-amber'],
          ].map(([label, value, color]) => (
            <div key={label} className="rounded-xl border border-white/[0.06] bg-white/[0.035] p-4">
              <div className="flex items-center justify-between gap-4">
                <span className="text-sm font-medium text-slate-200">{label}</span>
                <span className="font-mono text-xs text-slate-400">{value}</span>
              </div>
              <div className="mt-3 h-2 rounded-full bg-white/[0.07]">
                <div className={`h-full rounded-full ${color}`} style={{ width: value === 'clear' ? '92%' : value === 'low' ? '26%' : value }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (type === 'command') {
    return (
      <div className="visual-console">
        <div className="p-4">
          <div className="mb-4 grid grid-cols-3 gap-3">
            {['Live now', 'High risk', 'Stale'].map((label, i) => (
              <div key={label} className="rounded-lg border border-white/[0.06] bg-white/[0.035] p-3">
                <div className="font-display text-2xl font-bold text-white">{[42, 3, 1][i]}</div>
                <div className="label-mono text-slate-500">{label}</div>
              </div>
            ))}
          </div>
          {['CS2024-015', 'CS2024-042', 'CS2024-108'].map((roll, i) => (
            <div key={roll} className="mb-2 flex items-center justify-between rounded-lg border border-white/[0.06] bg-navy-950/70 px-3 py-3">
              <span className="font-mono text-xs text-slate-300">{roll}</span>
              <span className={`rounded-full px-2 py-0.5 font-mono text-xs ${i === 1 ? 'bg-red-500/10 text-red-400' : 'bg-emerald/10 text-emerald'}`}>
                {i === 1 ? 'review' : 'normal'}
              </span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="visual-console">
      <div className="p-4">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.035] p-5">
          <div className="mb-4 flex items-center justify-between">
            <span className="label-mono text-accent-light">Evidence packet</span>
            <span className="font-mono text-xs text-slate-500">PDF + CSV</span>
          </div>
          <div className="space-y-3">
            {['Answers', 'Risk timeline', 'Screenshots', 'Reviewer note'].map((row, i) => (
              <div key={row} className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg border border-accent/20 bg-accent/10" />
                <div className="h-2 flex-1 rounded-full bg-white/[0.08]">
                  <div className="h-full rounded-full bg-accent/60" style={{ width: `${82 - i * 12}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function CapabilityProof() {
  return (
    <section className="relative overflow-hidden bg-navy-900/30 py-24 md:py-32">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="pointer-events-none absolute left-1/2 top-24 h-[520px] w-[720px] -translate-x-1/2 rounded-full bg-accent/10 blur-[160px]" />
      <div className="relative mx-auto max-w-7xl px-6">
        <motion.div {...reveal} className="mx-auto max-w-3xl text-center">
          <span className="label-mono text-accent">Production capability</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-5xl">
            Not a webcam plugin. A full exam operating system.
          </h2>
          <p className="mt-4 text-lg leading-relaxed text-slate-400">
            Procta covers the full institutional workflow: exam setup, secure
            student runtime, live proctoring, AI-assisted grading, reporting,
            billing, LMS integrations, and operational reliability.
          </p>
        </motion.div>

        <motion.div {...reveal} className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-7">
          {proofStats.map((stat) => (
            <div key={stat.label} className="proof-metric">
              <div className="font-display text-3xl font-bold text-white"><CountUp value={stat.value} /></div>
              <div className="mt-2 text-sm font-semibold leading-snug text-slate-300">{stat.label}</div>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">{stat.detail}</p>
            </div>
          ))}
        </motion.div>

        <div className="mt-20 space-y-10">
          {storyPanels.map((panel, index) => (
            <motion.article
              key={panel.title}
              {...reveal}
              className={`feature-spotlight ${index % 2 ? 'lg:[&_.feature-copy]:order-2' : ''}`}
            >
              <div className="feature-copy">
                <div className="mb-5 inline-flex rounded-xl border border-accent/20 bg-accent/5 p-3 accent-glow">
                  <panel.icon size={22} className="text-accent-light" />
                </div>
                <div className="label-mono text-accent-light">{panel.kicker}</div>
                <h3 className="mt-3 font-display text-3xl font-bold leading-tight text-white md:text-4xl">
                  {panel.title}
                </h3>
                <p className="mt-4 text-base leading-relaxed text-slate-400 md:text-lg">{panel.body}</p>
                <ul className="mt-6 space-y-3">
                  {panel.bullets.map((bullet) => (
                    <li key={bullet} className="flex items-start gap-3 text-sm leading-relaxed text-slate-300">
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                      <span>{bullet}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <VisualPanel type={panel.visual} />
            </motion.article>
          ))}
        </div>

        <motion.div {...reveal} className="mt-16">
          <div className="mb-6 flex flex-col justify-between gap-3 md:flex-row md:items-end">
            <div>
              <div className="label-mono text-slate-500">Capability index</div>
              <h3 className="mt-2 font-display text-2xl font-bold text-white">The supporting systems underneath</h3>
            </div>
            <p className="max-w-xl text-sm leading-relaxed text-slate-500">
              The story above is what buyers remember. This index keeps the product breadth visible for technical evaluators.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {capabilities.map((item) => (
              <div key={item.title} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 transition-colors hover:border-accent/20">
                <item.icon size={17} className="mb-3 text-accent-light" />
                <h4 className="text-sm font-semibold text-white">{item.title}</h4>
                <p className="mt-2 text-xs leading-relaxed text-slate-500">{item.body}</p>
              </div>
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  )
}
