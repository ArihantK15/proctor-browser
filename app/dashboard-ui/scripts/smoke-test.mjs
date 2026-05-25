import { existsSync, readFileSync } from 'node:fs'

const requiredFiles = [
  'src/App.jsx',
  'src/lib/auth.jsx',
  'src/config.js',
  'src/panels/LiveSessionsPanel.jsx',
  'src/panels/BillingPanel.jsx',
  'src/panels/MembersPanel.jsx',
]

const missing = requiredFiles.filter((path) => !existsSync(path))
if (missing.length) {
  throw new Error(`Missing dashboard UI files: ${missing.join(', ')}`)
}

const auth = readFileSync('src/lib/auth.jsx', 'utf8')
if (!auth.includes('credentials:')) throw new Error('Dashboard auth must send cookie credentials')
const forbiddenTokenWrite = "localStorage.setItem('" + 'procta_' + "token'"
if (auth.includes(forbiddenTokenWrite)) {
  throw new Error('Dashboard auth must not persist access tokens in localStorage')
}
if (!auth.includes('ensureCsrfToken')) throw new Error('Dashboard auth must keep CSRF refresh path')

console.log('Dashboard UI smoke test passed')
