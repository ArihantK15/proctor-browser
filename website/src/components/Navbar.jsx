import { useState } from 'react'
import { Link } from 'wouter'
import { Menu, X } from 'lucide-react'
import { APP_URL } from '../config'

export default function Navbar() {
  const [open, setOpen] = useState(false)

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
    <nav className="fixed top-0 left-0 right-0 z-50 border-b border-white/5 bg-navy-950/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        <Link to="/" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} className="flex items-center gap-2.5 no-underline">
          {/* Brand chip — shield+eye mark, white on accent. Matches the
              favicon family (which is the same mark, blue on navy) so the
              browser tab + nav + footer + Google SERP all read as the
              same logo. */}
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M4 2 H12 Q13.5 2 13.5 3.5 V8 Q13.5 12 8 14 Q2.5 12 2.5 8 V3.5 Q2.5 2 4 2 Z"
                    fill="none" stroke="white" strokeWidth="1.2" strokeLinejoin="round"/>
              <circle cx="8" cy="8" r="1.5" fill="white"/>
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
            href={`${APP_URL}/dashboard`}
            className="rounded-lg px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:text-white no-underline"
          >
            Log In
          </a>
          <Link
            to="/signup"
            className="rounded-lg bg-accent-dark px-4 py-2 text-sm font-medium text-white glow-btn no-underline"
          >
            Start Free Trial
          </Link>
        </div>

        <button
          onClick={() => setOpen(!open)}
          className="text-slate-400 md:hidden bg-transparent border-none cursor-pointer"
          aria-label="Toggle menu"
        >
          {open ? <X size={24} /> : <Menu size={24} />}
        </button>
      </div>

      {open && (
        <div className="border-t border-white/5 bg-navy-950/95 backdrop-blur-xl md:hidden">
          <div className="flex flex-col gap-1 px-6 py-4">
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
                href={`${APP_URL}/dashboard`}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-2.5 text-sm text-slate-300 hover:text-white no-underline"
              >
                Log In
              </a>
              <Link
                to="/signup"
                onClick={() => setOpen(false)}
                className="rounded-lg bg-accent-dark px-3 py-2.5 text-center text-sm font-medium text-white glow-btn no-underline"
              >
                Start Free Trial
              </Link>
            </div>
          </div>
        </div>
      )}
    </nav>
  )
}
