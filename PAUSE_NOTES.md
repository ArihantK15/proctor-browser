# Procta — Paused (2026-07-17)

Development is paused. Nothing was deleted — this is a resumable pause, not a
decommission. This doc exists so a future resume doesn't require re-deriving
what was done or why.

## What's live right now

- **`app.procta.net`** (KVM4 server, root Caddy config) — shows a static
  "development is paused" holding page for every path. The real app
  (API, workers, Postgres, Redis, dolos-svc, everything) is **still running
  underneath, untouched** — Caddy just isn't routing public traffic to it
  anymore. Original routing preserved as `Caddyfile.pre-pause-backup-20260717`
  (both on the server at `/root/proctor-browser/` and in this repo at the
  root) — copy it back over `Caddyfile` and `docker exec proctor-caddy caddy
  reload --config /etc/caddy/Caddyfile` to resume exactly as it was.
- **`procta.net` / `www.procta.net`** (Vercel, `website/` directory) — same
  idea: `vercel.json`'s `buildCommand`/`outputDirectory` now bypass the real
  site build entirely and deploy only the paused page. Original config
  preserved as `website/vercel.json.pre-pause-backup` — copy it back to
  resume.

## Known issue, not fixed (low priority — site isn't expected to reopen soon)

On the Vercel-hosted marketing site, **only the root path (`/`) actually
shows the paused page** — any other path (`/signup`, `/pricing`, a random
nonexistent path, everything) returns a plain Vercel `404: NOT_FOUND`
instead. Confirmed live, not a caching artifact (a never-before-visited
random path also 404s).

What was tried:
1. A catch-all `rewrites: [{ "source": "/(.*)", "destination": "/paused.html" }]`
   while still running the real `npm run build` — didn't work because the
   site's `postbuild` step (`scripts/prerender.mjs`) prerenders every real
   route to its own actual static file, and Vercel serves an existing static
   file before ever consulting rewrites. Also, that same Puppeteer-based
   prerender step is what independently broke the real Vercel build with
   `TargetCloseError: Protocol error (Target.createTarget): Target closed` —
   Vercel's build sandbox doesn't run Puppeteer/Chromium the same way GitHub
   Actions' runners do.
2. `buildCommand`/`outputDirectory` bypassing the real build entirely (just
   copies `paused.html` to `index.html` in a fresh output dir) — this fixed
   the build failure and made `/` work, but a rewrite to `/index.html` still
   didn't catch other paths for reasons not yet diagnosed (Vercel's rewrite
   destination syntax with `cleanUrls: true` may need `/` instead of
   `/index.html`, or something else entirely — not investigated further).

**Next attempt, not yet tried**: skip rewrites entirely and instead output
the same content as *both* `index.html` and `404.html` in the build output —
Vercel natively serves `404.html` for any unmatched static path, which
sidesteps the rewrite-matching question altogether. Untested.

**Practical impact of leaving this unfixed**: anyone landing on `/` sees the
correct paused message. Anyone with a deep link (`/signup`, an old bookmark,
a search result) sees a bare Vercel 404 instead of the paused message — worse
UX, but not a functional or security problem, and not worth more time on a
site that's paused indefinitely.

## CI health (fixed, unrelated to the pause itself)

Three real, pre-existing CI bugs were found and fixed while working on this
(all on `main`, all still relevant if development resumes):
- `numpy` (`dadf4a65`) and `opencv-python` (`d2a667e9`) were missing from
  `requirements.lock` — `tests/test_proctor.py` (added just before this
  session) imports `cv2`/`numpy` at module level via `proctor.py`, but
  `requirements.lock` only compiles from `requirements.txt`, and both were
  only declared in `requirements-proctor.txt` (the Electron app's local
  runtime deps, never installed in CI). Confirmed `proctor.py`'s own
  module-level imports really are just `cv2`/`numpy` (+ stdlib/`requests`) -
  the heavier ML stack (`uniface`/`onnxruntime`/`insightface`/`vosk`/
  `sounddevice`) is lazy-imported inside functions and correctly does not
  belong in `requirements.txt`.
- `pillow`/`httplib2` (`e8ee4d38`) had known CVEs (`pip-audit` gate) - bumped
  to patched versions (`pillow>=12.3.0`, explicit `httplib2>=0.32.0` floor).

## Repo state

- `main` is otherwise untouched — no application code changed, only
  deploy/CI config plus the two pause commits.
- Not yet done (deliberately deferred, not urgent): full KVM4 decommission
  prep (verified fresh backup, `.env`/secrets extraction, resume-doc
  finalization beyond this file, making this repo private). Revisit when
  actually ready to repurpose the server for something else - right now it's
  just idle with the app stopped from receiving public traffic.
