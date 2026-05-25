import { existsSync, readFileSync } from 'node:fs'

const requiredFiles = [
  'main.js',
  'preload.js',
  'lobby_preload.js',
  'setup-preload.js',
  'renderer/index.html',
  'app/static/student.html',
  'app/static/theme.css',
  'proctor.py',
  'behavioral_analysis.py',
]

const missing = requiredFiles.filter((path) => !existsSync(path))
if (missing.length) {
  throw new Error(`Missing Electron runtime files: ${missing.join(', ')}`)
}

const pkg = JSON.parse(readFileSync('package.json', 'utf8'))
const files = pkg.build?.files || []
if (!pkg.build?.appId) throw new Error('Electron build.appId is missing')
if (!files.includes('renderer/**/*')) throw new Error('Electron build.files must include renderer/**/*')
if (!files.includes('app/static/student.html')) throw new Error('Electron build.files must include the student shell')
if (!pkg.build?.mac || !pkg.build?.win) throw new Error('Electron mac/win build targets are missing')

console.log('Electron smoke test passed')
