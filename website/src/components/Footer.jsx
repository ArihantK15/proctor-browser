import { Link } from 'wouter'
import { APP_URL } from '../config'

export default function Footer() {
  return (
    <footer className="border-t border-white/[0.06] bg-navy-950">
      <div className="mx-auto max-w-7xl px-6 py-12 md:py-16">
        <div className="grid gap-8 md:grid-cols-4">
          {/* Brand */}
          <div className="md:col-span-1">
            <Link to="/" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} className="flex items-center gap-2.5 no-underline">
              {/* Brand chip — same negative-space "P" mark as Navbar + favicon. */}
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
                <svg width="18" height="18" viewBox="0 0 64 64" fill="none" aria-hidden="true">
                  <path d="M20 15H40C45.523 15 50 19.477 50 25V25C50 30.523 45.523 35 40 35H27.5V50H20V15ZM27.5 22.5V27.5H40C41.38 27.5 42.5 26.38 42.5 25V25C42.5 23.62 41.38 22.5 40 22.5H27.5Z" fill="white"/>
                </svg>
              </div>
              <span className="font-display text-lg font-bold text-white tracking-tight">Procta</span>
            </Link>
            {/* Brand slogan — paired with the wordmark on every page footer. */}
            <p className="mt-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-accent-light/80">
              Remote exams. Real results.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-slate-500">
              AI-powered exam proctoring for institutions that value integrity and privacy.
            </p>
          </div>

          {/* Product */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Product</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><Link to="/features" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Features</Link></li>
              <li><Link to="/how-it-works" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">How It Works</Link></li>
              <li><Link to="/secure-browser" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Secure Browser (PSB)</Link></li>
              <li><Link to="/coaching" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">For Coaching Institutes</Link></li>
              <li><a href="/#use-cases" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Use Cases</a></li>
              <li><a href="/#demo" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Demo</a></li>
            </ul>
          </div>

          {/* Resources */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Resources</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><Link to="/blog" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Blog</Link></li>
              <li><Link to="/blog/ai-proctoring-vs-traditional-proctoring" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">AI vs Traditional Proctoring</Link></li>
              <li><Link to="/blog/online-exam-cheating-prevention-ai-proctoring" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Online Exam Cheating Prevention</Link></li>
              <li><Link to="/blog/dpdp-act-compliance-online-proctoring-indian-universities" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">DPDP Act & Proctoring</Link></li>
              <li><Link to="/trust" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Trust Center</Link></li>
              <li>
                <a href="https://status.procta.net" target="_blank" rel="noopener noreferrer" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                  <span aria-hidden="true" style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#10b981', flexShrink: 0 }} />
                  System Status
                </a>
              </li>
              <li><Link to="/privacy" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Privacy Policy</Link></li>
              <li><Link to="/terms" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Terms of Service</Link></li>
            </ul>
          </div>

          {/* Connect */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Connect</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><a href={`${APP_URL}/login`} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Log In</a></li>
              <li><Link to="/signup" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Request Demo</Link></li>
              <li><a href={`${APP_URL}/download`} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Download App</a></li>
              <li><a href="mailto:support@procta.net" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">support@procta.net</a></li>
            </ul>
          </div>
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-white/[0.06] pt-8 md:flex-row">
          <p className="text-xs text-slate-600">
            &copy; {new Date().getFullYear()} Procta. All rights reserved.
          </p>
          <div className="flex items-center gap-6">
            <Link to="/privacy" className="text-xs text-slate-600 transition-colors hover:text-accent-light no-underline">Privacy</Link>
            <Link to="/trust" className="text-xs text-slate-600 transition-colors hover:text-accent-light no-underline">Trust</Link>
            <Link to="/terms" className="text-xs text-slate-600 transition-colors hover:text-accent-light no-underline">Terms</Link>
          </div>
        </div>
      </div>
    </footer>
  )
}
