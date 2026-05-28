import {
  Activity,
  BadgeCheck,
  Camera,
  FileSpreadsheet,
  GraduationCap,
  Landmark,
  MessageSquare,
  ReceiptIndianRupee,
  ServerCog,
  ShieldCheck,
  Smartphone,
  Zap,
} from 'lucide-react'

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
    value: '617',
    label: 'backend tests passing',
    detail: 'plus CI security scans and zero open CodeQL alerts',
  },
]

const capabilities = [
  {
    icon: GraduationCap,
    title: 'Complete exam workflow',
    body: 'Create exams, import CSV rosters, schedule windows, assign groups, send reminders, run the exam, export results, and issue branded scorecards.',
  },
  {
    icon: ShieldCheck,
    title: 'Secure Electron browser',
    body: 'Kiosk mode, fullscreen enforcement, process checks, VM/remote-desktop detection, local autosave, and crash-resilient submit flow.',
  },
  {
    icon: Camera,
    title: 'On-device AI proctoring',
    body: 'Face, gaze, head pose, eye state, object detection, and voice-activity signals run on the student machine before events reach the server.',
  },
  {
    icon: Smartphone,
    title: 'Phone-camera room scan',
    body: 'QR pairing turns the student phone into a second room camera so teachers can see desk, notes, and side-device context.',
  },
  {
    icon: Activity,
    title: 'Live command center',
    body: 'Teachers see risk, calibration, violations, thumbnails, stale sessions, force-submit controls, and per-session evidence from one dashboard.',
  },
  {
    icon: Zap,
    title: 'Parallel AI grading',
    body: 'Short-answer grading suggestions run in parallel and stay human-reviewed, shrinking large answer-review batches from minutes to seconds.',
  },
  {
    icon: MessageSquare,
    title: 'Mid-exam student chat',
    body: 'Broadcast instructions or reply to one student without breaking the proctored session or forcing them outside the browser.',
  },
  {
    icon: FileSpreadsheet,
    title: 'Bulk imports and exports',
    body: 'Roster CSV dry-runs, roll-number format detection, result CSVs, PDF evidence packets, scorecards, and institution-ready reports.',
  },
  {
    icon: Landmark,
    title: 'LMS integrations',
    body: 'LTI 1.3 deep-link launch for Canvas and Moodle, plus Google Classroom sync paths for course and roster operations.',
  },
  {
    icon: ReceiptIndianRupee,
    title: 'India-ready billing',
    body: 'Razorpay Checkout, UPI Autopay subscription path, INR/GST-ready plans, quota enforcement, and overage logic.',
  },
  {
    icon: BadgeCheck,
    title: 'Fairness and appeals',
    body: 'Risk scores are explainable evidence, not automatic punishment. Teachers review timelines, confidence, screenshots, and issue reports.',
  },
  {
    icon: ServerCog,
    title: 'Production operations',
    body: 'Docker deploys, Caddy TLS, Redis/RQ workers, health checks, request IDs, backups, security scans, and rollback-oriented runbooks.',
  },
]

const demoFlow = [
  'Faculty creates exam and imports students',
  'Students install secure browser and pair phone cam',
  'Teacher monitors live risk and evidence',
  'Students submit; scorecards and exports are ready',
]

export default function CapabilityProof() {
  return (
    <section className="relative py-24 md:py-32 bg-navy-900/30">
      <div className="pointer-events-none absolute inset-0 grain-overlay" />
      <div className="relative mx-auto max-w-7xl px-6">
        <div className="mx-auto max-w-3xl text-center">
          <span className="label-mono text-accent">Demo-ready proof</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Not a webcam plugin. A full exam operating system.
          </h2>
          <p className="mt-4 text-lg leading-relaxed text-slate-400">
            The product already covers the workflow your professor will ask about:
            exam setup, secure student runtime, live proctoring, grading,
            reporting, billing, integrations, and operational reliability.
          </p>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {proofStats.map((stat) => (
            <div key={stat.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 card-topline">
              <div className="font-display text-3xl font-bold text-white">{stat.value}</div>
              <div className="mt-2 text-sm font-semibold text-slate-300">{stat.label}</div>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">{stat.detail}</p>
            </div>
          ))}
        </div>

        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {capabilities.map((item) => (
            <div key={item.title} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 transition-colors hover:border-accent/20">
              <div className="mb-4 inline-flex rounded-lg border border-accent/20 bg-accent/5 p-2.5">
                <item.icon size={18} className="text-accent-light" />
              </div>
              <h3 className="text-base font-semibold text-white">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">{item.body}</p>
            </div>
          ))}
        </div>

        <div className="mt-14 rounded-2xl border border-accent/20 bg-accent/[0.04] p-6 md:p-8">
          <div className="label-mono text-accent-light">Exam-day workflow</div>
          <div className="mt-5 grid gap-3 md:grid-cols-4">
            {demoFlow.map((step, i) => (
              <div key={step} className="rounded-xl border border-white/[0.06] bg-navy-950/60 p-4">
                <div className="font-mono text-xs text-accent">0{i + 1}</div>
                <p className="mt-2 text-sm font-medium leading-relaxed text-slate-200">{step}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
