# Continuous Code Quality Playbook

This project should have three layers of automated checking:

1. Fast local checks while developing.
2. Mandatory CI gates before merge/deploy.
3. Scheduled deep audits for security, performance, and architecture drift.

An LLM can help explain failures and find design risks, but it should not replace deterministic tools. The best setup is: tools produce evidence, then the LLM reviews the evidence plus the changed code.

## Current Project Stack

- Backend: FastAPI/Python, Supabase/Postgres, Redis/RQ workers.
- Desktop client: Electron/Node.
- Dashboard: React/Vite in `app/dashboard-ui`.
- Marketing site: React/Vite in `website`.
- Deployment: Docker, Docker Compose, GitHub Actions, droplet.

## Local Developer Gate

Run this before pushing production-sensitive changes:

```bash
python3 -m compileall -q app tests
pytest tests/ --ignore=tests/browser --ignore=tests/test_proctor_e2e.py --ignore=tests/test_proctor_features.py -q --tb=short
npm audit --audit-level=low
cd app/dashboard-ui && npm audit --audit-level=low && npm run build
cd ../../website && npm audit --audit-level=low && npm run build
docker compose config --quiet
```

If Docker is running locally, also run:

```bash
docker build -t procta-api-local .
```

For a stricter release check, run browser/proctor tests separately because they are heavier:

```bash
pytest tests/browser -q
pytest tests/test_proctor_e2e.py tests/test_proctor_features.py -q
```

## Recommended Local Automation

Use `pre-commit` to catch issues before commits. Add hooks for:

- Python syntax and formatting.
- secret scanning.
- YAML/JSON/TOML validation.
- trailing whitespace and large files.
- optional Semgrep security rules.

Suggested `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: check-yaml
      - id: check-json
      - id: check-toml
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-added-large-files

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.24.0
    hooks:
      - id: gitleaks

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.6
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

Install and enable:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## CI Gates

The existing GitHub Actions setup already does important work:

- `.github/workflows/test.yml`
  - Python compile check.
  - backend tests.
  - marketing site build.
  - Docker image build.
  - container health check.
  - k6 smoke test.

- `.github/workflows/deploy.yml`
  - reuses tests before deployment.
  - builds and pushes Docker image.
  - restarts API on the droplet.
  - waits for health status.

Recommended improvements:

1. Add dashboard build to `test.yml`.
2. Add `npm audit --audit-level=low` for root, dashboard, and website.
3. Add secret scanning with Gitleaks.
4. Add Semgrep OWASP/security scan.
5. Add Trivy filesystem and Docker image scan.
6. Add a scheduled weekly workflow for deeper checks.

Example CI additions:

```yaml
- name: Audit Electron dependencies
  run: npm audit --audit-level=low

- name: Build dashboard
  run: |
    cd app/dashboard-ui
    npm ci
    npm audit --audit-level=low
    npm run build

- name: Audit website dependencies
  run: |
    cd website
    npm audit --audit-level=low
```

## Security Scanners

Use these as deterministic security gates:

| Tool | Purpose | Recommended Frequency |
| --- | --- | --- |
| `npm audit` | Node dependency CVEs | every PR |
| `pip-audit` | Python dependency CVEs | every PR or weekly |
| Gitleaks | secrets in repo/history | every PR |
| Semgrep | code security patterns | every PR |
| Trivy | Docker/image/filesystem CVEs | every PR and deploy |
| Docker Scout | image supply-chain review | optional |

Useful local commands:

```bash
pip-audit -r requirements.txt
gitleaks detect --source .
semgrep scan --config auto
trivy fs .
trivy image procta-api-local
```

## Local LLM Review

A local LLM is useful for continuous review, but treat it as a reviewer, not a compiler.

Recommended local setup:

- Runtime: Ollama.
- Models:
  - `qwen2.5-coder:14b` or larger for code review.
  - `deepseek-coder-v2` if your machine can handle it.
  - `llama3.1:8b` for lightweight summaries.
- UI/editor integration:
  - Continue.dev in VS Code.
  - Aider for patch-oriented coding.
  - Open WebUI if you want a browser dashboard.

Docker-based Ollama:

```bash
docker run -d \
  --name ollama \
  -p 11434:11434 \
  -v ollama:/root/.ollama \
  ollama/ollama

docker exec -it ollama ollama pull qwen2.5-coder:14b
```

Native Ollama is usually easier on macOS:

```bash
ollama pull qwen2.5-coder:14b
ollama run qwen2.5-coder:14b
```

## LLM Review Prompt

Use this prompt for code review:

```text
You are reviewing a production FastAPI, Supabase, Electron, React/Vite proctoring system.

Review only the changed files and the tool output provided.

Prioritize:
1. security issues,
2. privacy/compliance failures,
3. authentication/authorization bugs,
4. data integrity bugs,
5. production reliability risks,
6. performance regressions,
7. missing tests.

Do not invent issues. Every finding must include:
- severity,
- exact file and line,
- why it matters,
- minimal fix,
- test that should catch it.

Return:
Critical Issues
Warnings
Suggested Fixes
Missing Tests
Deployment Risk
```

Feed it:

```bash
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
pytest output
npm audit output
build output
docker output
```

## Better Automated LLM Workflow

The best version is a small review script that:

1. Runs deterministic checks.
2. Captures output to files under `logs/code-quality/`.
3. Captures `git diff`.
4. Sends the diff and results to a local or hosted LLM.
5. Writes a Markdown review report.

Suggested report path:

```text
logs/code-quality/latest-review.md
```

The LLM report should be advisory. CI should still fail only on deterministic tools unless you intentionally add strict LLM review later.

## Suggested Script

Create `scripts/quality_check.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs/code-quality

python3 -m compileall -q app tests | tee logs/code-quality/python-compile.log
pytest tests/ --ignore=tests/browser --ignore=tests/test_proctor_e2e.py --ignore=tests/test_proctor_features.py -q --tb=short | tee logs/code-quality/pytest.log
npm audit --audit-level=low | tee logs/code-quality/npm-audit-root.log

(cd app/dashboard-ui && npm audit --audit-level=low && npm run build) | tee logs/code-quality/dashboard.log
(cd website && npm audit --audit-level=low && npm run build) | tee logs/code-quality/website.log

docker compose config --quiet | tee logs/code-quality/docker-compose.log
git diff --stat > logs/code-quality/git-diff-stat.log
git diff > logs/code-quality/git-diff.patch
```

Run:

```bash
bash scripts/quality_check.sh
```

## Continuous LLM Reviewer

This repository includes an advisory local LLM reviewer script:

```bash
scripts/llm_review.sh
```

Recommended workflow:

```bash
bash scripts/quality_check.sh
scripts/llm_review.sh
open logs/code-quality/llm-review.md
```

The reviewer expects Ollama to be running locally:

```bash
ollama serve
ollama pull qwen2.5-coder:14b
```

Use a different model:

```bash
LLM_REVIEW_MODEL=deepseek-coder-v2 scripts/llm_review.sh
```

Review against a different base branch:

```bash
BASE_REF=main scripts/llm_review.sh
```

The script writes:

```text
logs/code-quality/git-diff.patch
logs/code-quality/llm-review-prompt.txt
logs/code-quality/llm-review-response.json
logs/code-quality/llm-review.md
```

Use this report as a senior-review checklist. Do not fail deployment solely because the LLM says so unless a human confirms the issue or a deterministic test/scanner reproduces it.

Good automation levels:

- Local: run manually before big commits.
- Pre-push: run `scripts/quality_check.sh`; optionally run `scripts/llm_review.sh`.
- Pull request: run deterministic checks in GitHub Actions.
- Weekly: run deep scanners and optionally generate an LLM summary.

Avoid making the LLM mandatory on every commit at first. It will slow you down and may produce false positives. Start advisory, build trust, then promote only proven checks into CI.

## Scheduled Audits

Add a weekly GitHub Action:

```yaml
on:
  schedule:
    - cron: "0 3 * * 1"
  workflow_dispatch:
```

Weekly checks should include:

- full pytest suite where possible,
- `pip-audit`,
- `npm audit`,
- Semgrep,
- Trivy,
- Docker build,
- k6 smoke test,
- dependency freshness report.

## What To Automate First

Highest ROI order:

1. Add dashboard build/audit to CI.
2. Add `npm audit` for all three Node projects in CI.
3. Add Gitleaks to CI.
4. Add Semgrep to CI.
5. Add Trivy Docker image scan.
6. Add `scripts/quality_check.sh`.
7. Add pre-commit hooks.
8. Add scheduled weekly deep audit.
9. Add optional LLM review report from local Ollama.
10. Add real browser/proctor E2E as a separate slower CI workflow.

## Important Rule

Do not let an LLM be the only thing protecting production. The LLM should help you understand and prioritize issues. The deploy gate should be made of tests, builds, audits, scanners, health checks, and smoke tests.
