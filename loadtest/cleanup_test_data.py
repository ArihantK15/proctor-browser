#!/usr/bin/env python3
"""
Cleanup Load Test Data
======================
Removes test students and exam created by setup_test_data.py.

Usage:
  python loadtest/cleanup_test_data.py \
    --host https://app.procta.net \
    --teacher-email <email> \
    --teacher-password <password>
"""

import argparse
import json
import os
import sys
import requests

PREFIX = "LOADTEST_"


def main():
    parser = argparse.ArgumentParser(description="Cleanup Procta load test data")
    parser.add_argument("--host", required=True)
    parser.add_argument("--teacher-email", required=True)
    parser.add_argument("--teacher-password", required=True)
    args = parser.parse_args()

    host = args.host.rstrip("/")
    s = requests.Session()

    # Login
    r = s.post(f"{host}/api/auth/login", json={
        "email": args.teacher_email,
        "password": args.teacher_password,
    })
    if r.status_code != 200:
        print(f"Login failed: {r.status_code}")
        sys.exit(1)

    auth_data = r.json()
    token = auth_data.get("token") or auth_data.get("access_token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Load manifest
    manifest_path = os.path.join(os.path.dirname(__file__), "test_students.json")
    if not os.path.exists(manifest_path):
        print("No test_students.json found — nothing to clean up")
        return

    with open(manifest_path) as f:
        data = json.load(f)

    exam_id = data.get("exam_id", "")
    rolls = data.get("roll_numbers", [])

    # Delete exam
    if exam_id:
        print(f"Deleting exam {exam_id}...")
        r = s.delete(f"{host}/api/admin/exams/{exam_id}", headers=headers)
        print(f"  {'OK' if r.status_code == 200 else f'HTTP {r.status_code}: {r.text[:100]}'}")

    # Delete test students
    print(f"Deleting {len(rolls)} test students...")
    deleted = 0
    for roll in rolls:
        r = s.delete(f"{host}/api/admin/students/{roll}", headers=headers)
        if r.status_code == 200:
            deleted += 1
    print(f"  Deleted {deleted}/{len(rolls)} students")

    # Remove manifest
    os.remove(manifest_path)
    print("Cleanup complete.")


if __name__ == "__main__":
    main()
