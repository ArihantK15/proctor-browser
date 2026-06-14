const { join } = require('path')

// Keep Chromium INSIDE node_modules so Vercel's build cache persists it across
// deploys. Puppeteer's default cache (~/.cache/puppeteer) is NOT part of the
// cached build env, so a cache-restored install reports "up to date", skips the
// Chromium-download postinstall, and prerender's puppeteer.launch() then fails
// — breaking the deploy. node_modules IS cached, so the binary survives here.
module.exports = {
  cacheDirectory: join(__dirname, 'node_modules', '.cache', 'puppeteer'),
}
