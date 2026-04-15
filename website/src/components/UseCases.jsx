import { motion } from 'framer-motion'
import { GraduationCap, BookOpen, Briefcase, ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function UseCases() {
  const cases = [
    {
      icon: GraduationCap,
      title: 'Universities & Colleges',
      desc: 'Semester exams, entrance tests, and internal assessments. Procta scales from a single department to the entire institution.',
      features: ['Bulk student registration', 'Scheduled exam windows', 'Faculty dashboard access'],
    },
    {
      icon: BookOpen,
      title: 'EdTech Platforms',
      desc: 'Certification exams and course assessments that need verified integrity. Issue credentials your students can trust.',
      features: ['API integration', 'White-label options', 'Certificate verification'],
    },
    {
      icon: Briefcase,
      title: 'Hiring & HR Teams',
      desc: 'Technical assessments and aptitude tests for candidate screening. Ensure the person taking the test is the person you hired.',
      features: ['Identity verification', 'Anti-impersonation', 'Custom question banks'],
    },
  ]

  return (
    <section id="use-cases" className="relative py-24 md:py-32">
      <div className="mx-auto max-w-7xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
          className="mx-auto max-w-2xl text-center"
        >
          <span className="label-mono text-accent">Use Cases</span>
          <h2 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl">
            Built for Your Industry
          </h2>
          <p className="mt-4 text-lg text-slate-400">
            Different audiences, same guarantee: exam integrity you can trust.
          </p>
        </motion.div>

        <div className="mt-16 grid gap-6 md:grid-cols-3">
          {cases.map((item, i) => (
            <motion.div
              key={item.title}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="group relative flex flex-col rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 transition-colors hover:border-accent/15 hover:bg-accent/[0.02] card-topline grain-overlay"
            >
              <div className="mb-6 inline-flex self-start rounded-xl border border-accent/20 bg-accent/5 p-3 accent-glow">
                <item.icon size={24} className="text-accent-light" />
              </div>
              <h3 className="mb-3 text-xl font-semibold text-white">{item.title}</h3>
              <p className="mb-6 text-sm leading-relaxed text-slate-400">{item.desc}</p>
              <ul className="mt-auto space-y-2">
                {item.features.map(f => (
                  <li key={f} className="flex items-center gap-2 text-sm text-slate-400">
                    <span className="h-1 w-1 rounded-full bg-accent" />
                    {f}
                  </li>
                ))}
              </ul>
              <Link
                to="/signup"
                className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-accent-light transition-colors hover:text-white no-underline"
              >
                Learn more <ArrowRight size={14} />
              </Link>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
