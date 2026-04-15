#!/usr/bin/env python3
"""
Setup Test Data for Procta Load Test
=====================================
Creates a test exam and registers N test students.

Usage:
  python3 loadtest/setup_test_data.py \
    --host https://app.procta.net \
    --students 300 \
    --teacher-email <your-teacher-email> \
    --teacher-password <your-teacher-password>
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
    args = parser.parse_args()

    host = args.host.rstrip("/")
    s = requests.Session()

    # ── Step 1: Teacher Login ───────────────────────────────────
    print(f"[1/5] Logging in as {args.teacher_email}...")
    r = s.post(f"{host}/api/auth/login", json={
        "email": args.teacher_email,
        "password": args.teacher_password,
    })
    if r.status_code != 200:
        print(f"  FAILED: {r.status_code} — {r.text[:200]}")
        sys.exit(1)

    auth_data = r.json()
    teacher_token = auth_data.get("access_token")
    teacher_id = auth_data.get("teacher", {}).get("id", "")
    headers = {
        "Authorization": f"Bearer {teacher_token}",
        "Content-Type": "application/json",
    }
    print(f"  OK — teacher_id: {teacher_id}")

    if not teacher_id:
        print("  ERROR: No teacher_id returned. Check login response.")
        sys.exit(1)

    # ── Step 2: Create Load Test Exam ───────────────────────────
    print(f"[2/5] Creating load test exam ({args.questions} questions, {args.duration} min)...")

    r = s.post(f"{host}/api/admin/exams", json={
        "exam_title": f"Load Test — {args.students} Students",
        "duration_minutes": args.duration,
    }, headers=headers)

    if r.status_code != 200:
        print(f"  FAILED to create exam: {r.status_code} — {r.text[:300]}")
        sys.exit(1)

    exam_data = r.json()
    exam_id = exam_data.get("exam_id")
    print(f"  OK — exam_id: {exam_id}")

    if not exam_id:
        print("  ERROR: No exam_id returned.")
        sys.exit(1)

    # ── Step 3: Add questions to the exam ───────────────────────
    print(f"[3/5] Adding {args.questions} questions...")

    questions = []
    for i in range(1, args.questions + 1):
        questions.append({
            "id": f"lt_q{i}",
            "question": f"Load test question {i}: What is {i} + {i}?",
            "question_type": "mcq_single",
            "options": {
                "A": f"{i * 2}",
                "B": f"{i * 2 + 1}",
                "C": f"{i * 2 - 1}",
                "D": f"{i * 3}",
            },
            "correct": "A",
            "image_url": "",
        })

    r = s.post(f"{host}/api/admin/questions", json={
        "exam_id": exam_id,
        "questions": questions,
    }, headers=headers)

    if r.status_code != 200:
        print(f"  FAILED to add questions: {r.status_code} — {r.text[:300]}")
        sys.exit(1)
    print(f"  OK — {args.questions} questions added")

    # Set exam schedule to allow immediate access
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + args.duration * 60 + 7200))
    r = s.post(f"{host}/api/admin/exam-schedule", json={
        "exam_id": exam_id,
        "starts_at": now_iso,
        "ends_at": end_iso,
    }, headers=headers)
    if r.status_code == 200:
        print(f"  Schedule set: {now_iso} → {end_iso}")
    else:
        print(f"  Warning: schedule not set ({r.status_code})")

    # ── Step 4: Register Test Students (bulk, no rate limit) ────
    print(f"[4/5] Registering {args.students} test students (bulk)...")
    roll_numbers = []
    total_registered = 0
    total_skipped = 0
    batch_size = 100

    for batch_start in range(0, args.students, batch_size):
        batch_end = min(batch_start + batch_size, args.students)
        batch = []
        for i in range(batch_start + 1, batch_end + 1):
            roll = f"{PREFIX}{i:04d}"
            batch.append({
                "roll_number": roll,
                "full_name": f"Load Test Student {roll}",
                "email": f"{roll.lower()}@loadtest.local",
                "phone": "0000000000",
            })

        r = s.post(f"{host}/api/admin/register-students-bulk", json={
            "students": batch,
        }, headers=headers)

        if r.status_code == 200:
            data = r.json()
            total_registered += data.get("registered", 0)
            total_skipped += data.get("skipped", 0)
            for item in batch:
                roll_numbers.append(item["roll_number"])
        else:
            print(f"  Batch {batch_start+1}-{batch_end} FAILED: {r.status_code} — {r.text[:200]}")

        pct = int((batch_end / args.students) * 100)
        print(f"  {pct}% — {total_registered} registered, {total_skipped} skipped")

    print(f"  Done — {total_registered} new, {total_skipped} already existed")

    # ── Step 5: Save Manifest ───────────────────────────────────
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

    print(f"\n[5/5] Manifest saved: {manifest_path}")
    print(f"\n{'='*60}")
    print(f"  LOAD TEST READY")
    print(f"  Exam ID:    {exam_id}")
    print(f"  Teacher ID: {teacher_id}")
    print(f"  Students:   {len(roll_numbers)}")
    print(f"  Questions:  {args.questions}")
    print(f"  Duration:   {args.duration} min")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    print(f"  1. pip3 install locust")
    print(f"  2. locust -f loadtest/locustfile.py --host {host}")
    print(f"  3. Open http://localhost:8089")
    print(f"  4. Recommended test plan:")
    print(f"     - Warm-up:  50 users,  ramp 10/sec")
    print(f"     - Sustained: 150 users, ramp 10/sec")
    print(f"     - Stress:   300 users, ramp 20/sec")
    print(f"     - Break:    500+ users, ramp 50/sec")


if __name__ == "__main__":
    main()
