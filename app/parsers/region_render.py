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
            # Clamp the output bitmap so a malformed/oversized MediaBox can't OOM
            # the worker: PDF pages may declare up to 200in (14400pt); 2x that is
            # ~830 MP / multi-GB. Cap the longest rendered side and keep the crop
            # math on the SAME scale.
            _MAX_SIDE = 5000  # px
            w_pt, h_pt = page.get_size()
            longest_pt = max(w_pt, h_pt)
            scale = _SCALE
            if longest_pt * scale > _MAX_SIDE:
                scale = _MAX_SIDE / longest_pt
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            x0, y0, x1, y1 = bbox
            pad = 4   # points of padding so glyphs aren't clipped
            crop = (max(0, int((x0 - pad) * scale)),
                    max(0, int((y0 - pad) * scale)),
                    min(pil.width, int((x1 + pad) * scale)),
                    min(pil.height, int((y1 + pad) * scale)))
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
