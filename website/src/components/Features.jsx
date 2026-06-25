import {
  Lock, MonitorOff, Save, Eye, ScanFace, Box, Volume2,
  BarChart3, FileText, Activity, Sliders, Download, Users,
  Smartphone, MessageSquare, GraduationCap, ReceiptIndianRupee,
  BadgeCheck, ServerCog, UploadCloud, Layers3,
  FileQuestion, FileInput, Sparkles, Calculator, ListChecks, Building2
} from 'lucide-react'
import { motion, useReducedMotion } from 'framer-motion'
import { fadeUp, stagger, inViewProps, pick } from '../lib/motion'

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
      { icon: Box, name: 'Object Detection', desc: 'YOLO26 identifies phones, earphones, headphones, and smartwatches in real-time — NMS-free and CPU-optimized for student laptops' },
      { icon: Volume2, name: 'Audio Analysis', desc: 'RMS-based voice detection flags conversations and dictation' },
      { icon: Smartphone, name: 'Phone Room Camera', desc: 'QR pairing turns a student phone into a second room-scan camera' },
    ]
  },
  {
    label: 'Question Authoring',
    items: [
      { icon: FileQuestion, name: 'Reusable Question Bank', desc: 'Build a tagged pool of questions once, reuse it across exams, import and export in bulk' },
      { icon: FileInput, name: 'Import from PDF & Word', desc: 'Upload existing question papers — extraction runs on your own server and parses questions, options, and answer keys. Math and diagrams are preserved as images' },
      { icon: Sparkles, name: 'AI Question Generation', desc: 'Generate questions from a topic, or straight from your notes (PDF, Word, PowerPoint). Every question is reviewed before it is saved — nothing auto-publishes' },
      { icon: Calculator, name: 'Numeric & Integer Answers', desc: 'JEE-style numeric questions with a tolerance range, alongside MCQ, multi-select, true/false, and AI-graded short answer' },
      { icon: ListChecks, name: 'AI Lint & Rubrics', desc: 'One-click question quality checks, auto-generated grading rubrics, and tag suggestions' },
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
      { icon: Layers3, name: 'LTI 1.3 (beta) + Classroom', desc: 'Canvas/Moodle deep-link launch (LTI 1.3, in beta) and Google Classroom sync' },
      { icon: ReceiptIndianRupee, name: 'Razorpay Billing', desc: 'INR plans, UPI Autopay subscription path, quotas, and overage logic' },
      { icon: BadgeCheck, name: 'Issues & Appeals', desc: 'Teacher issue reports and human-in-the-loop review for fairness' },
      { icon: Building2, name: 'Organizations & Roles', desc: 'Many teachers under one institution, with admin oversight — a roll-up across every teacher’s exams, results, and evidence, and strict per-teacher isolation' },
      { icon: ServerCog, name: 'Scale Headroom', desc: '1,500 clean VU run, 3,500-student architecture target, 6,500 live-frame cache cap' },
    ]
  },
]

export default function Features() {
  const reduced = useReducedMotion()
  const childVar = pick(reduced, fadeUp)
  return (
    <section id="features" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          className="mx-auto max-w-2xl text-center"
          variants={childVar}
          {...inViewProps}
        >
          <span className="label-mono text-accent">Features</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Everything You Need
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            A complete exam integrity platform, not just a webcam plugin.
          </p>
        </motion.div>

        <div className="mt-16 space-y-12">
          {groups.map((group) => (
            <div
              key={group.label}
            >
              <h3 className="mb-4 label-mono text-slate-500">
                {group.label}
              </h3>
              <motion.div
                className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
                variants={stagger(0.06)}
                {...inViewProps}
              >
                {group.items.map(item => (
                  <motion.div
                    key={item.name}
                    variants={childVar}
                    whileHover={reduced ? undefined : { y: -4 }}
                    transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
                    className="group relative rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 transition-colors hover:border-accent/20 hover:bg-accent/[0.03] card-topline grain-overlay"
                  >
                    <item.icon size={18} className="mb-3 text-slate-500 transition-colors group-hover:text-accent-light" />
                    <h4 className="mb-1 text-sm font-semibold text-white">{item.name}</h4>
                    <p className="text-xs leading-relaxed text-slate-400">{item.desc}</p>
                  </motion.div>
                ))}
              </motion.div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
