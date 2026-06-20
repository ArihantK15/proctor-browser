"""Scorecard PDF generation service.

Extracted from app/routers/admin.py to reduce the god module.
"""

import io
import logging
from pathlib import Path

from ..repositories.sessions import assert_session_owned as _assert_session_owned
from ..repositories.questions import load_questions as _load_questions, load_exam_config as _load_exam_config
from ..database import async_table as _atable
from ..services.risk import compute_risk_score, _is_violation
from ..utils import _safe_filename, fmt_ist, now_ist

logger = logging.getLogger(__name__)

# Procta brand
_PROCTA_BLUE = "#4a78dc"
_PROCTA_NAVY = "#1a1a2e"
_PROCTA_LOGO = Path(__file__).resolve().parent.parent / "static" / "icon-192.png"


def _procta_brand_header():
    """A Procta-branded header band (logo + wordmark) for the top of a
    scorecard when the teacher's org has no white-label logo of its own, so the
    report visibly comes from Procta. Falls back to a text-only wordmark if the
    icon asset can't be read."""
    from reportlab.lib import colors as _c
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle, Paragraph, Image
    from reportlab.lib.styles import ParagraphStyle

    word = Paragraph(
        f'<font color="{_PROCTA_BLUE}"><b>Procta</b></font>'
        f'<font size="9" color="#94a3b8">&nbsp;&nbsp;AI-proctored exams</font>',
        ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=18, leading=20))
    try:
        logo = Image(str(_PROCTA_LOGO), width=0.42 * inch, height=0.42 * inch, kind="proportional")
        row = [[logo, word]]
        widths = [0.55 * inch, 5.0 * inch]
    except Exception:
        row = [[word]]
        widths = [5.5 * inch]
    t = Table(row, colWidths=widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.4, _c.HexColor(_PROCTA_BLUE)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
    ]))
    return t


def _build_scorecard_evidence(session_id: str, exam: dict, real_violations: list,
                              tid, styles) -> list:
    """Flowables for the 'Visual Evidence' section — the proof screenshots
    captured at each flagged moment, matched from disk (same source the
    dashboard timeline + full report use). Returns [] when there's nothing to
    show. Best-effort throughout: a missing/unreadable image never raises."""
    from reportlab.lib.units import inch
    from reportlab.platypus import (Paragraph, Spacer, Image, KeepTogether,
                                    PageBreak, Table, TableStyle)
    from reportlab.lib.styles import ParagraphStyle
    from .sessions import (collect_session_screenshots,
                           match_screenshot_for_violation,
                           match_room_screenshot_for_violation)

    roll = exam.get("roll_number") or (
        session_id.rsplit("_", 1)[0] if "_" in session_id else session_id)
    try:
        shots = collect_session_screenshots(roll, str(tid))
    except Exception:
        shots = {}
    if not shots:
        return []

    items = []
    for v in real_violations:
        img = match_screenshot_for_violation(v, shots)
        if not img:
            continue
        items.append((v, img, match_room_screenshot_for_violation(v, shots)))
        if len(items) >= 20:
            break
    if not items:
        return []

    cap_style = ParagraphStyle("evcap", parent=styles["Normal"], fontSize=9,
                               leading=12, spaceAfter=4)
    sub_cap = ParagraphStyle("evsub", parent=cap_style, fontSize=8, alignment=1)
    flow = [
        PageBreak(),
        Paragraph(f"Visual Evidence ({len(items)} captures)", styles["Heading2"]),
        Paragraph("Screenshots captured at flagged moments, in chronological order.",
                  styles["Italic"]),
        Spacer(1, 10),
    ]
    for v, img, room in items:
        sev = (v.get("severity") or "low").upper()
        sev_color = "#c0392b" if v.get("severity") == "high" else "#d68910"
        vtype_pretty = (v.get("violation_type") or "").replace("_", " ").title()
        ts = fmt_ist(v.get("created_at", ""))
        caption = (f'<b>{vtype_pretty}</b> &middot; '
                   f'<font color="{sev_color}"><b>{sev}</b></font> &middot; {ts}')
        try:
            if room is not None:
                primary = Image(str(img), width=2.6 * inch, height=2.0 * inch, kind="proportional")
                phone = Image(str(room), width=2.6 * inch, height=2.0 * inch, kind="proportional")
                grid = Table([[primary, phone],
                              [Paragraph("Primary camera", sub_cap),
                               Paragraph("Phone camera", sub_cap)]],
                             colWidths=[2.75 * inch, 2.75 * inch])
                grid.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                          ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
                flow.append(KeepTogether([Paragraph(caption, cap_style), grid, Spacer(1, 14)]))
            else:
                im = Image(str(img), width=4.5 * inch, height=3.4 * inch, kind="proportional")
                flow.append(KeepTogether([Paragraph(caption, cap_style), im, Spacer(1, 14)]))
        except Exception as e:
            logger.warning("scorecard: unreadable screenshot %s: %s", img, e)
    return flow


_END_REASON_LABELS = {
    "academic_dishonesty":  "Suspected academic dishonesty",
    "identity_fraud":       "Identity could not be re-verified",
    "environment_issue":    "Unsuitable exam environment",
    "repeated_violations":  "Repeated proctoring violations",
    "student_request":      "Student requested termination",
    "technical_failure":    "Persistent technical failure",
    "other":                "Other",
}


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
    # Phase 74: surface termination cause when the session was
    # force-submitted by a teacher. Two-line cell: human-readable code
    # label + the optional free-text note. Only renders when the
    # session was actually terminated (otherwise the row is omitted
    # entirely, no blank "Termination: -" eyesore).
    term_code = (exam.get("termination_reason_code") or "").strip()
    term_text = (exam.get("termination_reason_text") or "").strip()
    if exam.get("status") == "force_submitted" and (term_code or term_text):
        label = _END_REASON_LABELS.get(term_code, term_code or "Ended by teacher")
        line = f"Ended by teacher — {label}"
        if term_text:
            line += f"\n\"{term_text}\""
        info.append(["Termination", line])
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
    # Use the authoritative grader so the per-question result matches the actual
    # score. Raw `==` marked multi-select (set-equal but reordered) and
    # numeric-range ("range:MIN:MAX") answers wrong, contradicting the summary
    # score printed on the same page.
    from .scoring import answers_match as _answers_match
    qd = [["#", "Question", "Your Answer", "Correct Answer", "Result"]]
    for i, q in enumerate(questions, 1):
        qid = str(q.get("question_id", q.get("id", "")))
        correct_ans = str(q.get("correct", ""))
        student_ans = ans_map.get(qid, "\u2014")
        is_right = _answers_match(str(student_ans), correct_ans)
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


async def resolve_student_name(exam: dict, teacher_id) -> str:
    """Best-effort display name for a session.

    ``exam_sessions.full_name`` is the primary source, but several join paths
    (early-join, recovered/reattached sessions) leave it empty even though the
    student exists on the roster. Fall back to the roster
    (``students`` by teacher_id + roll_number), then email, then the roll
    number itself, so neither the Scorecard nor the Audit Report ever renders a
    blank / ``None`` student name.
    """
    name = (exam.get("full_name") or "").strip()
    if name:
        return name
    roll = (exam.get("roll_number") or "").strip()
    if roll:
        try:
            rows = (await _atable("students")
                    .select("full_name")
                    .eq("teacher_id", str(teacher_id))
                    .eq("roll_number", roll)
                    .limit(1).execute()).data or []
            if rows and (rows[0].get("full_name") or "").strip():
                return rows[0]["full_name"].strip()
        except Exception:
            logger.debug("resolve_student_name: roster lookup failed", exc_info=True)
    email = (exam.get("email") or "").strip()
    if email:
        return email
    return roll


async def _build_scorecard_pdf(session_id: str, teacher_id) -> tuple[bytes, str, dict]:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Spacer, Paragraph, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader

    tid = teacher_id
    exam = await _assert_session_owned(session_id, tid)
    exam["full_name"] = await resolve_student_name(exam, tid)
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
    passed = pct >= (config.get("pass_mark") or 40)

    viol_rows = (await _atable("violations")
                 .select("id, violation_type, severity, details, created_at")
                 .eq("session_key", session_id)
                 .eq("teacher_id", str(tid)).order("created_at").execute()).data or []

    # Only ACTUAL student-behaviour violations belong in the summary. The
    # violations table also stores lifecycle + diagnostic events (id_verification,
    # calibration, proctor boot/model-load, session reset/abandon, room-cam
    # plumbing, …) which were polluting the scorecard as fake "violations". Gate
    # on the shared _is_violation() so the scorecard, dashboard timeline and risk
    # score all agree on what counts.
    real_violations = [v for v in viol_rows if _is_violation(v.get("violation_type", ""))]

    viol_counts: dict[str, dict[str, int]] = {}
    for v in real_violations:
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
    else:
        # No institute white-label logo → show Procta's own branding so the
        # report visibly comes from us (logo + wordmark + brand rule).
        try:
            story.append(_procta_brand_header())
            story.append(Spacer(1, 14))
        except Exception:
            logger.debug("scorecard: procta brand header failed", exc_info=True)

    story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
    story.append(Paragraph(
        "Official result summary — safe to share with the student and institution.",
        ParagraphStyle("scsub", parent=styles["Normal"], fontSize=9,
                       textColor=colors.HexColor("#666666"), spaceAfter=6)))
    story.append(Spacer(1, 12))
    story.append(_build_info_table(exam, score, total, pct, risk["label"], passed))
    story.append(Spacer(1, 20))

    story.append(Paragraph("Proctoring Violations", styles["Heading2"]))
    story.append(Spacer(1, 8))
    if viol_counts:
        story.append(_build_violation_table(viol_counts))
    else:
        story.append(Paragraph("No proctoring violations detected.", styles["Normal"]))
    story.append(Spacer(1, 20))

    story.append(Paragraph("Question-wise Results", styles["Heading2"]))
    story.append(Spacer(1, 8))
    if questions:
        story.append(_build_question_table(questions, ans_map))
    else:
        story.append(Paragraph("No questions available.", styles["Normal"]))

    # Visual evidence — screenshots captured at each real violation (matched
    # from disk the same way the dashboard timeline + full report do). Best
    # effort: never let a missing/unreadable capture fail the scorecard.
    try:
        story.extend(_build_scorecard_evidence(session_id, exam, real_violations, tid, styles))
    except Exception:
        logger.debug("scorecard: evidence section failed", exc_info=True)

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
