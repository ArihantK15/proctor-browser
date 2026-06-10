"""Endpoint tests for question-bank PDF/DOCX extract + confirm."""
import io
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token  # noqa: E402

TEACHER = {"id": "teacher-1", "email": "p@t.com", "org_id": "org-1",
           "org_role": "teacher", "full_name": "P", "status": "active"}


def _hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1')}"}


def _pdf(text):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for ln in text.splitlines():
        c.drawString(72, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


# ── /extract ──────────────────────────────────────────────────────

def test_extract_requires_auth(client):
    r = client.post("/api/v1/admin/question-bank/extract",
                    files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 401


def test_extract_returns_preview(client):
    pdf = _pdf("1. What is 2+2?\n(a) 3\n(b) 4\nAns: B")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("bank.pdf", pdf, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["found"] == 1
    assert data["questions"][0]["correct"] == "B"
    assert "id" not in data["questions"][0]   # nothing persisted


def test_extract_scanned_pdf_422(client):
    blank = _pdf("")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("scan.pdf", blank, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 422


def test_extract_bad_extension_415(client):
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("notes.txt", b"hi", "text/plain")},
                        headers=_hdr())
    assert r.status_code == 415


def test_extract_oversize_413(client):
    big = b"%PDF-" + b"0" * (21 * 1024 * 1024)
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("big.pdf", big, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 413


# ── /extract/confirm ──────────────────────────────────────────────

def _confirm_body(qs):
    return {"questions": qs}


def test_confirm_rejects_blocking_flag(client):
    payload = _confirm_body([{
        "question": "x", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "", "tags": [], "image_url": "", "flags": ["no_answer"],
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400
    assert "no_answer" in r.text or "attention" in r.text.lower()


def test_confirm_recomputes_flags_not_trusts_client(client):
    # Client lies: claims clean flags but the data still has no correct answer.
    payload = _confirm_body([{
        "question": "x", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "", "tags": [], "image_url": "", "flags": [],   # lying
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400   # server recomputes no_answer


def test_confirm_persists_clean_questions(client):
    payload = _confirm_body([{
        "question": "2+2?", "type": "mcq_single", "options": {"A": "3", "B": "4"},
        "correct": "B", "tags": ["math"], "image_url": "", "flags": [],
    }])
    captured = {}

    class _Chain:
        def insert(self, rows):
            captured["rows"] = rows
            return self

        async def execute(self):
            r = MagicMock()
            r.data = [{"id": "q-1"}]
            return r

    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER), \
         patch("app.routers.question_bank._atable", side_effect=lambda t: _Chain()):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert captured["rows"][0]["teacher_id"] == "teacher-1"
    assert captured["rows"][0]["correct"] == "B"


def test_confirm_rejects_external_image_url(client):
    # Egress guard: a non-local image_url (tracking beacon) must be rejected.
    payload = _confirm_body([{
        "question": "x", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "A", "tags": [], "image_url": "https://evil.com/beacon.jpg",
        "flags": [],
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400
    assert "image" in r.text.lower()


def test_confirm_accepts_local_image_url(client):
    payload = _confirm_body([{
        "question": "", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "A", "tags": [],
        "image_url": "/api/v1/question-image/teacher-1/abc.png", "flags": [],
    }])

    class _Chain:
        def insert(self, rows):
            return self

        async def execute(self):
            r = MagicMock()
            r.data = [{"id": "q-2"}]
            return r

    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER), \
         patch("app.routers.question_bank._atable", side_effect=lambda t: _Chain()):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 200, r.text


def test_confirm_image_question_no_answer_still_blocks(client):
    # An image-backed question with no correct answer must still block.
    payload = _confirm_body([{
        "question": "", "type": "mcq_single", "options": {"A": "3", "B": "4"},
        "correct": "", "tags": [], "image_url": "/api/v1/question-image/teacher-1/x.png",
        "flags": [],
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400   # no_answer still applies
