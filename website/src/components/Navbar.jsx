import { useState, useEffect } from 'react'
import { Link } from 'wouter'
import { Menu, X } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { getLenis } from '../lib/smoothScroll'
import { APP_URL } from '../config'

export default function Navbar() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const reduced = useReducedMotion()

  // Scroll-aware chrome: once the page scrolls, deepen the nav background
  // and add a soft elevation so it reads as a real app bar over content.
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Order matches the visitor's question hierarchy:
  //   "Why should I trust this?" (Why Procta — outcomes from Phase 2)
  //   "How does it work?"        (How It Works — 4-step flow)
  //   "Will it fit my use case?" (Use Cases — universities / EdTech / hiring)
  //   "What about my data?"      (Privacy — DPDP Act 2023)
  //   "Anything I'm missing?"    (FAQ)
  const links = [
    { label: 'Features',     href: '/features' },
    { label: 'Pricing',      href: '/pricing' },
    { label: 'LTI Setup',    href: '/lti-setup' },
    { label: 'How It Works', href: '/how-it-works' },
    { label: 'Why Procta',   href: '/#differentiators' },
    { label: 'Trust',        href: '/trust' },
    { label: 'FAQ',          href: '/#faq' },
    { label: 'Blog',         href: '/blog' },
  ]

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 border-b backdrop-blur-xl transition-colors duration-300 ${
        scrolled
          ? 'border-white/10 bg-navy-950/90 shadow-lg shadow-black/30'
          : 'border-white/5 bg-navy-950/80'
      }`}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
        <Link to="/" onClick={() => { const l = getLenis(); if (l) l.scrollTo(0); else window.scrollTo({ top: 0, behavior: 'smooth' }) }} className="flex items-center gap-2.5 no-underline">
          {/* Brand chip — negative-space "P" mark, white on accent.
              Matches the favicon family (same mark, blue on navy) so the
              browser tab + nav + footer + Google SERP all read as the
              same logo. */}
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
            <svg width="18" height="18" viewBox="0 0 64 64" fill="none" aria-hidden="true">
              <path d="M20 15H40C45.523 15 50 19.477 50 25V25C50 30.523 45.523 35 40 35H27.5V50H20V15ZM27.5 22.5V27.5H40C41.38 27.5 42.5 26.38 42.5 25V25C42.5 23.62 41.38 22.5 40 22.5H27.5Z" fill="white"/>
            </svg>
          </div>
          <span className="font-display text-lg font-bold text-white tracking-tight">Procta</span>
        </Link>

        <div className="hidden items-center gap-8 md:flex">
          {links.map(l => {
            const isHash = l.href.startsWith('/#') || l.href.startsWith('#')
            return isHash ? (
              <a key={l.href} href={l.href} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">
                {l.label}
              </a>
            ) : (
              <Link key={l.href} to={l.href} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">
                {l.label}
              </Link>
            )
          })}
        </div>

        <div className="hidden items-center gap-3 md:flex">
          <a
            href={`${APP_URL}/login`}
            className="rounded-lg px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:text-white no-underline"
          >
            Log In
          </a>
          <Link
            to="/signup"
            className="rounded-lg bg-accent-dark px-4 py-2 text-sm font-medium text-white glow-btn no-underline transition-transform active:scale-[0.97]"
          >
            Start Free Trial
          </Link>
        </div>

        <button
          onClick={() => setOpen(!open)}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 md:hidden bg-transparent border-none cursor-pointer transition-colors hover:bg-white/5 hover:text-white active:scale-[0.97]"
          aria-label="Toggle menu"
          aria-expanded={open}
        >
          {open ? <X size={24} /> : <Menu size={24} />}
        </button>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            key="mobile-menu"
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8 }}
            animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -8 }}
            transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
            className="max-h-[calc(100dvh-64px)] overflow-y-auto border-t border-white/5 bg-navy-950/95 backdrop-blur-xl md:hidden"
          >
            <div className="flex flex-col gap-1 px-4 py-4 sm:px-6">
              {links.map(l => {
                const isHash = l.href.startsWith('/#') || l.href.startsWith('#')
                return isHash ? (
                  <a key={l.href} href={l.href} onClick={() => setOpen(false)} className="rounded-lg px-3 py-2.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-accent-light no-underline">
                    {l.label}
                  </a>
                ) : (
                  <Link key={l.href} to={l.href} onClick={() => setOpen(false)} className="rounded-lg px-3 py-2.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-accent-light no-underline">
                    {l.label}
                  </Link>
                )
              })}
              <div className="mt-3 flex flex-col gap-2 border-t border-white/5 pt-3">
                <a
                  href={`${APP_URL}/login`}
                  onClick={() => setOpen(false)}
                  className="rounded-lg px-3 py-2.5 text-sm text-slate-300 hover:text-white no-underline"
                >
                  Log In
                </a>
                <Link
                  to="/signup"
                  onClick={() => setOpen(false)}
                  className="rounded-lg bg-accent-dark px-3 py-2.5 text-center text-sm font-medium text-white glow-btn no-underline active:scale-[0.97]"
                >
                  Start Free Trial
                </Link>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </nav>
  )
}
