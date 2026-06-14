#!/usr/bin/env node

import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs'
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
    // Previously this silently returned (exit 0), so a build where puppeteer
    // failed to launch shipped an empty-#root CSR shell — the marketing site's
    // worst failure mode (blank navy page until JS executes). Make it LOUD:
    // fail the build unless the operator explicitly opts out via SKIP_PRERENDER.
    if (process.env.SKIP_PRERENDER === '1') {
      console.warn(`    ⚠ Puppeteer unavailable; SKIP_PRERENDER=1 set — shipping CSR shell on purpose: ${err.message}`)
      return
    }
    console.error(`    ✗ Puppeteer could not launch and SKIP_PRERENDER is not set: ${err.message}`)
    console.error('      Refusing to ship an empty #root shell (blank-page-on-slow-JS risk).')
    console.error('      Fix Chrome/puppeteer in the build env, or set SKIP_PRERENDER=1 to override.')
    process.exit(1)
  }

  const server = await startServer(DIST)
  const port = server.address().port
  console.log(`Starting static server on :${port}...`)

  const base = `http://localhost:${port}`

  // The pristine CSR shell straight from `vite build` (empty #root). serve-handler
  // has no SPA fallback, so a request for /pricing — which has no file yet —
  // returns a 404 page, and the old prerender captured THAT (empty #root) for
  // every lazy sub-route. We instead seed each route's path with this shell
  // before navigating, so serve-handler serves a real app shell there, the app
  // boots, wouter renders that route, its lazy chunk loads, and we capture the
  // actual page. Read once, BEFORE the loop overwrites dist/index.html.
  const SHELL = readFileSync(join(DIST, 'index.html'), 'utf-8')

  try {
    for (const route of ROUTES) {
      const url = `${base}${route}`
      console.log(`  Prerendering ${route}...`)

      const filePath = route === '/'
        ? join(DIST, 'index.html')
        : join(DIST, route.slice(1), 'index.html')

      // Seed this exact path with the pristine shell so serve-handler serves the
      // app (not a 404) when puppeteer navigates here.
      mkdirSync(dirname(filePath), { recursive: true })
      writeFileSync(filePath, SHELL, 'utf-8')

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

        // Wait for the route's REAL content to render: the Suspense fallback
        // (RouteFallback sets aria-busy) is gone and #root has substantial text.
        // This is what makes lazy sub-routes (Pricing, Trust, /compare/*, …)
        // capture their actual page instead of the loading spinner. Soft-fail so
        // a single slow/broken route never aborts the whole prerender.
        try {
          await page.waitForFunction(() => {
            const root = document.getElementById('root')
            if (!root) return false
            if (root.querySelector('[aria-busy="true"]')) return false
            return root.innerText.trim().length > 200
          }, { timeout: 12000 })
        } catch {
          console.log(`    … ${route} content didn't settle in time — capturing as-is`)
        }

        // Brief extra settle for fonts/images after content renders.
        await page.evaluate(() => new Promise((r) => setTimeout(r, 300)))

        const html = await page.content()
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

  // Final guard: the homepage #root MUST contain prerendered markup. This
  // catches the case where puppeteer launched but every route errored (each is
  // caught per-route above), which would otherwise leave the original empty
  // index.html in place and ship a blank shell.
  const homepage = readFileSync(join(DIST, 'index.html'), 'utf-8')
  if (/<div id="root">\s*<\/div>/.test(homepage)) {
    if (process.env.SKIP_PRERENDER === '1') {
      console.warn('    ⚠ Homepage #root is empty after prerender, but SKIP_PRERENDER=1 — continuing.')
    } else {
      console.error('    ✗ Homepage #root is empty after prerender — refusing to ship a blank shell.')
      console.error('      Every route likely errored above; check the logs. Set SKIP_PRERENDER=1 to override.')
      process.exit(1)
    }
  }

  console.log('\nPrerendering complete.')
  process.exit(0)
}

prerender()
