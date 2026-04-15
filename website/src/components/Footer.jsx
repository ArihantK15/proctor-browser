import { Link } from 'react-router-dom'
import { APP_URL } from '../config'

export default function Footer() {
  return (
    <footer className="border-t border-white/[0.06] bg-navy-950">
      <div className="mx-auto max-w-7xl px-6 py-12 md:py-16">
        <div className="grid gap-8 md:grid-cols-4">
          {/* Brand */}
          <div className="md:col-span-1">
            <Link to="/" className="flex items-center gap-2.5 no-underline">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 3h3v1H5v8h2v1H4V3zm5 0h3v10h-3v-1h2V4H9V3z" fill="white"/>
                  <circle cx="8" cy="8" r="1.5" fill="white" opacity="0.8"/>
                </svg>
              </div>
              <span className="font-display text-lg font-bold text-white tracking-tight">Procta</span>
            </Link>
            <p className="mt-4 text-sm leading-relaxed text-slate-500">
              AI-powered exam proctoring for institutions that value integrity and privacy.
            </p>
          </div>

          {/* Product */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Product</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><a href="#features" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Features</a></li>
              <li><a href="#how-it-works" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">How It Works</a></li>
              <li><a href="#use-cases" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Use Cases</a></li>
              <li><a href="#demo" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Demo</a></li>
            </ul>
          </div>

          {/* Company */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Company</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><Link to="/privacy" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Privacy Policy</Link></li>
              <li><Link to="/terms" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Terms of Service</Link></li>
              <li><a href="mailto:contact@procta.net" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Contact</a></li>
            </ul>
          </div>

          {/* Connect */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Connect</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><a href={`${APP_URL}/dashboard`} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Log In</a></li>
              <li><Link to="/signup" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Request Demo</Link></li>
              <li><a href={`${APP_URL}/download`} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Download App</a></li>
            </ul>
          </div>
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-white/[0.06] pt-8 md:flex-row">
          <p className="text-xs text-slate-600">
            &copy; {new Date().getFullYear()} Procta. All rights reserved.
          </p>
          <div className="flex items-center gap-6">
            <Link to="/privacy" className="text-xs text-slate-600 transition-colors hover:text-accent-light no-underline">Privacy</Link>
            <Link to="/terms" className="text-xs text-slate-600 transition-colors hover:text-accent-light no-underline">Terms</Link>
          </div>
        </div>
      </div>
    </footer>
  )
}
