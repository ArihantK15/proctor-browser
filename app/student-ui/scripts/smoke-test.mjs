import { existsSync, readFileSync } from 'node:fs'

const requiredFiles = [
  'src/main.jsx',
  'src/hooks/useTurnstile.js',
]

const missing = requiredFiles.filter((path) => !existsSync(path))
if (missing.length) {
  throw new Error(`Missing student UI files: ${missing.join(', ')}`)
}

const app = readFileSync('src/main.jsx', 'utf8')
if (!app.includes('credentials:')) throw new Error('Student UI auth must send cookie credentials')
const forbiddenTokenWrite = "localStorage.setItem('" + 'procta_student_' + "token'"
if (app.includes(forbiddenTokenWrite)) {
  throw new Error('Student UI must not persist access tokens in localStorage')
}
if (!app.includes('fetchWithTimeout')) throw new Error('Student UI should retain fetch timeouts')

console.log('Student UI smoke test passed')
