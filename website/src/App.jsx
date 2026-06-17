import { Route, Switch, useLocation } from 'wouter'
import { createElement, lazy, Suspense } from 'react'
import { motion, useReducedMotion } from 'framer-motion'

import SmoothScroll from './components/SmoothScroll'
import Landing from './pages/Landing'

const Pricing = lazy(() => import('./pages/Pricing'))
const LtiSetup = lazy(() => import('./pages/LtiSetup'))

const Signup = lazy(() => import('./pages/Signup'))
const Privacy = lazy(() => import('./pages/Privacy'))
const Trust = lazy(() => import('./pages/Trust'))
const Terms = lazy(() => import('./pages/Terms'))
const Features = lazy(() => import('./pages/Features'))
const HowItWorks = lazy(() => import('./pages/HowItWorks'))
const Blog = lazy(() => import('./pages/Blog'))
const BlogAiVsTraditional = lazy(() => import('./pages/BlogAiVsTraditional'))
const BlogCheatingPrevention = lazy(() => import('./pages/BlogCheatingPrevention'))
const BlogDPDPCompliance = lazy(() => import('./pages/BlogDPDPCompliance'))
const Download = lazy(() => import('./pages/Download'))
const Register = lazy(() => import('./pages/Register'))
const MigrateFromMettl = lazy(() => import('./pages/MigrateFromMettl'))
const CompareTalview = lazy(() => import('./pages/CompareTalview'))
const CompareProctortrack = lazy(() => import('./pages/CompareProctortrack'))
const CompareHonorlock = lazy(() => import('./pages/CompareHonorlock'))
const CoachingInstitutes = lazy(() => import('./pages/CoachingInstitutes'))
const SecureBrowser = lazy(() => import('./pages/SecureBrowser'))
const NotFound = lazy(() => import('./pages/NotFound'))

function RouteFallback() {
  // A lazy route's chunk is downloading. Show branded content instead of a
  // bare navy void so a slow load never reads as a broken page. The spinner
  // keyframes live in index.css (route-fallback-spin).
  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#0F1629',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '20px',
        color: '#e2e8f0',
        fontFamily: 'system-ui, sans-serif',
      }}
      aria-busy="true"
      aria-live="polite"
    >
      <img src="/logo.svg" alt="Procta" width={140} height={40} style={{ opacity: 0.95 }} />
      <div
        style={{
          width: 28,
          height: 28,
          border: '3px solid rgba(226,232,240,0.25)',
          borderTopColor: '#e2e8f0',
          borderRadius: '50%',
          animation: 'route-fallback-spin 0.8s linear infinite',
        }}
      />
      <div style={{ fontSize: 14, color: '#94a3b8' }}>Loading Procta…</div>
    </div>
  )
}

function LazyRoute({ Component: RouteComponent }) {
  return (
    <Suspense fallback={<RouteFallback />}>
      {createElement(RouteComponent)}
    </Suspense>
  )
}

export default function App() {
  const [location] = useLocation()
  const reduced = useReducedMotion()
  return (
    <>
      <SmoothScroll />
      <a href="#main-content" className="skip-to-content" tabIndex={1}>
        Skip to content
      </a>
      <div id="main-content" tabIndex={-1}>
        {/* Enter-only page transition: the wrapper re-mounts on every route
            change (key=location) and fades/rises the new page in. No exit
            animation — avoids AnimatePresence + Suspense fallback flashes. */}
        <motion.div
          key={location}
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: 10 }}
          animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0 }}
          transition={{ duration: reduced ? 0.2 : 0.28, ease: [0.23, 1, 0.32, 1] }}
        >
        <Switch>
          <Route path="/" component={Landing} />
          <Route path="/pricing"><LazyRoute Component={Pricing} /></Route>
          <Route path="/lti-setup"><LazyRoute Component={LtiSetup} /></Route>
          <Route path="/signup"><LazyRoute Component={Signup} /></Route>
          <Route path="/privacy"><LazyRoute Component={Privacy} /></Route>
          <Route path="/trust"><LazyRoute Component={Trust} /></Route>
          <Route path="/terms"><LazyRoute Component={Terms} /></Route>
          <Route path="/features"><LazyRoute Component={Features} /></Route>
          <Route path="/how-it-works"><LazyRoute Component={HowItWorks} /></Route>
          <Route path="/blog"><LazyRoute Component={Blog} /></Route>
          <Route path="/blog/ai-proctoring-vs-traditional-proctoring"><LazyRoute Component={BlogAiVsTraditional} /></Route>
          <Route path="/blog/online-exam-cheating-prevention-ai-proctoring"><LazyRoute Component={BlogCheatingPrevention} /></Route>
          <Route path="/blog/dpdp-act-compliance-online-proctoring-indian-universities"><LazyRoute Component={BlogDPDPCompliance} /></Route>
          <Route path="/download"><LazyRoute Component={Download} /></Route>
          <Route path="/register"><LazyRoute Component={Register} /></Route>
          {/* SEO landing pages targeted at "X alternative" / "X vs Procta" search traffic. */}
          <Route path="/migrate-from-mettl"><LazyRoute Component={MigrateFromMettl} /></Route>
          <Route path="/compare/talview-vs-procta"><LazyRoute Component={CompareTalview} /></Route>
          <Route path="/compare/proctortrack-vs-procta"><LazyRoute Component={CompareProctortrack} /></Route>
          <Route path="/compare/honorlock-vs-procta"><LazyRoute Component={CompareHonorlock} /></Route>
          {/* SEO landing pages — ICP + brandable PSB term (gap #59 / SEO pass). */}
          <Route path="/coaching"><LazyRoute Component={CoachingInstitutes} /></Route>
          <Route path="/secure-browser"><LazyRoute Component={SecureBrowser} /></Route>
          <Route><LazyRoute Component={NotFound} /></Route>
        </Switch>
        </motion.div>
      </div>
    </>
  )
}
