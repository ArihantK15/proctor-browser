# Contributing to Procta

Thanks for considering a contribution. This file covers everything
you need to run Procta locally and send a clean PR. If anything's
out of date, file an issue — keeping this accurate is on us.

> **Looking for the security policy?** See [`SECURITY.md`](SECURITY.md)
> for how to report vulnerabilities (do **not** open public issues
> for security bugs).

## Table of contents

- [Project shape](#project-shape)
- [Prerequisites](#prerequisites)
- [Local setup](#local-setup)
- [Running the backend API](#running-the-backend-api)
- [Running the React dashboards](#running-the-react-dashboards)
- [Running the Electron exam client](#running-the-electron-exam-client)
- [Running the marketing site](#running-the-marketing-site)
- [Tests](#tests)
- [Pre-commit hooks](#pre-commit-hooks)
- [Commit signing (recommended)](#commit-signing-recommended)
- [Commit style](#commit-style)
- [Branch model & PRs](#branch-model--prs)
- [Code style](#code-style)
- [License & DCO](#license--dco)

## Project shape

| Path | What |
|---|---|
| `app/` | FastAPI backend (auth, exams, proctoring, billing). |
| `app/dashboard-ui/` | Vite + React admin dashboard. |
| `app/student-ui/` | Vite + React student dashboard. |
| `app/static/` | Built React bundles + legacy vanilla JS surfaces. |
| `renderer/` | Electron renderer pages (lobby, exam, phone-cam). |
| `main.js`, `preload.js`, `lib/` | Electron main process. |
| `website/` | Marketing site (Vite + React). |
| `weights/` | ML model artefacts (gaze, head pose). Migrating to Git LFS. |
| `migrations/` | Forward-only SQL migrations against Supabase / Postgres. |
| `tests/` | Pytest test suite (601 tests, run in <15 s). |
| `.github/workflows/` | CI: pytest, Semgrep, pip-audit, docker-smoke, deploy, CodeQL. |

## Prerequisites

- **Python 3.11** (3.12 not yet validated; 3.10 has type-syntax issues).
- **Node 20+** and **npm 10+**.
- **Git LFS** — see [LFS-tracked assets](#git-lfs) below.
- **Docker + docker-compose** if you want to run the full stack
  (Postgres + Redis + API) the way prod does.

Optional but very nice:
- `direnv` so `.envrc` switches your venv automatically.
- `pre-commit` (Python pkg) — set up below.

## Local setup

```bash
git clone https://github.com/ArihantK15/proctor-browser.git
cd proctor-browser

# Backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Electron + root tooling
npm install

# React dashboards (each Vite app is independent)
( cd app/dashboard-ui && npm install )
( cd app/student-ui && npm install )
( cd website && npm install )

# Copy + edit env
cp .env.example .env
$EDITOR .env   # fill in SUPABASE_URL, RAZORPAY_KEY, etc.
```

### Git LFS

The 45 MB ONNX gaze model lives in Git LFS. Install once per machine
before cloning (or before first checkout if you forgot):

```bash
brew install git-lfs   # or: apt install git-lfs / choco install git-lfs
git lfs install
# If you cloned before installing LFS:
git lfs fetch --all
git lfs checkout
```

Verify with `git lfs ls-files` — you should see
`weights/resnet18_gaze.onnx` listed.

## Running the backend API

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8080
```

Hot-reload picks up Python changes; the React dashboards are served
as static bundles from `app/static/*-react/`, so changes there need
a Vite rebuild (see next section).

For the prod-like docker-compose stack:

```bash
docker compose up --build
```

## Running the React dashboards

Each dashboard is a separate Vite app that builds into `app/static/`:

```bash
cd app/dashboard-ui
npm run dev    # hot-reload at http://localhost:5173
npm run build  # writes to ../static/dashboard-react/
```

Same shape for `app/student-ui/`. The `npm run build` output is what
the FastAPI server actually serves, so commit it (we don't run Vite
in production).

## Running the Electron exam client

```bash
npm start            # launches Electron pointing at app.procta.net
npm run start:local  # if you set SERVER_URL=http://localhost:8080
```

Building a signed distributable is `npm run dist:mac` / `dist:win` /
`dist:linux`. CI handles release builds on tag pushes (see
`.github/workflows/build.yml`).

## Running the marketing site

```bash
cd website
npm run dev    # http://localhost:5174
npm run build
```

Deploys to Cloudflare Pages on push to `main`; no extra step needed
in your PR.

## Tests

```bash
source .venv/bin/activate
pytest -q             # full suite (601 tests, ~13 s)
pytest -q -k auth     # narrow to auth tests
pytest -q --lf        # last-failed only
```

If a test is flaky, file an issue with the failing log — don't add
`@pytest.mark.flaky`. We chase flakes, we don't accept them.

Frontend tests are minimal today; if you add components, please
add at least a smoke test (`vitest` is wired up in each dashboard).

## Pre-commit hooks

We run gitleaks + a few hygiene checks on every commit to keep
secrets out of git. Install once:

```bash
pip install pre-commit
pre-commit install
```

Now every `git commit` runs `.pre-commit-config.yaml`. If a hook
fails, fix the issue (don't `--no-verify`). The remote GitHub push
protection is a second layer that will catch bypassed locals.

To run all hooks against the whole repo (for example after pulling
a config change):

```bash
pre-commit run --all-files
```

## Commit signing (recommended)

GitHub adds a "Verified" badge to signed commits, which is the best
free defence against impersonation. SSH signing is the easiest path
(reuse the key you already use for `git push`):

```bash
git config --global commit.gpgsign true
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub

# Then, in GitHub → Settings → SSH and GPG keys:
# - Add your public key.
# - Switch its purpose to "Signing key" (or add a second entry).
```

Verify with `git commit --allow-empty -m "test signed" && git log -1
--show-signature`.

## Commit style

Conventional Commits, lowercase verb prefixes:

- `feat:` user-visible feature.
- `fix:` user-visible bug fix.
- `refactor:` no behaviour change.
- `chore:` tooling, config, deps.
- `docs:` docs only.
- `test:` test-only changes.
- `security:` security-relevant fix (with detail in the body).
- `perf:` performance change.

The body should explain **why**, not what — `git diff` shows the
what. Aim for 50-char subject, blank line, then a wrapped body.

Examples we like:

- `feat(billing): pay-as-you-go overage at ₹80/student`
- `fix(csp): allow cdn.razorpay.com (risk-detection bundle)`
- `security(P2.1+P2.2): HttpOnly cookie auth + close CSRF gap`

## Branch model & PRs

- We use **trunk-based development** — branch off `main`, PR back to
  `main`, no long-lived release branches.
- Keep PRs small: one logical change per PR. Multiple commits per PR
  is fine.
- **Required to merge**:
  - All required status checks green (pytest, security-scan,
    docker-smoke).
  - At least one approving review (self-merge by the maintainer is
    allowed for chore/docs PRs).
  - Linear history (rebase on `main` before merge, no merge commits).
- **PR description** should answer:
  - What this PR does.
  - How a reviewer can verify it.
  - Any follow-ups you punted on.

## Code style

- **Python**: ruff is the linter and formatter. CI doesn't currently
  block on ruff, but PRs that ignore it will get review comments.
  Type hints required on public functions.
- **JavaScript / TypeScript**: eslint + prettier. Each Vite app has
  its own config; respect the one in the directory you're editing.
- **SQL**: lowercase keywords, snake_case identifiers, never `SELECT *`
  in production code (test fixtures are fine).
- **CSS**: BEM-ish naming, no new `!important` without a comment
  explaining why specificity ladder didn't work.

## License & DCO

Procta is licensed under the [LICENSE](LICENSE) at the repo root.
By submitting a contribution you certify that you wrote it (or have
the right to submit it) and that you license it under the same
terms. We don't run a CLA bot — instead, add the line
`Signed-off-by: Your Name <you@email>` to your commits
(`git commit -s`). It's tiny but it's how we keep the provenance
trail clean.

---

**Stuck?** Open a draft issue with `[help]` in the title or DM
[@ArihantK15](https://github.com/ArihantK15). We'd rather answer
a "is this the right approach?" question early than review a 500-line
PR that took the wrong turn at line 12.
