"""Unit tests for PDF region rasterization (app/parsers/region_render.py)."""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.region_render import render_region_png  # noqa: E402


def _make_text_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_render_region_returns_png_bytes():
    pdf = _make_text_pdf("E = mc^2 region")
    png = render_region_png(pdf, page_index=0, bbox=[60, 60, 300, 100])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100


def test_bad_page_returns_empty():
    pdf = _make_text_pdf("x")
    assert render_region_png(pdf, page_index=99, bbox=[0, 0, 10, 10]) == b""


def test_bad_bytes_returns_empty():
    assert render_region_png(b"not a pdf", page_index=0, bbox=[0, 0, 10, 10]) == b""
