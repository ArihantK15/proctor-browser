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
              {/* Brand chip — same shield+eye+crosshair mark as Navbar + favicon. */}
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
                <svg width="18" height="18" viewBox="0 0 64 64" fill="none" aria-hidden="true">
                  <path d="M19 11 L45 11 Q51 11 51 17 L51 32 Q51 49 32 56 Q13 49 13 32 L13 17 Q13 11 19 11 Z"
                        fill="white" fillOpacity="0.18" stroke="white" strokeWidth="2.4"/>
                  <circle cx="32" cy="32" r="4.8" fill="white"/>
                  <line x1="32" y1="22" x2="32" y2="26" stroke="white" strokeWidth="1.8" strokeOpacity="0.85"/>
                  <line x1="32" y1="38" x2="32" y2="42" stroke="white" strokeWidth="1.8" strokeOpacity="0.85"/>
                  <line x1="22" y1="32" x2="26" y2="32" stroke="white" strokeWidth="1.8" strokeOpacity="0.85"/>
                  <line x1="38" y1="32" x2="42" y2="32" stroke="white" strokeWidth="1.8" strokeOpacity="0.85"/>
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
