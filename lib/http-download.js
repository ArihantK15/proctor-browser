const fs = require('fs');
const https = require('https');
const http = require('http');

// Retry wrapper. CI runners intermittently drop the connection mid-stream
// ("Error: socket hang up" / ECONNRESET) on larger CDN/GitHub-release
// downloads — retry with backoff + a fresh dest file so a flaky network drop
// doesn't fail the whole operation. Shared by bundle-python.js (build time)
// and the runtime-asset fetch (lib/runtime-assets.js, app time) so this
// redirect/retry logic exists in exactly one place.
async function download(url, dest, attempts = 4) {
  for (let i = 1; i <= attempts; i++) {
    try {
      return await downloadOnce(url, dest);
    } catch (e) {
      const last = i >= attempts;
      console.warn(`[dl] attempt ${i}/${attempts} failed for ${url}: ${e.message}` +
        (last ? '' : ` — retrying in ${i * 2}s`));
      try { fs.rmSync(dest, { force: true }); } catch { /* nothing to clean */ }
      if (last) throw e;
      await new Promise(r => setTimeout(r, i * 2000));
    }
  }
}

function downloadOnce(url, dest) {
  // Only opens the destination stream on the final 200 — GitHub release
  // URLs redirect (302 -> objects.githubusercontent.com), so opening the
  // file up front and closing it on the first redirect left an empty file.
  // Handles the full redirect set + a depth cap.
  return new Promise((resolve, reject) => {
    const follow = (u, depth = 0) => {
      if (depth > 8) { reject(new Error(`Too many redirects — ${url}`)); return; }
      const client = String(u).startsWith('http://') ? http : https;
      client.get(u, (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
          res.resume(); // drain so the socket frees
          // Location may be relative (some servers, and this module's own
          // test server, send just a path) — resolve it against the URL we
          // just requested. Real call sites (GitHub/python.org release
          // redirects) already send absolute Location headers, so this is a
          // no-op for them; new URL(absolute, base) just returns absolute.
          const next = new URL(res.headers.location, u).href;
          follow(next, depth + 1);
          return;
        }
        if (res.statusCode !== 200) {
          res.resume();
          reject(new Error(`HTTP ${res.statusCode} — ${u}`));
          return;
        }
        const file = fs.createWriteStream(dest);
        res.pipe(file);
        file.on('finish', () => file.close(resolve));
        file.on('error', reject);
      }).on('error', reject);
    };
    follow(url);
  });
}

module.exports = { download };
