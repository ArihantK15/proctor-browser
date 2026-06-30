"""Seed a coding question + test cases for the Edge Compiler.

Creates an exam (if TEACHER_ID + EXAM_ID are not set, creates a minimal one),
inserts a "Sum of Two Numbers" coding question with 2 sample + 3 hidden test
cases, and computes expected_output by running the reference solution through
the appropriate interpreter (node for JS, python3 for Python).

Usage:
    export TEACHER_ID=<teacher-uuid> EXAM_ID=<exam-id>
    python3 scripts/seed_coding_question.py [--language javascript|python]

Without env vars, creates a temporary exam for teacher "seed-teacher-1".
Default --language is javascript (preserves Phase-1 behaviour).
"""
import argparse
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import async_table as _atable
from app.services import secrets_crypto


# ── Language-specific definitions ───────────────────────────────────────────

# JavaScript — Sum of Two Numbers (original P1-T8 seed)
_JS_REFERENCE = """const readline = require('readline');
const rl = readline.createInterface({input: process.stdin});
rl.on('line', line => {
  const [a, b] = line.trim().split(/\\s+/).map(Number);
  console.log(a + b);
  rl.close();
});
"""

_JS_STARTER = (
    "// Read two integers from stdin, print their sum.\n"
    "// Example: \"2 3\" → 5\n"
)

# Python — Sum of Two Numbers (two integers on separate lines, print sum)
_PY_REFERENCE = """\
a = int(input())
b = int(input())
print(a + b)
"""

_PY_STARTER = (
    "# Read two integers (one per line) from stdin, print their sum.\n"
    "# Example: input is '2\\n3\\n', output is '5'\n"
    "a = int(input())\n"
    "b = int(input())\n"
    "# your code here\n"
)

# Test cases are the same for both languages; each language's reference
# interpreter fills expected_output at seed time.
# NOTE: Python variant uses one-per-line format ("2\n3\n") instead of the JS
# "2 3\n" single-line format, because idiomatic Python competitive I/O reads
# one int per line.  The question text + starter make the format explicit.
_TEST_CASES_JS = [
    {"idx": 0, "input": "2 3\n",   "visibility": "sample", "float_tolerance": None},
    {"idx": 1, "input": "10 20\n", "visibility": "sample", "float_tolerance": None},
    {"idx": 2, "input": "100 200\n","visibility": "hidden", "float_tolerance": None},
    {"idx": 3, "input": "0 0\n",   "visibility": "hidden", "float_tolerance": None},
    {"idx": 4, "input": "-5 12\n", "visibility": "hidden", "float_tolerance": None},
]

_TEST_CASES_PY = [
    {"idx": 0, "input": "2\n3\n",     "visibility": "sample", "float_tolerance": None},
    {"idx": 1, "input": "10\n20\n",   "visibility": "sample", "float_tolerance": None},
    {"idx": 2, "input": "100\n200\n", "visibility": "hidden", "float_tolerance": None},
    {"idx": 3, "input": "0\n0\n",     "visibility": "hidden", "float_tolerance": None},
    {"idx": 4, "input": "-5\n12\n",   "visibility": "hidden", "float_tolerance": None},
]

_QUESTION_TEXT = (
    "# Sum of Two Numbers\n\n"
    "Write a program that reads **two integers** from stdin and prints their "
    "sum to stdout.\n\n"
    "## Constraints\n- -1000 ≤ a, b ≤ 1000\n\n"
)

_QUESTION_TEXT_JS = _QUESTION_TEXT + (
    "## Input format\nA single line with two space-separated integers.\n\n"
    "## Example\n\n**Input:**\n```\n2 3\n```\n**Output:**\n```\n5\n```"
)

_QUESTION_TEXT_PY = _QUESTION_TEXT + (
    "## Input format\nTwo integers, one per line.\n\n"
    "## Example\n\n**Input:**\n```\n2\n3\n```\n**Output:**\n```\n5\n```"
)


# ── Reference-runner helper ──────────────────────────────────────────────────

def compute_expected(input_str: str, language: str) -> str | None:
    """Run the reference solution for *language* with *input_str* as stdin.

    Dispatches to:
      - node  (via -e flag) for 'javascript'
      - python3 (via -c flag) for 'python'

    Returns stripped stdout on success, or None on timeout / interpreter
    not found / non-zero exit code (caller substitutes a placeholder and
    warns).
    """
    if language == "javascript":
        cmd = ["node", "-e", _JS_REFERENCE]
        source = None           # reference is passed via -e, no tempfile needed
    elif language == "python":
        # Use -c to pass the source inline — mirrors node -e exactly.
        cmd = ["python3", "-c", _PY_REFERENCE]
        source = None
    else:
        raise ValueError(f"unknown language: {language!r}")

    try:
        r = subprocess.run(
            cmd,
            input=input_str,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ── Main seed logic ──────────────────────────────────────────────────────────

async def main(language: str):
    tid = os.environ.get("TEACHER_ID") or "seed-teacher-1"
    eid = os.environ.get("EXAM_ID")

    lang_label = language.capitalize()

    # Ensure exam config exists
    if not eid:
        eid = f"coding-demo-{uuid.uuid4().hex[:8]}"
        print(f"[seed] creating exam {eid} for teacher {tid}")
        try:
            await _atable("exam_config").insert({
                "teacher_id": tid,
                "exam_id": eid,
                "exam_title": f"Coding Demo — Sum of Two Numbers ({lang_label})",
                "duration_minutes": 30,
                "coding_max_submit_attempts": 10,
            }).execute()
        except Exception as e:
            print(f"[seed] exam_config insert (may already exist): {e}")

    # The whole coding chain keys on the questions.question_id LABEL — load_questions
    # REMAPS each row to id = str(question_id), so the renderer's q.id, the judge-
    # stored coding_submissions.question_id, and scoring's q['id'] are ALL this label
    # (NOT the UUID PK). So coding_test_cases.question_id MUST be this label. The
    # uuid suffix keeps the label unique. (Earlier this keyed on an explicit UUID PK
    # — that mismatched load_questions and made testcase lookup + scoring fail.)
    qid = f"coding-sum-{language[:2]}-{uuid.uuid4().hex[:8]}"

    if language == "javascript":
        options = json.dumps({
            "marks": 10,
            "marks_policy": "partial",
            "allowed_languages": ["javascript"],
            "time_limit_ms": 5000,
            "memory_limit_kb": 65536,
            "starter": _JS_STARTER,
        })
        question_text = _QUESTION_TEXT_JS
        test_cases = _TEST_CASES_JS
    else:  # python
        options = json.dumps({
            "marks": 10,
            "marks_policy": "partial",
            "allowed_languages": ["python"],
            "time_limit_ms": 5000,
            "memory_limit_kb": 65536,
            "starter": _PY_STARTER,
        })
        question_text = _QUESTION_TEXT_PY
        test_cases = _TEST_CASES_PY

    question_row = {
        "teacher_id": tid,
        "exam_id": eid,
        "question_id": qid,
        "question": question_text,
        "question_type": "coding",
        "options": options,
        "correct": "",  # coding questions have no correct answer
    }
    print(f"[seed] inserting question {qid} (language={language})")
    try:
        await _atable("questions").insert(question_row).execute()
    except Exception as e:
        print(f"[seed] question insert failed: {e}")
        return

    interpreter_name = "node" if language == "javascript" else "python3"
    for tc in test_cases:
        expected = compute_expected(tc["input"], language)
        if expected is None:
            print(
                f"[seed] WARNING: could not compute expected for idx={tc['idx']} — "
                f"{interpreter_name} may not be installed. Using placeholder '0'."
            )
            expected = "0"
        tc_row = {
            "question_id": qid,   # key on the question_id LABEL (see note above)
            "teacher_id": tid,
            "idx": tc["idx"],
            "input": tc["input"],
            # Envelope-encrypt the answer key before it hits Postgres (no-op
            # if CODING_SECRETS_KEY isn't set — dev/CI without a key).
            "expected_output": secrets_crypto.encrypt(expected),
            "visibility": tc["visibility"],
            "float_tolerance": tc["float_tolerance"],
        }
        await _atable("coding_test_cases").insert(tc_row).execute()

    print("\n[seed] DONE!")
    print(f"  Teacher ID  : {tid}")
    print(f"  Exam ID     : {eid}")
    print(f"  question_id  : {qid}   (the key renderer/judge/scoring all use)")
    print(f"  Language    : {language}")
    print("  Type        : coding (Sum of Two Numbers)")
    print(f"  Test cases  : {len(test_cases)} "
          f"({sum(1 for t in test_cases if t['visibility']=='sample')} sample, "
          f"{sum(1 for t in test_cases if t['visibility']=='hidden')} hidden)")
    print("\nTo test with curl:")
    print("  curl -H 'Authorization: Bearer <student-token>' \\")
    print(f"    'http://localhost:8000/api/v1/coding/testcases?session_id=TEST&question_id={qid}'")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed a coding question for the Edge Compiler. "
                    "Uses TEACHER_ID / EXAM_ID env vars if set; otherwise creates a demo exam."
    )
    parser.add_argument(
        "--language",
        choices=["javascript", "python"],
        default="javascript",
        help=(
            "Language to seed (default: javascript). "
            "'javascript' runs the reference through node; "
            "'python' runs it through python3."
        ),
    )
    args = parser.parse_args()

    import asyncio
    asyncio.run(main(args.language))
