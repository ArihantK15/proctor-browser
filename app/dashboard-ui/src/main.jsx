import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import App from './App.jsx'
import './responsive.css'

// Sentry — gated on VITE_SENTRY_DSN set at build time. No-op without it.
// replaysSessionSampleRate stays at 0 because the dashboard surfaces
// student PII (rolls, emails, exam answers) that we deliberately don't
// want replayed to a third party. Error stack traces alone are enough.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || 'production',
    release: import.meta.env.VITE_RELEASE || undefined,
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 0.0,
  })
}

const Fallback = () => (
  <div style={{ padding: 40, textAlign: 'center', maxWidth: 480, margin: '80px auto' }}>
    <h2>Something went wrong</h2>
    <p style={{ color: 'var(--text-muted)', marginTop: 12, fontSize: 13 }}>
      The error has been reported. Refresh the page to retry.
    </p>
    <button
      className="btn btn-secondary btn-sm"
      style={{ marginTop: 16 }}
      onClick={() => window.location.reload()}
    >
      Reload
    </button>
  </div>
)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Sentry.ErrorBoundary fallback={<Fallback />}>
      <App />
    </Sentry.ErrorBoundary>
  </StrictMode>,
)
