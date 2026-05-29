import { existsSync, readFileSync } from 'node:fs'

const requiredFiles = [
  'main.js',
  'preload.js',
  'lobby_preload.js',
  'setup-preload.js',
  'renderer/index.html',
  'app/static/student.html',
  'app/static/theme.css',
  'app/static/_safe.js',
  'app/static/student-app.js',
  'proctor.py',
  'behavioral_analysis.py',
  'audio_processor.py',
  'scripts/download_audio_models.py',
  'requirements-proctor.txt',
  'config.js',
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
if (!files.includes('app/static/_safe.js')) throw new Error('Electron build.files must include _safe.js for the lobby')
if (!files.includes('app/static/student-app.js')) throw new Error('Electron build.files must include student-app.js for the lobby')
if (!pkg.build?.mac || !pkg.build?.win) {
  throw new Error('Electron mac/win build targets are missing')
}
if (!pkg.build?.electronFuses) {
  throw new Error('Electron fuses block is missing — security hardening regressed')
}

// PIP_PACKAGES ⊇ requirements-proctor.txt guard. A package listed in
// requirements but NOT in PIP_PACKAGES means a student install diverges
// from the documented dev install — exactly the bug we hit when vosk +
// python_speech_features were added to requirements-proctor.txt and
// the student installer kept omitting them.
const configJs = readFileSync('config.js', 'utf8')
const pipMatch = configJs.match(/const PIP_PACKAGES = \[([\s\S]*?)\]/)
if (!pipMatch) {
  throw new Error('PIP_PACKAGES not found in config.js — student installer will install nothing')
}
// Strip JS line-comments BEFORE splitting on commas, otherwise a
// pattern like "// note\n  'vosk'," lands in the same fragment and
// our package-name parser thinks the whole fragment is a comment.
const pipBody = pipMatch[1].replace(/\/\/[^\n]*/g, '')
const pipPkgs = new Set(
  pipBody
    .split(',')
    .map(s => s.trim().replace(/^['"]/, '').replace(/['"]$/, '').replace(/_/g, '-'))
    .filter(Boolean)
)
const reqLines = readFileSync('requirements-proctor.txt', 'utf8').split('\n')
const reqPkgs = reqLines
  .map(l => l.trim())
  .filter(l => l && !l.startsWith('#'))
  .map(l => l.split(/[<>=!~]/)[0].trim().toLowerCase().replace(/_/g, '-'))
const missingFromInstaller = reqPkgs.filter(p => !pipPkgs.has(p))
if (missingFromInstaller.length) {
  throw new Error(
    `PIP_PACKAGES is missing ${missingFromInstaller.join(', ')} — student installer will diverge ` +
    `from requirements-proctor.txt. Add these to config.js#PIP_PACKAGES.`
  )
}

console.log('Electron smoke test passed')
