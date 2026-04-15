import { motion } from 'framer-motion'
import {
  Lock, MonitorOff, Save, Eye, ScanFace, Box, Volume2,
  BarChart3, FileText, Activity, Sliders, Download, Users
} from 'lucide-react'

const groups = [
  {
    label: 'Exam Security',
    items: [
      { icon: Lock, name: 'Kiosk Mode', desc: 'Full-screen lockdown prevents alt-tab, screenshots, and app switching' },
      { icon: MonitorOff, name: 'Anti-Tab Switching', desc: 'Detects and logs every attempt to leave the exam window' },
      { icon: Save, name: 'Auto-Save & Submit', desc: 'Continuous 60-second auto-save with automatic submission at time expiry' },
    ]
  },
  {
    label: 'AI Proctoring',
    items: [
      { icon: ScanFace, name: 'Face Detection', desc: 'MediaPipe-powered face presence monitoring with absence tracking' },
      { icon: Eye, name: 'Gaze Tracking', desc: 'Detects prolonged off-screen gaze patterns indicating external reference' },
      { icon: Box, name: 'Object Detection', desc: 'YOLOv8n identifies phones, books, and unauthorized items in real-time' },
      { icon: Volume2, name: 'Audio Analysis', desc: 'RMS-based voice detection flags conversations and dictation' },
    ]
  },
  {
    label: 'Analytics & Reports',
    items: [
      { icon: Activity, name: 'Violation Timeline', desc: 'Timestamped log of every detected anomaly during the exam session' },
      { icon: BarChart3, name: 'Risk Scoring', desc: 'Log-saturating 0-100 score normalized by exam duration' },
      { icon: FileText, name: 'PDF Reports', desc: 'Downloadable reports with violation summaries and risk breakdowns' },
    ]
  },
  {
    label: 'Admin Control',
    items: [
      { icon: Sliders, name: 'Live Dashboard', desc: 'Real-time monitoring of all active exam sessions in one view' },
      { icon: Download, name: 'CSV Export', desc: 'Export results, scores, and violation data for institutional records' },
      { icon: Users, name: 'Student Management', desc: 'Pre-registration, scheduling, and access code configuration' },
    ]
  },
]

export default function Features() {
  return (
    <section id="features" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
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
          {groups.map((group, gi) => (
            <motion.div
              key={group.label}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.5, delay: gi * 0.1 }}
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
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
