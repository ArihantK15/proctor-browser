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
              {/* Brand chip — same shield+eye mark as Navbar + favicon. */}
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent accent-glow">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path d="M4 2 H12 Q13.5 2 13.5 3.5 V8 Q13.5 12 8 14 Q2.5 12 2.5 8 V3.5 Q2.5 2 4 2 Z"
                        fill="none" stroke="white" strokeWidth="1.2" strokeLinejoin="round"/>
                  <circle cx="8" cy="8" r="1.5" fill="white"/>
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
              <li><Link to="/privacy" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Privacy Policy</Link></li>
              <li><Link to="/terms" className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Terms of Service</Link></li>
            </ul>
          </div>

          {/* Connect */}
          <div>
            <h4 className="mb-4 label-mono text-slate-500">Connect</h4>
            <ul className="space-y-2.5 list-none p-0">
              <li><a href={`${APP_URL}/dashboard`} className="text-sm text-slate-400 transition-colors hover:text-accent-light no-underline">Log In</a></li>
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
