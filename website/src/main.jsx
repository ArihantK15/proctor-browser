import { StrictMode } from 'react'
import { createRoot, hydrateRoot } from 'react-dom/client'
import { Router } from 'wouter'
import { HelmetProvider } from 'react-helmet-async'
import './index.css'
import App from './App.jsx'

// When the page was prerendered (puppeteer postbuild → real HTML inside
// #root), hydrate on top of the existing DOM so users don't see a flash
// of prerendered content getting blown away and re-rendered. On the dev
// server (empty shell), fall back to createRoot.
const rootEl = document.getElementById('root')
const tree = (
  <StrictMode>
    <HelmetProvider>
      <Router>
        <App />
      </Router>
    </HelmetProvider>
  </StrictMode>
)

if (rootEl.hasChildNodes()) {
  hydrateRoot(rootEl, tree)
} else {
  createRoot(rootEl).render(tree)
}
