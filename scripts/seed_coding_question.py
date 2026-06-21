"""Seed a JS coding question + test cases for the Edge Compiler (P1-T8).

Creates an exam (if TEACHER_ID + EXAM_ID are not set, creates a minimal one),
inserts a "Sum of Two Numbers" coding question with 2 sample + 3 hidden test
cases, and computes expected_output by running the reference solution through
Node.js.

Usage:
    export TEACHER_ID=<teacher-uuid> EXAM_ID=<exam-id>
    python3 scripts/seed_coding_question.py

Without env vars, creates a temporary exam for teacher "seed-teacher-1".
"""
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import async_table as _atable


# ── Reference solution (JS) ─────────────────────────────────────────
REFERENCE_SOURCE = """const readline = require('readline');
const rl = readline.createInterface({input: process.stdin});
rl.on('line', line => {
  const [a, b] = line.trim().split(/\\s+/).map(Number);
  console.log(a + b);
  rl.close();
});
"""

TEST_CASES = [
    {"idx": 0, "input": "2 3\n", "visibility": "sample", "float_tolerance": None},
    {"idx": 1, "input": "10 20\n", "visibility": "sample", "float_tolerance": None},
    {"idx": 2, "input": "100 200\n", "visibility": "hidden", "float_tolerance": None},
    {"idx": 3, "input": "0 0\n", "visibility": "hidden", "float_tolerance": None},
    {"idx": 4, "input": "-5 12\n", "visibility": "hidden", "float_tolerance": None},
]

QUESTION_OPTIONS = json.dumps({
    "marks": 10,
    "marks_policy": "partial",
    "allowed_languages": ["javascript"],
    "time_limit_ms": 5000,
    "memory_limit_kb": 65536,
    "starter": "// Read two integers from stdin, print their sum.\n// Example: \"2 3\" → 5\n",
})


def compute_expected(input_str: str) -> str | None:
    """Run the reference JS solution with *input_str* as stdin and return stdout."""
    try:
        r = subprocess.run(
            ["node", "-e", REFERENCE_SOURCE],
            input=input_str,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


async def main():
    tid = os.environ.get("TEACHER_ID") or "seed-teacher-1"
    eid = os.environ.get("EXAM_ID")

    # Ensure exam config exists
    if not eid:
        eid = f"coding-demo-{uuid.uuid4().hex[:8]}"
        print(f"[seed] creating exam {eid} for teacher {tid}")
        try:
            await _atable("exam_config").insert({
                "teacher_id": tid,
                "exam_id": eid,
                "exam_title": "Coding Demo — Sum of Two Numbers",
                "duration_minutes": 30,
                "coding_max_submit_attempts": 10,
            }).execute()
        except Exception as e:
            print(f"[seed] exam_config insert (may already exist): {e}")

    qid = f"coding-sum-{uuid.uuid4().hex[:8]}"
    question_row = {
        "teacher_id": tid,
        "exam_id": eid,
        "question_id": qid,
        "question": (
            "# Sum of Two Numbers\n\n"
            "Write a program that reads **two space-separated integers** from "
            "stdin and prints their sum to stdout.\n\n"
            "## Constraints\n- -1000 ≤ a, b ≤ 1000\n\n"
            "## Example\n\n**Input:**\n```\n2 3\n```\n**Output:**\n```\n5\n```"
        ),
        "question_type": "coding",
        "options": QUESTION_OPTIONS,
        "correct": "",  # coding questions have no correct answer
    }
    print(f"[seed] inserting question {qid}")
    try:
        await _atable("questions").insert(question_row).execute()
    except Exception as e:
        print(f"[seed] question insert failed: {e}")
        return

    for tc in TEST_CASES:
        expected = compute_expected(tc["input"])
        if expected is None:
            print(f"[seed] WARNING: could not compute expected for idx={tc['idx']} — "
                  f"Node.js may not be installed. Using placeholder '0'.")
            expected = "0"
        tc_row = {
            "question_id": qid,
            "teacher_id": tid,
            "idx": tc["idx"],
            "input": tc["input"],
            "expected_output": expected,
            "visibility": tc["visibility"],
            "float_tolerance": tc["float_tolerance"],
        }
        await _atable("coding_test_cases").insert(tc_row).execute()

    print(f"\n[seed] DONE!")
    print(f"  Teacher ID  : {tid}")
    print(f"  Exam ID     : {eid}")
    print(f"  Question ID : {qid}")
    print(f"  Type        : coding (Sum of Two Numbers)")
    print(f"  Test cases  : {len(TEST_CASES)} ({sum(1 for t in TEST_CASES if t['visibility']=='sample')} sample, "
          f"{sum(1 for t in TEST_CASES if t['visibility']=='hidden')} hidden)")
    print(f"\nTo test with curl:")
    print(f"  curl -H 'Authorization: Bearer <student-token>' \\")
    print(f"    'http://localhost:8000/api/v1/coding/testcases?session_id=TEST&question_id={qid}'")
    print()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
