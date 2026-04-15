"""
Procta Load Test — Full Student Exam Flow
==========================================
Simulates realistic student behavior: login → questions → answer → heartbeat → submit.

Usage:
  1. pip install locust
  2. Set up test data:  python loadtest/setup_test_data.py --students 200 --host https://app.procta.net
  3. Run load test:     locust -f loadtest/locustfile.py --host https://app.procta.net
  4. Open http://localhost:8089 → set users + ramp-up → Start
  5. After test, download the HTML report from the Locust UI

Environment variables (optional):
  EXAM_ID        — exam to test against (default: from setup script output)
  TEACHER_ID     — teacher who owns the exam
  ACCESS_CODE    — exam access code if set
"""

import json
import os
import random
import string
import time
from locust import HttpUser, task, between, events, tag

# ── Config ──────────────────────────────────────────────────────────
EXAM_ID = os.environ.get("EXAM_ID", "")
TEACHER_ID = os.environ.get("TEACHER_ID", "")
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
LOADTEST_SECRET = os.environ.get("LOADTEST_SECRET", "")
TEST_STUDENT_PREFIX = "LOADTEST_"

# Track allocated roll numbers so each user gets a unique one
_roll_pool = []
_roll_index = 0


def _next_roll():
    global _roll_index
    if _roll_index < len(_roll_pool):
        roll = _roll_pool[_roll_index]
        _roll_index += 1
        return roll
    # Fallback: generate on the fly
    _roll_index += 1
    return f"{TEST_STUDENT_PREFIX}{_roll_index:04d}"


@events.init.add_listener
def on_init(environment, **kwargs):
    """Load roll numbers from the setup script output."""
    global _roll_pool
    manifest = os.path.join(os.path.dirname(__file__), "test_students.json")
    if os.path.exists(manifest):
        with open(manifest) as f:
            data = json.load(f)
            _roll_pool = data.get("roll_numbers", [])
            global EXAM_ID, TEACHER_ID, ACCESS_CODE
            if not EXAM_ID:
                EXAM_ID = data.get("exam_id", "")
            if not TEACHER_ID:
                TEACHER_ID = data.get("teacher_id", "")
            if not ACCESS_CODE:
                ACCESS_CODE = data.get("access_code", "")
        print(f"[LoadTest] Loaded {len(_roll_pool)} test students for exam {EXAM_ID}")
    else:
        print(f"[LoadTest] WARNING: No test_students.json found. Run setup_test_data.py first.")
        # Generate synthetic roll numbers anyway
        _roll_pool = [f"{TEST_STUDENT_PREFIX}{i:04d}" for i in range(1, 501)]


class ExamStudent(HttpUser):
    """Simulates a single student taking a proctored exam."""

    # Realistic think time: students spend 5-20s per question
    wait_time = between(3, 10)

    def on_start(self):
        """Login and set up exam session."""
        self.roll_number = _next_roll()
        self.token = None
        self.session_id = None
        self.questions = []
        self.answers = {}
        self.current_q = 0
        self.exam_started = False
        self.submitted = False
        self.start_time = time.time()

        # Step 1: Validate (login)
        self._login()

    def _auth_headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if LOADTEST_SECRET:
            h["X-Loadtest-Key"] = LOADTEST_SECRET
        return h

    def _login(self):
        payload = {"roll_number": self.roll_number}
        if EXAM_ID:
            payload["exam_id"] = EXAM_ID
        if ACCESS_CODE:
            payload["access_code"] = ACCESS_CODE

        headers = {"X-Loadtest-Key": LOADTEST_SECRET} if LOADTEST_SECRET else {}
        with self.client.post(
            "/api/validate-student",
            json=payload,
            headers=headers,
            name="/api/validate-student",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("valid"):
                    self.token = data["token"]
                    resp.success()
                else:
                    resp.failure(f"Invalid: {data.get('error', 'unknown')}")
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"HTTP {resp.status_code}")

        if not self.token:
            return

        # Step 2: Check for existing session
        with self.client.get(
            f"/api/check-session/{self.roll_number}",
            headers=self._auth_headers(),
            name="/api/check-session/[roll]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("exists"):
                    self.session_id = data["session_key"]
                    self.answers = data.get("answers", {})
                resp.success()

        # Create session ID if needed
        if not self.session_id:
            self.session_id = f"{self.roll_number}_{int(time.time())}"

        # Step 3: Load questions
        with self.client.get(
            f"/api/questions?session_id={self.session_id}",
            headers=self._auth_headers(),
            name="/api/questions",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                self.questions = data.get("questions", [])
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

        # Step 4: Send exam_started event
        if self.token and self.session_id:
            self._send_event("exam_started", "low", "Load test student started exam")
            self.exam_started = True

    # ── Tasks (weighted by realistic frequency) ──────────────────

    @task(10)
    @tag("answer")
    def answer_question(self):
        """Answer the next question."""
        if not self.exam_started or self.submitted or not self.questions:
            return

        if self.current_q >= len(self.questions):
            # All questions answered — submit
            self._submit_exam()
            return

        q = self.questions[self.current_q]
        qid = q["id"]
        # Pick a random option — options is a dict like {"A": "text", "B": "text"}
        options = q.get("options", {})
        answer = random.choice(list(options.keys())) if options else "A"
        self.answers[qid] = answer
        self.current_q += 1

        with self.client.post(
            "/api/save-answer",
            json={
                "session_id": self.session_id,
                "question_id": qid,
                "answer": answer,
            },
            headers=self._auth_headers(),
            name="/api/save-answer",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(5)
    @tag("heartbeat")
    def send_heartbeat(self):
        """Periodic heartbeat to keep session alive."""
        if not self.exam_started or self.submitted:
            return

        with self.client.post(
            "/heartbeat",
            json={
                "session_id": self.session_id,
                "event_type": "heartbeat",
                "severity": "low",
                "details": "alive",
            },
            headers=self._auth_headers(),
            name="/heartbeat",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(3)
    @tag("bulk-save")
    def bulk_save(self):
        """Periodic bulk save of all answers."""
        if not self.exam_started or self.submitted or not self.answers:
            return

        with self.client.post(
            "/api/save-answers-bulk",
            json={
                "session_id": self.session_id,
                "answers": self.answers,
            },
            headers=self._auth_headers(),
            name="/api/save-answers-bulk",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    @tag("violation")
    def send_violation_event(self):
        """Simulate occasional proctoring violations."""
        if not self.exam_started or self.submitted:
            return

        violation_types = [
            ("tab_switch", "medium", "Student switched tabs"),
            ("face_not_detected", "high", "No face in frame"),
            ("multiple_faces", "high", "Multiple faces detected"),
            ("gaze_away", "medium", "Looking away from screen"),
            ("eye_detected", "low", "Normal gaze tracking"),
        ]
        vtype, severity, details = random.choice(violation_types)
        self._send_event(vtype, severity, details)

    @task(1)
    @tag("frame")
    def send_frame(self):
        """Simulate sending a proctoring frame (small payload, not real image)."""
        if not self.exam_started or self.submitted:
            return

        # Send a tiny base64 payload instead of real frame to avoid bandwidth skew
        # Real frames are ~50-100KB; we use 1KB to test endpoint throughput
        fake_frame = "data:image/jpeg;base64," + "A" * 1024

        with self.client.post(
            "/api/analyze-frame",
            json={
                "session_id": self.session_id,
                "frame": fake_frame,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            headers=self._auth_headers(),
            name="/api/analyze-frame",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    # ── Helpers ──────────────────────────────────────────────────

    def _send_event(self, event_type, severity, details):
        with self.client.post(
            "/event",
            json={
                "session_id": self.session_id,
                "event_type": event_type,
                "severity": severity,
                "details": details,
            },
            headers=self._auth_headers(),
            name="/event",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    def _submit_exam(self):
        if self.submitted:
            return
        self.submitted = True
        elapsed = int(time.time() - self.start_time)

        with self.client.post(
            "/api/submit-exam",
            json={
                "session_id": self.session_id,
                "roll_number": self.roll_number,
                "full_name": f"Load Test {self.roll_number}",
                "email": f"{self.roll_number.lower()}@test.local",
                "time_taken_secs": elapsed,
                "answers": self.answers,
                "score": 0,
                "total": len(self.questions),
                "violations": [],
            },
            headers=self._auth_headers(),
            name="/api/submit-exam",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    def on_stop(self):
        """Submit if still in progress when Locust stops."""
        if self.exam_started and not self.submitted and self.token:
            self._submit_exam()
