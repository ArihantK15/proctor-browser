"""Scorecard PDF generation service.

Extracted from app/routers/admin.py to reduce the god module.
"""

import io
import logging
from datetime import datetime

from ..dependencies import (
    _assert_session_owned, _load_questions, _atable,
    _load_exam_config, compute_risk_score, _safe_filename,
    fmt_ist, now_ist,
)

logger = logging.getLogger(__name__)


async def _build_scorecard_pdf(session_id: str, teacher_id) -> tuple[bytes, str, dict]:
    """Render a single student's scorecard as a PDF and return
    ``(bytes, filename, exam_summary)``.

    Centralised so the /scorecard-pdf endpoint, the bulk ZIP route,
    and the "email scorecards to students" flow all produce the exact
    same document. ``teacher_id`` must already be validated — this
    helper is trusted-internal; it assumes ownership was asserted by
    the caller.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Table,
                                     TableStyle, Paragraph, Spacer)
    from reportlab.lib.styles import getSampleStyleSheet

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

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
    story.append(Spacer(1, 12))

    score = exam.get("score", 0)
    total = exam.get("total", 0)
    pct = exam.get("percentage", 0)
    risk = await compute_risk_score(session_id, teacher_id=tid)
    passed = pct >= 40

    info = [
        ["Field", "Value"],
        ["Student Name", exam.get("full_name", "")],
        ["Roll Number", exam.get("roll_number", "")],
        ["Date", fmt_ist(exam.get("submitted_at", exam.get("started_at", "")))],
        ["Score", f"{score}/{total}"],
        ["Percentage", f"{pct}%"],
        ["Result", "PASS" if passed else "FAIL"],
        ["Time Taken", f"{exam.get('time_taken_secs', 0) // 60}m {exam.get('time_taken_secs', 0) % 60}s"],
        ["Risk Level", risk["label"]],
    ]
    t = Table(info, colWidths=[140, 330])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("ROWBACKGROUNDS", (0,1), (-1,-1),
         [colors.HexColor("#f0f4ff"), colors.white]),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 20))

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

    HUMAN_NAMES: dict[str, str] = {
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

    if viol_counts:
        story.append(Paragraph("Violation Summary", styles["Heading2"]))
        story.append(Spacer(1, 8))

        vdata = [["Violation", "Count"]]
        for vtype in sorted(viol_counts.keys(), key=lambda k: viol_counts[k]["total"], reverse=True):
            name = HUMAN_NAMES.get(vtype, vtype.replace("_", " ").title())
            vdata.append([name, str(viol_counts[vtype]["total"])])

        vtotal = ["Total", str(sum(v["total"] for v in viol_counts.values()))]
        vdata.append(vtotal)

        vt = Table(vdata, colWidths=[370, 100])
        row_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ]
        for i in range(1, len(vdata)):
            bg = colors.HexColor("#f0f4ff") if i % 2 == 1 else colors.white
            row_style.append(("ROWBACKGROUNDS", (0, i), (-1, i), bg))
        row_style.append(("BACKGROUND", (0, len(vdata) - 1), (-1, len(vdata) - 1),
                          colors.HexColor("#e8ecf4")))
        row_style.append(("FONTNAME", (0, len(vdata) - 1), (-1, len(vdata) - 1),
                          "Helvetica-Bold"))
        vt.setStyle(TableStyle(row_style))
        story.append(vt)
        story.append(Spacer(1, 20))

    story.append(Paragraph("Question-wise Results", styles["Heading2"]))
    story.append(Spacer(1, 8))

    if questions:
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
                str(i),
                q_text,
                str(student_ans)[:20],
                correct_ans[:20],
                "\u2713" if is_right else "\u2717",
            ])
        qt = Table(qd, colWidths=[25, 230, 80, 80, 35])
        qt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("ROWBACKGROUNDS", (0,1), (-1,-1),
             [colors.HexColor("#f8f9fa"), colors.white]),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("PADDING", (0,0), (-1,-1), 6),
            ("ALIGN", (4,1), (4,-1), "CENTER"),
        ]))
        story.append(qt)
    else:
        story.append(Paragraph("No questions available.", styles["Normal"]))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST",
        styles["Normal"]))

    doc.build(story)
    buf.seek(0)
    roll = _safe_filename(exam.get("roll_number"), "unknown")
    fname = f"scorecard_{roll}_{now_ist().strftime('%Y%m%d')}.pdf"
    summary = {
        "exam": exam,
        "exam_title": exam_title,
        "score": score,
        "total": total,
        "percentage": pct,
        "passed": passed,
        "risk_label": risk["label"],
        "violations": viol_counts,
        "total_violations": sum(v["total"] for v in viol_counts.values()),
    }
    return buf.getvalue(), fname, summary
