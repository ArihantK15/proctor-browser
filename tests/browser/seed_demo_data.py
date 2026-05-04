"""
Seed a running Procta environment with enough data that multi-exam bugs
can't hide during manual or automated testing.

Creates (idempotently, by name) for ONE teacher account:
  • 2 exams named "QA Exam A" and "QA Exam B"
  • N registered students per exam (default 5)

Why: the three recent regressions (pending-ID filter, exam persistence,
tools-tab refresh) only manifest with 2+ exams. Running this once against
a staging droplet turns those bugs from "subtle" into "obvious on first
click."

Usage:
    PROCTA_URL=https://staging.procta.net \\
    PROCTA_EMAIL=qa@procta.net \\
    PROCTA_PASSWORD=secret \\
        python seed_demo_data.py
"""
import os
import sys
import httpx


PROCTA_URL = os.environ.get("PROCTA_URL", "").rstrip("/")
PROCTA_EMAIL = os.environ.get("PROCTA_EMAIL", "")
PROCTA_PASSWORD = os.environ.get("PROCTA_PASSWORD", "")
STUDENTS_PER_EXAM = int(os.environ.get("STUDENTS_PER_EXAM", "5"))


def _require_env() -> None:
    missing = [n for n, v in [
        ("PROCTA_URL", PROCTA_URL),
        ("PROCTA_EMAIL", PROCTA_EMAIL),
        ("PROCTA_PASSWORD", PROCTA_PASSWORD),
    ] if not v]
    if missing:
        sys.exit(f"missing env: {', '.join(missing)}")


def login(client: httpx.Client) -> tuple[str, str]:
    """Return (access_token, teacher_id)."""
    resp = client.post(f"{PROCTA_URL}/api/auth/login",
                       json={"email": PROCTA_EMAIL, "password": PROCTA_PASSWORD})
    resp.raise_for_status()
    body = resp.json()
    return body["access_token"], body["teacher"]["id"]


def ensure_exams(client: httpx.Client, token: str) -> list[dict]:
    """Create QA Exam A + B if they don't already exist. Returns both."""
    headers = {"Authorization": f"Bearer {token}"}
    existing = client.get(f"{PROCTA_URL}/api/admin/exams", headers=headers)
    existing.raise_for_status()
    by_title = {e["exam_title"]: e for e in existing.json().get("exams", [])}

    wanted = [("QA Exam A", 45), ("QA Exam B", 60)]
    result = []
    for title, minutes in wanted:
        if title in by_title:
            print(f"  · {title} already exists ({by_title[title]['exam_id']})")
            result.append(by_title[title])
            continue
        resp = client.post(
            f"{PROCTA_URL}/api/admin/exams",
            headers=headers,
            json={"exam_title": title, "duration_minutes": minutes},
        )
        resp.raise_for_status()
        row = resp.json()
        print(f"  ✓ created {title} ({row['exam_id']})")
        result.append(row)
    return result


def register_students(client: httpx.Client, teacher_id: str, exam_tag: str, n: int) -> None:
    """Register N students. Roll numbers are deterministic so reruns are no-ops
    (server returns 409 for duplicates, which we swallow)."""
    created = 0
    for i in range(1, n + 1):
        roll = f"QA{exam_tag}{i:03d}"
        resp = client.post(f"{PROCTA_URL}/api/register-student", json={
            "roll_number": roll,
            "full_name":   f"QA Student {exam_tag}{i}",
            "email":       f"qa.{exam_tag.lower()}.{i}@example.test",
            "teacher_id":  teacher_id,
        })
        if resp.status_code == 409:
            continue  # already seeded
        resp.raise_for_status()
        created += 1
    print(f"  · {exam_tag}: {created} new, {n - created} already existed")


def main() -> None:
    _require_env()
    with httpx.Client(timeout=30) as client:
        print(f"→ logging in to {PROCTA_URL} as {PROCTA_EMAIL}")
        token, teacher_id = login(client)
        print(f"  teacher_id={teacher_id}")

        print("→ ensuring exams exist")
        exams = ensure_exams(client, token)

        print(f"→ registering {STUDENTS_PER_EXAM} students per exam tag")
        register_students(client, teacher_id, "A", STUDENTS_PER_EXAM)
        register_students(client, teacher_id, "B", STUDENTS_PER_EXAM)

        print("\nDone. Multi-exam test data is ready.")
        print("Exams:")
        for e in exams:
            print(f"  • {e['exam_title']}  exam_id={e['exam_id']}")


if __name__ == "__main__":
    main()
