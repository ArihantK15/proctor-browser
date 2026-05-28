"""Scorecard PDF generation service.

Extracted from app/routers/admin.py to reduce the god module.
"""

import io
import logging
from datetime import datetime

from ..repositories.sessions import assert_session_owned as _assert_session_owned
from ..repositories.questions import load_questions as _load_questions, load_exam_config as _load_exam_config
from ..database import async_table as _atable
from ..services.risk import compute_risk_score
from ..utils import _safe_filename, fmt_ist, now_ist

logger = logging.getLogger(__name__)


def _build_info_table(exam: dict, score: float, total: float,
                       pct: float, risk_label: str, passed: bool):
    from reportlab.lib import colors as _c
    from reportlab.platypus import Table, TableStyle
    info = [
        ["Field", "Value"],
        ["Student Name", exam.get("full_name", "")],
        ["Roll Number", exam.get("roll_number", "")],
        ["Date", fmt_ist(exam.get("submitted_at", exam.get("started_at", "")))],
        ["Score", f"{score}/{total}"],
        ["Percentage", f"{pct}%"],
        ["Result", "PASS" if passed else "FAIL"],
        ["Time Taken", f"{exam.get('time_taken_secs', 0) // 60}m {exam.get('time_taken_secs', 0) % 60}s"],
        ["Risk Level", risk_label],
    ]
    t = Table(info, colWidths=[140, 330])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _c.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), _c.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [_c.HexColor("#f0f4ff"), _c.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, _c.grey),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _build_violation_table(viol_counts: dict):
    from reportlab.lib import colors as _c
    from reportlab.platypus import Table, TableStyle

    HUMAN_NAMES = {
        "gaze_away": "Gaze Away",
        "head_turned": "Head Turned",
        "eyes_closed": "Eyes Closed",
        "face_missing": "Face Missing",
        "multiple_faces": "Multiple Faces",
        "wrong_person": "Wrong Person",
        "calibration_abort": "Calibration Aborted (Identity Swap)",
        "cheat_object_detected": "Cheat Object Detected",
        "voice_detected": "Voice Detected",
        "window_focus_lost": "Window Focus Lost",
        "tab_hidden": "Tab Hidden",
        "shortcut_blocked": "Shortcut Blocked",
        "vm_detected": "VM Detected",
        "remote_desktop_detected": "Remote Desktop",
        "screen_share_detected": "Screen Share",
        "multiple_monitors": "Multiple Monitors",
        "phone_consulting": "Phone Consulting (Behavioral)",
        "collaboration": "Collaboration Suspected (Behavioral)",
        "answer_memo": "Answer Memorization (Behavioral)",
        "note_reading": "Note Reading (Behavioral)",
        "sustained_offtask": "Sustained Off-Task (Behavioral)",
        "nervous_evasion": "Nervous Evasion (Behavioral)",
    }

    vdata = [["Violation", "Count"]]
    for vtype in sorted(viol_counts.keys(), key=lambda k: viol_counts[k]["total"], reverse=True):
        name = HUMAN_NAMES.get(vtype, vtype.replace("_", " ").title())
        vdata.append([name, str(viol_counts[vtype]["total"])])

    vtotal = ["Total", str(sum(v["total"] for v in viol_counts.values()))]
    vdata.append(vtotal)

    vt = Table(vdata, colWidths=[370, 100])
    row_count = len(vdata)
    s = [
        ("BACKGROUND", (0, 0), (-1, 0), _c.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), _c.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, _c.grey),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
    ]
    for i in range(1, row_count):
        bg = _c.HexColor("#f0f4ff") if i % 2 == 1 else _c.white
        s.append(("BACKGROUND", (0, i), (-1, i), bg))
    s.append(("BACKGROUND", (0, row_count - 1), (-1, row_count - 1),
              _c.HexColor("#e8ecf4")))
    s.append(("FONTNAME", (0, row_count - 1), (-1, row_count - 1),
              "Helvetica-Bold"))
    vt.setStyle(TableStyle(s))
    return vt


def _build_question_table(questions: list, ans_map: dict):
    from reportlab.lib import colors as _c
    from reportlab.platypus import Table, TableStyle
    qd = [["#", "Question", "Your Answer", "Correct Answer", "Result"]]
    for i, q in enumerate(questions, 1):
        qid = str(q.get("question_id", q.get("id", "")))
        correct_ans = str(q.get("correct", ""))
        student_ans = ans_map.get(qid, "\u2014")
        is_right = str(student_ans) == correct_ans
        q_text = q.get("question", "")
        if len(q_text) > 60:
            q_text = q_text[:57] + "..."
        qd.append([
            str(i), q_text, str(student_ans)[:20],
            correct_ans[:20], "\u2713" if is_right else "\u2717",
        ])
    qt = Table(qd, colWidths=[25, 230, 80, 80, 35])
    qt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _c.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), _c.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [_c.HexColor("#f8f9fa"), _c.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, _c.grey),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (4, 1), (4, -1), "CENTER"),
    ]))
    return qt


async def _build_scorecard_pdf(session_id: str, teacher_id) -> tuple[bytes, str, dict]:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader

    tid = teacher_id
    exam = await _assert_session_owned(session_id, tid)
    exam_id = exam.get("exam_id")

    questions = await _load_questions(teacher_id=tid, exam_id=exam_id)
    ans_rows = (await _atable("answers").select("question_id,answer")
                .eq("session_key", session_id)
                .eq("teacher_id", str(tid)).execute()).data or []
    ans_map = {str(a["question_id"]): a["answer"] for a in ans_rows}

    config = None
    try:
        config = await _load_exam_config(str(tid), exam_id=exam_id)
    except Exception as e:
        logger.debug("Failed to load exam config for scorecard: %s", e)
    exam_title = (config or {}).get("exam_title") or (config or {}).get("title") or "Exam"

    score = exam.get("score", 0)
    total = exam.get("total", 0)
    pct = exam.get("percentage", 0)
    risk = await compute_risk_score(session_id, teacher_id=tid)
    passed = pct >= 40

    viol_rows = (await _atable("violations")
                 .select("violation_type, severity")
                 .eq("session_key", session_id)
                 .eq("teacher_id", str(tid)).execute()).data or []

    viol_counts: dict[str, dict[str, int]] = {}
    for v in viol_rows:
        vtype = v.get("violation_type", "unknown")
        sev = v.get("severity", "low")
        if vtype not in viol_counts:
            viol_counts[vtype] = {"high": 0, "medium": 0, "low": 0, "total": 0}
        viol_counts[vtype][sev] = viol_counts[vtype].get(sev, 0) + 1
        viol_counts[vtype]["total"] += 1

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    # White-label header: if the teacher's org has a logo_url, render it
    # full-width at the top of the PDF so the scorecard looks like
    # their institute issued it (not Procta). The "Powered by Procta"
    # footer DeepSeek added stays at the bottom as the attribution.
    # We fetch+cache via _fetch_org_logo_image; failure to load is
    # silent — scorecard still renders, just without the header logo.
    org_logo_img = await _fetch_org_logo_image(tid)
    if org_logo_img is not None:
        story.append(org_logo_img)
        story.append(Spacer(1, 14))

    story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(_build_info_table(exam, score, total, pct, risk["label"], passed))
    story.append(Spacer(1, 20))

    if viol_counts:
        story.append(Paragraph("Violation Summary", styles["Heading2"]))
        story.append(Spacer(1, 8))
        story.append(_build_violation_table(viol_counts))
        story.append(Spacer(1, 20))

    story.append(Paragraph("Question-wise Results", styles["Heading2"]))
    story.append(Spacer(1, 8))
    if questions:
        story.append(_build_question_table(questions, ans_map))
    else:
        story.append(Paragraph("No questions available.", styles["Normal"]))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST",
        styles["Normal"]))

    _brand_footer = ParagraphStyle(
        "BrandFooter",
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.HexColor("#94a3b8"),
        alignment=1,
        spaceBefore=24,
    )
    story.append(Spacer(1, 24))
    story.append(Paragraph(
        "Powered by <b>Procta</b> &nbsp;&middot;&nbsp; "
        '<font color="#94a3b8">AI-proctored exams &mdash; procta.net</font>',
        _brand_footer,
    ))

    doc.build(story)
    buf.seek(0)
    roll = _safe_filename(exam.get("roll_number"), "unknown")
    fname = f"scorecard_{roll}_{now_ist().strftime('%Y%m%d')}.pdf"
    summary = {
        "exam": exam, "exam_title": exam_title,
        "score": score, "total": total, "percentage": pct,
        "passed": passed, "risk_label": risk["label"],
        "violations": viol_counts,
        "total_violations": sum(v["total"] for v in viol_counts.values()),
    }
    return buf.getvalue(), fname, summary


# ─── Org-logo helper ────────────────────────────────────────────────
#
# Lazy-imports + an in-process LRU cache keyed by (teacher_id, logo_url)
# so a bulk "email 200 scorecards" run hits the network once per org,
# not once per PDF. Failures are silent: if Fetch / decode / image
# parse fail we just don't render the logo. The scorecard text content
# is the source of truth; the logo is decoration.

_logo_cache: "dict[str, tuple[bytes, str]]" = {}
_LOGO_CACHE_MAX = 64  # at most 64 distinct logos in memory


async def _fetch_org_logo_image(teacher_id):
    """Return a reportlab `Image` ready to drop into a Platypus story,
    or None if the teacher's org has no logo / fetch failed."""
    try:
        rows = (await _atable("teachers")
                .select("org_id")
                .eq("id", str(teacher_id))
                .limit(1)
                .execute()).data or []
        if not rows:
            return None
        org_id = rows[0].get("org_id")
        if not org_id:
            return None
        org_rows = (await _atable("organizations")
                    .select("logo_url")
                    .eq("id", str(org_id))
                    .limit(1)
                    .execute()).data or []
        if not org_rows:
            return None
        url = (org_rows[0].get("logo_url") or "").strip()
        if not url:
            return None

        # App-layer scheme check mirrors admin_org.update_org's validation.
        if not url.lower().startswith(("https://", "data:image/")):
            return None

        # Cache the raw bytes so repeated calls don't re-fetch.
        cached = _logo_cache.get(url)
        if cached is None:
            data = await _download_logo_bytes(url)
            if not data:
                return None
            # Coarse LRU eviction.
            if len(_logo_cache) >= _LOGO_CACHE_MAX:
                _logo_cache.pop(next(iter(_logo_cache)))
            _logo_cache[url] = data
            cached = data

        raw, _content_type = cached
        from reportlab.platypus import Image
        from reportlab.lib.utils import ImageReader
        try:
            reader = ImageReader(io.BytesIO(raw))
            # Cap render width to 180 px so wide banners shrink and
            # square logos don't overpower the page. Aspect-ratio
            # preserved via kind="proportional".
            return Image(reader, width=180, height=60, kind="proportional")
        except Exception:
            logger.debug("scorecard: logo decode failed for %s", url[:60])
            return None
    except Exception:
        logger.debug("scorecard: logo lookup failed", exc_info=True)
        return None


async def _download_logo_bytes(url: str) -> "tuple[bytes, str] | None":
    """Fetch the logo bytes. data:image/ URIs are decoded inline; HTTPS
    URLs are fetched via httpx with a 4-second timeout. Returns
    (bytes, content_type) or None on any failure.

    Cap at 2 MB to prevent a misconfigured admin from blowing memory.
    """
    if url.lower().startswith("data:image/"):
        try:
            import base64
            header, _, payload = url.partition(",")
            mime = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
            raw = base64.b64decode(payload) if ";base64" in header else payload.encode("utf-8")
            if len(raw) > 2 * 1024 * 1024:
                return None
            return raw, mime
        except Exception:
            return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(url, follow_redirects=True)
            if r.status_code != 200:
                return None
            content = r.content[: 2 * 1024 * 1024]  # hard cap
            return content, r.headers.get("content-type", "image/png")
    except Exception:
        return None
