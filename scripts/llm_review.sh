#!/usr/bin/env bash
set -euo pipefail

MODEL="${LLM_REVIEW_MODEL:-qwen2.5-coder:14b}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/api/generate}"
BASE_REF="${BASE_REF:-origin/main}"
OUT_DIR="${OUT_DIR:-logs/code-quality}"
REPORT="${REPORT:-$OUT_DIR/llm-review.md}"
MAX_DIFF_BYTES="${MAX_DIFF_BYTES:-120000}"

mkdir -p "$OUT_DIR"

DIFF_FILE="$OUT_DIR/git-diff.patch"
STAT_FILE="$OUT_DIR/git-diff-stat.log"
PROMPT_FILE="$OUT_DIR/llm-review-prompt.txt"
RESPONSE_FILE="$OUT_DIR/llm-review-response.json"

git diff --stat "$BASE_REF"...HEAD > "$STAT_FILE" || git diff --stat > "$STAT_FILE"
git diff "$BASE_REF"...HEAD > "$DIFF_FILE" || git diff > "$DIFF_FILE"

if [ ! -s "$DIFF_FILE" ]; then
  printf '# LLM Code Review\n\nNo local diff found against `%s`.\n' "$BASE_REF" > "$REPORT"
  echo "No diff found. Wrote $REPORT"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to call Ollama." >&2
  exit 1
fi

if ! curl -fsS "${OLLAMA_URL%/api/generate}/api/tags" >/dev/null 2>&1; then
  echo "Ollama is not reachable at ${OLLAMA_URL%/api/generate}." >&2
  echo "Start it with: ollama serve" >&2
  echo "Then pull a model with: ollama pull $MODEL" >&2
  exit 1
fi

python3 - "$DIFF_FILE" "$MAX_DIFF_BYTES" "$PROMPT_FILE" "$MODEL" "$STAT_FILE" <<'PY'
import pathlib
import sys

diff_path = pathlib.Path(sys.argv[1])
max_bytes = int(sys.argv[2])
prompt_path = pathlib.Path(sys.argv[3])
model = sys.argv[4]
stat_path = pathlib.Path(sys.argv[5])

diff_bytes = diff_path.read_bytes()
truncated = len(diff_bytes) > max_bytes
diff_text = diff_bytes[:max_bytes].decode("utf-8", errors="replace")
stat_text = stat_path.read_text(encoding="utf-8", errors="replace")

logs = []
for name in [
    "python-compile.log",
    "pytest.log",
    "npm-audit-root.log",
    "dashboard.log",
    "website.log",
    "docker-compose.log",
]:
    p = pathlib.Path("logs/code-quality") / name
    if p.exists():
        logs.append(f"## {name}\n{p.read_text(encoding='utf-8', errors='replace')[-8000:]}")

prompt = f"""You are a senior software engineer, security reviewer, QA engineer, and SRE.

Review this production code diff for a FastAPI/Supabase/Electron/React proctoring system.

Use only the evidence below. Do not invent files, dependencies, or behavior.

Prioritize:
1. security/privacy/compliance issues,
2. authentication and authorization bugs,
3. data integrity problems,
4. reliability and deployment risks,
5. performance regressions,
6. missing tests.

Every actionable finding must include:
- severity: Critical, High, Medium, or Low,
- exact file path and line if visible in the diff,
- why it matters,
- minimal fix,
- test that should catch it.

If there are no serious issues, say so clearly. Keep noise low.

Return Markdown with these sections:

### Verdict
### Critical Issues
### High Priority Issues
### Medium / Low Issues
### Missing Tests
### Suggested Patch Plan
### Deployment Risk

Model requested: {model}

Git diff stat:
```text
{stat_text}
```

Tool output:
```text
{chr(10).join(logs) if logs else "No tool logs were found. Run scripts/quality_check.sh first for better review context."}
```

Git diff:
```diff
{diff_text}
```

Diff truncated: {truncated}
"""

prompt_path.write_text(prompt, encoding="utf-8")
PY

python3 - "$PROMPT_FILE" "$MODEL" "$OLLAMA_URL" "$RESPONSE_FILE" "$REPORT" <<'PY'
import json
import pathlib
import sys
import urllib.request

prompt_path = pathlib.Path(sys.argv[1])
model = sys.argv[2]
url = sys.argv[3]
response_path = pathlib.Path(sys.argv[4])
report_path = pathlib.Path(sys.argv[5])

payload = {
    "model": model,
    "prompt": prompt_path.read_text(encoding="utf-8"),
    "stream": False,
    "options": {
        "temperature": 0.1,
        "top_p": 0.9,
    },
}

request = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=600) as resp:
    raw = resp.read().decode("utf-8")

response_path.write_text(raw, encoding="utf-8")
data = json.loads(raw)
review = data.get("response", "").strip()

if not review:
    raise SystemExit("Ollama returned an empty review.")

report_path.write_text(review + "\n", encoding="utf-8")
print(f"Wrote {report_path}")
PY

