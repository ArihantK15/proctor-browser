import { motion } from 'framer-motion'
import { Helmet } from 'react-helmet-async'

import { Link } from 'wouter'
import { ArrowRight } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

const posts = [
  {
    slug: 'online-exam-cheating-prevention-ai-proctoring',
    title: 'Online Exam Cheating Statistics & Prevention: How AI Proctoring Stops Academic Dishonesty in 2026',
    desc: 'Latest cheating statistics, common methods students use, and how AI proctoring with gaze tracking and object detection prevents academic dishonesty in online exams.',
    date: 'May 9, 2026',
    readTime: '10 min read',
  },
  {
    slug: 'dpdp-act-compliance-online-proctoring-indian-universities',
    title: 'DPDP Act Compliance for Online Proctoring: A Complete Guide for Indian Universities 2026',
    desc: 'Navigate DPDP Act 2023 compliance for AI proctoring in Indian higher education. Covers data minimization, consent, on-device processing, and step-by-step compliance checklist.',
    date: 'May 9, 2026',
    readTime: '9 min read',
  },
  {
    slug: 'ai-proctoring-vs-traditional-proctoring',
    title: 'AI Proctoring vs Traditional Proctoring: A Complete Comparison for 2026',
    desc: 'Compare cost, scalability, accuracy, and privacy trade-offs between AI-powered and in-person exam proctoring.',
    date: 'May 9, 2026',
    readTime: '8 min read',
  },
]

export default function Blog() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Procta Blog — AI Proctoring Insights & Best Practices</title>
        <meta name="description" content="Articles about AI proctoring, online exam security, academic integrity best practices, and comparisons between proctoring approaches." />
        <link rel="canonical" href="https://www.procta.net/blog" />
        <meta property="og:title" content="Procta Blog — AI Proctoring Insights" />
        <meta property="og:description" content="Articles about AI proctoring, online exam security, and academic integrity." />
        <meta property="og:url" content="https://www.procta.net/blog" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>
      <Navbar />
      <section className="pt-32 pb-20 md:pt-44 md:pb-32">
        <div className="mx-auto max-w-4xl px-6">
          <div className="animate-fadeIn">
            <h1 className="font-display text-4xl font-bold text-white md:text-5xl">
              Procta Blog
            </h1>
            <p className="mt-4 text-lg text-slate-400">
              Insights on AI proctoring, academic integrity, and best practices for online exams.
            </p>
          </div>

          <div className="mt-12 space-y-6">
            {posts.map((post, i) => (
              <motion.div
                key={post.slug}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: i * 0.1 }}
              >
                <Link to={`/blog/${post.slug}`} className="group block rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 transition-all hover:border-accent/20 hover:bg-accent/[0.03] no-underline">
                  <div className="flex items-center gap-3 text-xs text-slate-500 mb-2">
                    <span>{post.date}</span>
                    <span className="h-1 w-1 rounded-full bg-slate-600" />
                    <span>{post.readTime}</span>
                  </div>
                  <h2 className="font-display text-xl font-bold text-white group-hover:text-accent-light transition-colors">
                    {post.title}
                  </h2>
                  <p className="mt-2 text-sm text-slate-400 leading-relaxed">{post.desc}</p>
                  <div className="mt-4 flex items-center gap-1 text-sm text-accent-light font-medium">
                    Read more <ArrowRight size={14} />
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        </div>
      </section>
      <Footer />
    </div>
  )
}
