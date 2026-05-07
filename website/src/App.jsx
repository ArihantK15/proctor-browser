import { Routes, Route } from 'react-router-dom'
import { lazy, Suspense } from 'react'

// Landing is the only route 90%+ of visitors hit, so it stays in the
// initial bundle for instant first paint. Signup, Privacy, Terms are
// rare-path routes that we lazy-load — they only ship JS to the
// browsers that actually navigate to them.
import Landing from './pages/Landing'

const Signup = lazy(() => import('./pages/Signup'))
const Privacy = lazy(() => import('./pages/Privacy'))
const Terms = lazy(() => import('./pages/Terms'))

// Suspense fallback: a tiny inline placeholder. We keep the page
// background so the transition feels like a quick skeleton rather
// than a flash of white. Routes are small (≤30 KB each gzipped) so
// the fallback is on screen for ~50–200 ms on any decent connection.
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
      <Route
        path="/signup"
        element={
          <Suspense fallback={<RouteFallback />}>
            <Signup />
          </Suspense>
        }
      />
      <Route
        path="/privacy"
        element={
          <Suspense fallback={<RouteFallback />}>
            <Privacy />
          </Suspense>
        }
      />
      <Route
        path="/terms"
        element={
          <Suspense fallback={<RouteFallback />}>
            <Terms />
          </Suspense>
        }
      />
    </Routes>
  )
}
