import { existsSync, readFileSync } from 'node:fs'

const requiredFiles = [
  'index.html',
  'vite.config.js',
  'src/App.jsx',
  'src/components/Navbar.jsx',
  'src/pages/Signup.jsx',
  'src/pages/Pricing.jsx',
]

const missing = requiredFiles.filter((path) => !existsSync(path))
if (missing.length) {
  throw new Error(`Missing website files: ${missing.join(', ')}`)
}

const app = readFileSync('src/App.jsx', 'utf8')
for (const route of ['/', '/pricing', '/signup']) {
  if (!app.includes(route)) throw new Error(`Website route ${route} is not wired in App.jsx`)
}

console.log('Website smoke test passed')
