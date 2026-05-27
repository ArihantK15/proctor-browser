# Continuous Quality Review

This is the local review workflow to run before every production deploy. It uses deterministic checks first, then an optional local LLM reviewer for a second opinion.

## Commands

Fast loop while developing:

```bash
MODE=fast scripts/quality_check.sh
```

Full release gate before deploy:

```bash
MODE=full scripts/quality_check.sh
```

Continuous local watcher:

```bash
INTERVAL=60 MODE=fast scripts/continuous_review.sh
```

With local LLM review after passing checks:

```bash
INTERVAL=120 MODE=fast RUN_LLM_ON_PASS=1 scripts/continuous_review.sh
```

## What The Gate Runs

`MODE=fast` runs:

- Python compile check for `app`, `tests`, and `worker.py`
- Focused regression tests around current high-risk product paths
- `git diff --check`
- root `npm audit --audit-level=low`
- dashboard `npm audit --audit-level=low`
- dashboard production build
- Docker Compose config validation
- diff stat and patch capture under `logs/code-quality/`

`MODE=full` additionally runs:

- full backend pytest suite, excluding browser and heavy proctor tests
- website `npm audit --audit-level=low`
- website production build

Heavy tests remain manual because they need browser or ML dependencies:

```bash
pytest tests/browser -q
pytest tests/test_proctor_e2e.py tests/test_proctor_features.py -q
```

## Local LLM Reviewer

The LLM reviewer reads:

- current git diff
- diff stat
- recent quality logs

It writes:

- `logs/code-quality/llm-review.md`
- `logs/code-quality/llm-review-prompt.txt`
- `logs/code-quality/llm-review-response.json`

Recommended local setup with Ollama:

```bash
ollama serve
ollama pull qwen2.5-coder:14b
MODE=full RUN_LLM=1 scripts/quality_check.sh
```

Docker option:

```bash
docker run -d --name ollama -p 11434:11434 -v ollama:/root/.ollama ollama/ollama
docker exec ollama ollama pull qwen2.5-coder:14b
RUN_LLM=1 scripts/quality_check.sh
```

You can swap models:

```bash
LLM_REVIEW_MODEL=deepseek-coder-v2:16b RUN_LLM=1 scripts/quality_check.sh
```

## Review Policy

Treat the LLM reviewer as advisory. It can catch missing tests, edge cases, and security smells, but it must not override failing deterministic checks.

Before deploy:

1. Run `MODE=full scripts/quality_check.sh`.
2. Run `RUN_LLM=1 MODE=full scripts/quality_check.sh` if Ollama is available.
3. Read `logs/code-quality/llm-review.md`.
4. Fix Critical and High findings or document why they are false positives.
5. Confirm CI is green after push.
6. Continue with `DEPLOY.md`.

## Failure Handling

If `quality_check.sh` fails:

- Open the matching log in `logs/code-quality/`.
- Fix the first real root cause.
- Rerun the same command.
- Do not deploy with known failing checks unless the failure is unrelated infrastructure noise and you have documented the reason.

If `llm_review.sh` fails:

- Check whether Ollama is running at `http://localhost:11434`.
- Pull the configured model.
- Rerun `scripts/llm_review.sh`.

LLM review failure alone does not block an emergency hotfix, but it should block normal releases until the review is available again.
