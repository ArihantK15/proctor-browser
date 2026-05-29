import { Route, Switch } from 'wouter'
import { createElement, lazy, Suspense } from 'react'

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
const NotFound = lazy(() => import('./pages/NotFound'))

function RouteFallback() {
  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#0d1117',
      }}
      aria-busy="true"
      aria-live="polite"
    />
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
  return (
    <>
      <a href="#main-content" className="skip-to-content" tabIndex={1}>
        Skip to content
      </a>
      <div id="main-content" tabIndex={-1}>
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
          <Route><LazyRoute Component={NotFound} /></Route>
        </Switch>
      </div>
    </>
  )
}
