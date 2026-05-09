import { Routes, Route } from 'react-router-dom'
import { lazy, Suspense } from 'react'

import Landing from './pages/Landing'

const Signup = lazy(() => import('./pages/Signup'))
const Privacy = lazy(() => import('./pages/Privacy'))
const Terms = lazy(() => import('./pages/Terms'))
const Features = lazy(() => import('./pages/Features'))
const HowItWorks = lazy(() => import('./pages/HowItWorks'))
const Blog = lazy(() => import('./pages/Blog'))
const BlogAiVsTraditional = lazy(() => import('./pages/BlogAiVsTraditional'))
const BlogCheatingPrevention = lazy(() => import('./pages/BlogCheatingPrevention'))
const BlogDPDPCompliance = lazy(() => import('./pages/BlogDPDPCompliance'))

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

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/signup" element={<Suspense fallback={<RouteFallback />}><Signup /></Suspense>} />
      <Route path="/privacy" element={<Suspense fallback={<RouteFallback />}><Privacy /></Suspense>} />
      <Route path="/terms" element={<Suspense fallback={<RouteFallback />}><Terms /></Suspense>} />
      <Route path="/features" element={<Suspense fallback={<RouteFallback />}><Features /></Suspense>} />
      <Route path="/how-it-works" element={<Suspense fallback={<RouteFallback />}><HowItWorks /></Suspense>} />
      <Route path="/blog" element={<Suspense fallback={<RouteFallback />}><Blog /></Suspense>} />
      <Route path="/blog/ai-proctoring-vs-traditional-proctoring" element={<Suspense fallback={<RouteFallback />}><BlogAiVsTraditional /></Suspense>} />
      <Route path="/blog/online-exam-cheating-prevention-ai-proctoring" element={<Suspense fallback={<RouteFallback />}><BlogCheatingPrevention /></Suspense>} />
      <Route path="/blog/dpdp-act-compliance-online-proctoring-indian-universities" element={<Suspense fallback={<RouteFallback />}><BlogDPDPCompliance /></Suspense>} />
    </Routes>
  )
}
