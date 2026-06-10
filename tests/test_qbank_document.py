"""Unit tests for document extraction (app/parsers/document.py)."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.document import (  # noqa: E402
    extract_document, ScannedPdfError, UnreadableDocError,
)


def _make_text_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in text.splitlines():
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_docx(text: str) -> bytes:
    import docx
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_pdf_text_extraction():
    pdf = _make_text_pdf("1. Q one is here\n(a) x\n(b) y")
    doc = extract_document(pdf, "bank.pdf")
    assert doc.kind == "pdf"
    assert "Q one" in doc.text
    assert doc.pdf_bytes is not None
    assert all(l.bbox is not None for l in doc.lines)


def test_docx_text_extraction():
    data = _make_docx("1. Q one is here\n(a) x\n(b) y")
    doc = extract_document(data, "bank.docx")
    assert doc.kind == "docx"
    assert "Q one" in doc.text
    assert doc.pdf_bytes is None


def test_scanned_pdf_raises():
    blank = _make_text_pdf("")     # no real text → scanned-like
    with pytest.raises(ScannedPdfError):
        extract_document(blank, "scan.pdf")


def test_unknown_extension_raises():
    with pytest.raises(UnreadableDocError):
        extract_document(b"hello", "notes.txt")


def test_corrupt_pdf_raises_unreadable():
    with pytest.raises(UnreadableDocError):
        extract_document(b"%PDF-broken-not-a-real-pdf", "x.pdf")
