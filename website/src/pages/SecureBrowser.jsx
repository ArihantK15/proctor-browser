import { Helmet } from 'react-helmet-async'
import { Link } from 'wouter'
import { ArrowLeft, Check, Lock, Monitor, EyeOff, Cpu } from 'lucide-react'
import Navbar from '../components/Navbar'
import Footer from '../components/Footer'

/**
 * /secure-browser — owns the "secure exam browser" / "lockdown browser" query
 * space AND builds the "Procta Secure Browser (PSB)" brand term. Parallel to
 * Mettl's "MSB" and SEB ("Safe Exam Browser") landing intent.
 */
export default function SecureBrowser() {
  return (
    <div className="min-h-screen bg-navy-950">
      <Helmet>
        <title>Procta Secure Browser (PSB) — Lockdown Exam Browser for Windows & macOS | Procta</title>
        <meta name="description" content="Procta Secure Browser (PSB) is a lockdown exam browser for Windows and macOS: fullscreen lock, copy/paste and app-switch blocking, VM and remote-desktop detection, and on-device AI proctoring — no raw video leaves the student's PC." />
        <link rel="canonical" href="https://www.procta.net/secure-browser" />
        <meta property="og:title" content="Procta Secure Browser (PSB) — Lockdown Exam Browser | Procta" />
        <meta property="og:description" content="Lockdown exam browser for Windows & macOS — fullscreen lock, paste/app-switch blocking, VM & remote-desktop detection, on-device AI proctoring." />
        <meta property="og:url" content="https://www.procta.net/secure-browser" />
        <meta property="og:type" content="article" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:image" content="https://www.procta.net/og-image.png" />
      </Helmet>
      <Navbar />

      <article className="pt-32 pb-20 md:pt-44 md:pb-32">
        <div className="mx-auto max-w-4xl px-6">
          <div className="animate-fadeIn">
            <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-accent-light hover:text-accent no-underline mb-8">
              <ArrowLeft size={14} /> Back to home
            </Link>
            <span className="label-mono text-accent">Procta Secure Browser (PSB)</span>
            <h1 className="mt-3 font-display text-3xl font-bold text-white md:text-4xl lg:text-5xl leading-tight">
              Procta Secure Browser (PSB): the lockdown exam browser
            </h1>
            <p className="mt-6 text-lg text-slate-400 leading-relaxed">
              <strong className="text-white">Procta Secure Browser (PSB)</strong> is Procta's own proprietary
              lockdown exam browser — designed, built, and maintained in-house. Students install it on Windows or
              macOS to sit a proctored exam: it locks the screen to the test, blocks the ways students cheat on a
              normal browser, and runs AI proctoring on-device — so camera frames are analysed on the student's own
              PC and no raw video is ever recorded or uploaded.
            </p>
          </div>

          <div className="mt-14 grid gap-5 sm:grid-cols-2">
            <Feature icon={<Lock size={20} />} title="True lockdown"
              body="Fullscreen lock, copy/paste and right-click blocked, keyboard shortcuts and app switching disabled, screenshots and screen-sharing suppressed for the duration of the exam." />
            <Feature icon={<Monitor size={20} />} title="Environment checks"
              body="Detects virtual machines, remote-desktop tools (AnyDesk, TeamViewer, RDP), multiple monitors, and known cheat utilities — and flags or blocks the session before it starts." />
            <Feature icon={<Cpu size={20} />} title="On-device AI"
              body="Face, gaze, and object detection run inside PSB on the student's machine. Only violation events and risk scores leave the device — never the video feed. Bandwidth-light and DPDP-friendly." />
            <Feature icon={<EyeOff size={20} />} title="Privacy by design"
              body="No biometric templates stored, no video retained. PSB pairs with the student's phone as a second room camera, and answers autosave locally so a network drop never loses work." />
          </div>

          <div className="mt-14 rounded-2xl border border-white/[0.08] bg-navy-900/60 p-8">
            <h2 className="font-display text-2xl font-bold text-white">How PSB compares to a normal browser</h2>
            <ul className="mt-5 space-y-3 text-slate-300">
              {[
                'A normal browser lets students open notes, switch tabs, screen-share to a helper, or run answers through an AI in another window. PSB closes every one of those doors.',
                'Unlike cloud proctoring that streams webcam video to a server, PSB does the AI analysis locally — faster, cheaper on bandwidth, and far easier to defend under DPDP.',
                'Cross-platform: one PSB build for Windows, one for macOS. Students download once and reuse it for every exam you assign.',
              ].map((t, i) => (
                <li key={i} className="flex gap-3">
                  <Check className="mt-1 shrink-0 text-emerald-400" size={18} />
                  <span>{t}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-14 text-center">
            <h2 className="font-display text-2xl font-bold text-white">See PSB run a real exam</h2>
            <p className="mt-3 text-slate-400">Start a 14-day free trial — no credit card.</p>
            <Link to="/signup" className="mt-6 inline-flex items-center gap-2 rounded-xl bg-accent px-6 py-3 font-semibold text-navy-950 no-underline accent-glow hover:opacity-90">
              Start free trial
            </Link>
          </div>
        </div>
      </article>

      <Footer />
    </div>
  )
}

function Feature({ icon, title, body }) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-navy-900/60 p-6">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/15 text-accent">{icon}</div>
      <h3 className="mt-4 font-display text-lg font-bold text-white">{title}</h3>
      <p className="mt-2 text-sm text-slate-400 leading-relaxed">{body}</p>
    </div>
  )
}
