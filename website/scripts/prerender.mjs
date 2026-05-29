#!/usr/bin/env node

import { writeFileSync, mkdirSync, existsSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import http from 'http'
import handler from 'serve-handler'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DIST = join(__dirname, '..', 'dist')

const ROUTES = [
  '/',
  '/features',
  '/how-it-works',
  '/pricing',
  '/signup',
  '/download',
  '/register',
  '/trust',
  '/privacy',
  '/terms',
  '/lti-setup',
  '/migrate-from-mettl',
  '/compare/talview-vs-procta',
  '/compare/proctortrack-vs-procta',
  '/compare/honorlock-vs-procta',
  '/blog',
  '/blog/ai-proctoring-vs-traditional-proctoring',
  '/blog/online-exam-cheating-prevention-ai-proctoring',
  '/blog/dpdp-act-compliance-online-proctoring-indian-universities',
]

async function startServer(dir) {
  const server = http.createServer((req, res) => {
    try {
      handler(req, res, { public: dir })
    } catch {
      res.writeHead(500)
      res.end()
    }
  })
  return new Promise((resolve) => {
    server.listen(0, () => resolve(server))
  })
}

async function prerender() {
  if (!existsSync(join(DIST, 'index.html'))) {
    console.error('dist/index.html not found. Run `npm run build` first.')
    process.exit(1)
  }

  let browser
  try {
    const { default: puppeteer } = await import('puppeteer')
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    })
  } catch (err) {
    console.error(`    ✗ Puppeteer unavailable, skipping prerender: ${err.message}`)
    return
  }

  const server = await startServer(DIST)
  const port = server.address().port
  console.log(`Starting static server on :${port}...`)

  const base = `http://localhost:${port}`

  try {
    for (const route of ROUTES) {
      const url = `${base}${route}`
      console.log(`  Prerendering ${route}...`)
      const page = await browser.newPage()
      await page.setViewport({ width: 1280, height: 720 })

      try {
        // Try networkidle0 first; fall back to domcontentloaded if it hangs
        try {
          await page.goto(url, { waitUntil: 'networkidle0', timeout: 15000 })
        } catch {
          console.log(`    … ${route} timed out on networkidle0, retrying with domcontentloaded`)
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 })
        }

        // Extra settle time for async rendering
        await page.evaluate(() => new Promise((r) => {
          if (document.readyState === 'complete') setTimeout(r, 1000)
          else window.addEventListener('load', () => setTimeout(r, 1000))
        }))

        const html = await page.content()

        const filePath = route === '/'
          ? join(DIST, 'index.html')
          : join(DIST, route.slice(1), 'index.html')

        mkdirSync(dirname(filePath), { recursive: true })
        writeFileSync(filePath, html, 'utf-8')
        const kb = (Buffer.byteLength(html, 'utf-8') / 1024).toFixed(1)
        console.log(`    ✓ ${route} (${kb} KB)`)
      } catch (err) {
        console.error(`    ✗ ${route}: ${err.message}`)
      } finally {
        await page.close()
      }
    }
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }

  console.log('\nPrerendering complete.')
  process.exit(0)
}

prerender()
