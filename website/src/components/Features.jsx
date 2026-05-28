import {
  Lock, MonitorOff, Save, Eye, ScanFace, Box, Volume2,
  BarChart3, FileText, Activity, Sliders, Download, Users,
  Smartphone, MessageSquare, GraduationCap, ReceiptIndianRupee,
  BadgeCheck, ServerCog, UploadCloud, Layers3
} from 'lucide-react'

const groups = [
  {
    label: 'Exam Security',
    items: [
      { icon: Lock, name: 'Kiosk Mode', desc: 'Full-screen lockdown prevents alt-tab, screenshots, and app switching' },
      { icon: MonitorOff, name: 'Anti-Tab Switching', desc: 'Detects and logs every attempt to leave the exam window' },
      { icon: Save, name: '5-Second Auto-Save', desc: 'Frequent local save plus server sync protects answers during network drops' },
      { icon: ServerCog, name: 'Process Integrity', desc: 'Detects screen recorders, remote desktop tools, VMs, and suspicious environments' },
    ]
  },
  {
    label: 'AI Proctoring',
    items: [
      { icon: ScanFace, name: 'Face Detection', desc: 'MediaPipe-powered face presence monitoring with absence tracking' },
      { icon: Eye, name: 'Gaze Tracking', desc: 'Detects prolonged off-screen gaze patterns indicating external reference' },
      { icon: Box, name: 'Object Detection', desc: 'YOLOv8n identifies phones, books, and unauthorized items in real-time' },
      { icon: Volume2, name: 'Audio Analysis', desc: 'RMS-based voice detection flags conversations and dictation' },
      { icon: Smartphone, name: 'Phone Room Camera', desc: 'QR pairing turns a student phone into a second room-scan camera' },
    ]
  },
  {
    label: 'Analytics & Reports',
    items: [
      { icon: Activity, name: 'Violation Timeline', desc: 'Timestamped log of every detected anomaly during the exam session' },
      { icon: BarChart3, name: 'Risk Scoring', desc: 'Log-saturating 0-100 score normalized by exam duration' },
      { icon: FileText, name: 'Evidence Packets', desc: 'Downloadable PDFs with answers, risk, screenshots, and reviewer notes' },
      { icon: GraduationCap, name: 'AI Short-Answer Grading', desc: 'Parallel LLM grading suggestions with teacher confirmation before publishing' },
    ]
  },
  {
    label: 'Admin Control',
    items: [
      { icon: Sliders, name: 'Live Dashboard', desc: 'Real-time monitoring of all active exam sessions in one view' },
      { icon: Download, name: 'CSV Export', desc: 'Export results, scores, and violation data for institutional records' },
      { icon: Users, name: 'Student Management', desc: 'Pre-registration, scheduling, groups, and access code configuration' },
      { icon: UploadCloud, name: 'Bulk CSV Import', desc: 'Dry-run imports with roll-number format detection for CBSE/JEE/NTA-style rosters' },
      { icon: MessageSquare, name: 'Student Chat', desc: 'Broadcast or reply to students during the exam without leaving the dashboard' },
    ]
  },
  {
    label: 'Institution Readiness',
    items: [
      { icon: Layers3, name: 'LTI 1.3 + Classroom', desc: 'Canvas/Moodle launch support and Google Classroom sync paths' },
      { icon: ReceiptIndianRupee, name: 'Razorpay Billing', desc: 'INR plans, UPI Autopay subscription path, quotas, and overage logic' },
      { icon: BadgeCheck, name: 'Issues & Appeals', desc: 'Teacher issue reports and human-in-the-loop review for fairness' },
      { icon: ServerCog, name: 'Scale Headroom', desc: '1,500 clean VU run, 3,500-student architecture target, 6,500 live-frame cache cap' },
    ]
  },
]

export default function Features() {
  return (
    <section id="features" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <div
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">Features</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Everything You Need
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            A complete exam integrity platform, not just a webcam plugin.
          </p>
        </div>

        <div className="mt-16 space-y-12">
          {groups.map((group) => (
            <div
              key={group.label}
            >
              <h3 className="mb-4 label-mono text-slate-500">
                {group.label}
              </h3>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {group.items.map(item => (
                  <div
                    key={item.name}
                    className="group relative rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 transition-all hover:border-accent/20 hover:bg-accent/[0.03] card-topline grain-overlay"
                  >
                    <item.icon size={18} className="mb-3 text-slate-500 transition-colors group-hover:text-accent-light" />
                    <h4 className="mb-1 text-sm font-semibold text-white">{item.name}</h4>
                    <p className="text-xs leading-relaxed text-slate-400">{item.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
