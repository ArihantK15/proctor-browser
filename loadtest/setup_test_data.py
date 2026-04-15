#!/usr/bin/env python3
"""
Setup Test Data for Procta Load Test
=====================================
Creates a test exam and registers N test students.

Usage:
  python loadtest/setup_test_data.py \
    --host https://app.procta.net \
    --students 200 \
    --teacher-email <your-teacher-email> \
    --teacher-password <your-teacher-password>

This will:
  1. Login as the teacher
  2. Create a load-test exam with 20 MCQ questions
  3. Register N test students (LOADTEST_0001 through LOADTEST_NNNN)
  4. Save a manifest (test_students.json) used by locustfile.py
"""

import argparse
import json
import os
import sys
import time
import requests

PREFIX = "LOADTEST_"


def main():
    parser = argparse.ArgumentParser(description="Setup load test data for Procta")
    parser.add_argument("--host", required=True, help="Server URL (e.g. https://app.procta.net)")
    parser.add_argument("--students", type=int, default=200, help="Number of test students to create")
    parser.add_argument("--teacher-email", required=True, help="Teacher login email")
    parser.add_argument("--teacher-password", required=True, help="Teacher login password")
    parser.add_argument("--questions", type=int, default=20, help="Number of MCQ questions")
    parser.add_argument("--duration", type=int, default=120, help="Exam duration in minutes")
    parser.add_argument("--cleanup", action="store_true", help="Delete existing load test data instead of creating")
    args = parser.parse_args()

    host = args.host.rstrip("/")
    s = requests.Session()

    # ── Step 1: Teacher Login ───────────────────────────────────
    print(f"[1/4] Logging in as {args.teacher_email}...")
    r = s.post(f"{host}/api/auth/login", json={
        "email": args.teacher_email,
        "password": args.teacher_password,
    })
    if r.status_code != 200:
        print(f"  FAILED: {r.status_code} — {r.text[:200]}")
        sys.exit(1)

    auth_data = r.json()
    teacher_token = auth_data.get("token") or auth_data.get("access_token")
    teacher_id = auth_data.get("teacher_id") or auth_data.get("user_id", "")
    headers = {
        "Authorization": f"Bearer {teacher_token}",
        "Content-Type": "application/json",
    }
    print(f"  OK — teacher_id: {teacher_id}")

    # ── Step 2: Create Load Test Exam ───────────────────────────
    print(f"[2/4] Creating load test exam ({args.questions} questions, {args.duration} min)...")
    exam_id = f"loadtest_{int(time.time())}"

    questions = []
    for i in range(1, args.questions + 1):
        questions.append({
            "id": f"lt_q{i}",
            "text": f"Load test question {i}: What is {i} + {i}?",
            "type": "mcq_single",
            "options": {
                "A": f"{i * 2}",
                "B": f"{i * 2 + 1}",
                "C": f"{i * 2 - 1}",
                "D": f"{i * 3}",
            },
            "correct": "A",
            "image_url": "",
        })

    r = s.post(f"{host}/api/admin/create-exam", json={
        "exam_id": exam_id,
        "exam_title": f"Load Test — {args.students} Students",
        "duration_minutes": args.duration,
        "questions": questions,
    }, headers=headers)

    if r.status_code != 200:
        print(f"  FAILED: {r.status_code} — {r.text[:300]}")
        sys.exit(1)
    print(f"  OK — exam_id: {exam_id}")

    # Set exam schedule to allow immediate access
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + args.duration * 60 + 7200))
    r = s.post(f"{host}/api/admin/set-schedule", json={
        "exam_id": exam_id,
        "starts_at": now_iso,
        "ends_at": end_iso,
    }, headers=headers)
    if r.status_code == 200:
        print(f"  Schedule set: {now_iso} → {end_iso}")

    # ── Step 3: Register Test Students ──────────────────────────
    print(f"[3/4] Registering {args.students} test students...")
    roll_numbers = []
    batch_size = 50
    registered = 0
    failed = 0

    for batch_start in range(0, args.students, batch_size):
        batch_end = min(batch_start + batch_size, args.students)
        batch_rolls = []

        for i in range(batch_start + 1, batch_end + 1):
            roll = f"{PREFIX}{i:04d}"
            batch_rolls.append(roll)

        # Register one by one (API doesn't support bulk registration)
        for roll in batch_rolls:
            try:
                r = s.post(f"{host}/api/admin/register-student", json={
                    "roll_number": roll,
                    "full_name": f"Load Test Student {roll}",
                    "email": f"{roll.lower()}@loadtest.local",
                    "phone": "0000000000",
                    "exam_id": exam_id,
                }, headers=headers)
                if r.status_code == 200:
                    roll_numbers.append(roll)
                    registered += 1
                elif r.status_code == 409:
                    # Already exists — still usable
                    roll_numbers.append(roll)
                    registered += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1

        pct = int((batch_end / args.students) * 100)
        print(f"  {pct}% — {registered} registered, {failed} failed", end="\r")

    print(f"\n  Done — {registered} students registered, {failed} failed")

    # ── Step 4: Save Manifest ───────────────────────────────────
    manifest_path = os.path.join(os.path.dirname(__file__), "test_students.json")
    manifest = {
        "exam_id": exam_id,
        "teacher_id": teacher_id,
        "access_code": "",
        "roll_numbers": roll_numbers,
        "students_count": len(roll_numbers),
        "questions_count": args.questions,
        "duration_minutes": args.duration,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[4/4] Manifest saved: {manifest_path}")
    print(f"\n{'='*60}")
    print(f"  LOAD TEST READY")
    print(f"  Exam ID:    {exam_id}")
    print(f"  Students:   {len(roll_numbers)}")
    print(f"  Questions:  {args.questions}")
    print(f"  Duration:   {args.duration} min")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    print(f"  1. pip install locust")
    print(f"  2. cd {os.path.dirname(os.path.dirname(__file__))}")
    print(f"  3. locust -f loadtest/locustfile.py --host {host}")
    print(f"  4. Open http://localhost:8089")
    print(f"  5. Recommended test plan:")
    print(f"     - Start:  50 users,  ramp 10/sec  (warm-up)")
    print(f"     - Scale: 150 users,  ramp 10/sec  (sustained)")
    print(f"     - Peak:  300 users,  ramp 20/sec  (stress test)")
    print(f"     - Break: 500+ users, ramp 50/sec  (find the limit)")
    print(f"  6. Download HTML report from Locust UI when done")


if __name__ == "__main__":
    main()
