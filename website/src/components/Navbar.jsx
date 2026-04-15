import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import { APP_URL } from '../config'

export default function Navbar() {
  const [open, setOpen] = useState(false)

  const links = [
    { label: 'Features', href: '#features' },
    { label: 'How It Works', href: '#how-it-works' },
    { label: 'Use Cases', href: '#use-cases' },
    { label: 'Privacy', href: '#privacy' },
    { label: 'FAQ', href: '#faq' },
  ]

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 border-b border-white/5 bg-navy-950/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        <Link to="/" className="flex items-center gap-2.5 no-underline">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 3h3v1H5v8h2v1H4V3zm5 0h3v10h-3v-1h2V4H9V3z" fill="white"/>
              <circle cx="8" cy="8" r="1.5" fill="white" opacity="0.8"/>
            </svg>
          </div>
          <span className="font-display text-lg font-bold text-white tracking-tight">Procta</span>
        </Link>

        <div className="hidden items-center gap-8 md:flex">
          {links.map(l => (
            <a
              key={l.href}
              href={l.href}
              className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline"
            >
              {l.label}
            </a>
          ))}
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
            Request Demo
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
            {links.map(l => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-2.5 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-accent-light no-underline"
              >
                {l.label}
              </a>
            ))}
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
                Request Demo
              </Link>
            </div>
          </div>
        </div>
      )}
    </nav>
  )
}
