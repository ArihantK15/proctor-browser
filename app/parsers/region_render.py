"""Rasterize a PDF page region to a PNG (math/diagram fidelity preservation).

pdfplumber bbox space is top-left origin in points. pypdfium2 renders a full
page to a bitmap at a chosen scale; we crop in pixel space afterward. Returns
b"" on any failure so the caller can fall back to text + a review flag.
"""
import io
import logging

logger = logging.getLogger("qbank.region")

_SCALE = 2.0   # render at 2x for crisp text/equations


def render_region_png(pdf_bytes: bytes, page_index: int, bbox: list) -> bytes:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            if page_index < 0 or page_index >= len(pdf):
                return b""
            page = pdf[page_index]
            bitmap = page.render(scale=_SCALE)
            pil = bitmap.to_pil()
            x0, y0, x1, y1 = bbox
            pad = 4   # points of padding so glyphs aren't clipped
            crop = (max(0, int((x0 - pad) * _SCALE)),
                    max(0, int((y0 - pad) * _SCALE)),
                    min(pil.width, int((x1 + pad) * _SCALE)),
                    min(pil.height, int((y1 + pad) * _SCALE)))
            if crop[2] <= crop[0] or crop[3] <= crop[1]:
                return b""
            region = pil.crop(crop)
            out = io.BytesIO()
            region.save(out, format="PNG")
            return out.getvalue()
        finally:
            pdf.close()
    except Exception as e:
        logger.warning("[qbank] region render failed: %s", e)
        return b""
