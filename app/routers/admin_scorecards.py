"""Scorecard and export router. Extracted from admin.py."""

import asyncio
import io
import csv
import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from ..dependencies import (
    require_admin, supabase, _atable, _assert_session_owned, _load_questions,
    _load_exam_config, compute_risk_score, _safe_filename,
    _collect_session_screenshots, _match_screenshot_for_violation,
    _html_escape, _xlsx_safe, fmt_ist, now_ist, SessionStatus, _cache,
    _fetch_all_results, _stream_csv_results, limiter,
)
from ..models import EmailScorecardsIn
from ..services.scorecard import _build_scorecard_pdf
from ..jobs import enqueue_job, send_scorecard_email_job

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.get("/api/v1/export-csv")
@limiter.limit("10/minute")
async def export_csv(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    return StreamingResponse(
        _stream_csv_results(teacher["id"], exam_id=exam_id, max_rows=5000),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=results.csv"})


@router.get("/api/v1/export-excel")
@limiter.limit("10/minute")
async def export_excel(request: Request, exam_id: str = None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    teacher = await require_admin(request)
    results = await _fetch_all_results(teacher["id"], exam_id=exam_id)

    wb = Workbook()
    ws = wb.active
    safe_eid = "".join(c for c in (exam_id or "all") if c.isalnum() or c in "-_")[:24]
    ws.title = f"Results_{safe_eid}" if safe_eid else "Results"

    headers = ["Timestamp", "Session ID", "Roll Number", "Full Name",
               "Email", "Score", "Total", "Percentage", "Time (min)",
               "Violations", "Risk Score", "Risk Label"]
    ws.append(headers)

    hdr_fill = PatternFill("solid", fgColor="1A1A2E")
    hdr_font = Font(bold=True, color="FFFFFF")
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = hdr_fill
        c.font = hdr_font
        c.alignment = Alignment(horizontal="center", vertical="center")

    risk_fills = {
        "Low":    PatternFill("solid", fgColor="D1FAE5"),
        "Medium": PatternFill("solid", fgColor="FEF3C7"),
        "High":   PatternFill("solid", fgColor="FEE2E2"),
    }

    for s in results:
        try:
            pct_val = float(s.get("percentage") or 0)
        except Exception:
            pct_val = 0.0
        try:
            secs = int(s.get("time_taken_secs") or 0)
        except Exception:
            secs = 0
        mins = round(secs / 60, 2) if secs else 0

        ws.append([
            _xlsx_safe(s.get("submitted_at", "")),
            _xlsx_safe(s.get("session_id", "")),
            _xlsx_safe(s.get("roll_number", "")),
            _xlsx_safe(s.get("full_name", "")),
            _xlsx_safe(s.get("email", "")),
            s.get("score", 0),
            s.get("total", 0),
            pct_val,
            mins,
            s.get("violation_count", 0),
            s.get("risk_score", ""),
            _xlsx_safe(s.get("risk_label", "")),
        ])
        label = s.get("risk_label")
        fill = risk_fills.get(label)
        if fill:
            ws.cell(row=ws.max_row, column=12).fill = fill

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(ws.max_row,1)}"

    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=8).number_format = '0.0"%"'
        ws.cell(row=row, column=9).number_format = '0.00'

    widths = [0] * len(headers)
    for row in ws.iter_rows(values_only=True):
        for i, v in enumerate(row):
            widths[i] = max(widths[i], min(len(str(v or "")), 40))
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(w + 2, 10)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    try:
        wb.save(tmp.name)
        tmp.close()
        fname = f"results_{safe_eid or 'all'}_{now_ist().strftime('%Y%m%d')}.xlsx"
        return FileResponse(
            tmp.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=fname,
            background=BackgroundTask(os.unlink, tmp.name))
    except Exception:
        os.unlink(tmp.name)
        raise


@router.get("/api/v1/export-pdf/{session_id:path}")
@limiter.limit("10/minute")
async def export_pdf(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer,
                                         Image, PageBreak, KeepTogether)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        exam = await _assert_session_owned(session_id, tid)

        viol_result = await _atable("violations")\
            .select("*")\
            .eq("session_key", session_id)\
            .eq("teacher_id", str(tid))\
            .order("created_at").execute()
        raw_violations = [
            v for v in (viol_result.data or [])
            if v["severity"] in ("high", "medium")
        ]

        ans_result = await _atable("answers")\
            .select("*")\
            .eq("session_key", session_id)\
            .eq("teacher_id", str(tid))\
            .execute()
        answers = ans_result.data or []

        buf    = io.BytesIO()
        doc    = SimpleDocTemplate(buf, pagesize=A4, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story  = []

        story.append(Paragraph("AI Proctored Exam — Report", styles["Title"]))
        story.append(Spacer(1, 12))

        info = [
            ["Field",          "Value"],
            ["Full Name",      exam["full_name"]],
            ["Roll Number",    exam["roll_number"]],
            ["Email",          exam.get("email", "")],
            ["Submitted At",   fmt_ist(exam.get("submitted_at", ""))],
            ["Score",          f"{exam.get('score',0)}/{exam.get('total',0)} "
                               f"({exam.get('percentage',0)}%)"],
            ["Time Taken",     f"{exam.get('time_taken_secs',0)} seconds "
                               f"({exam.get('time_taken_secs',0)//60}m "
                               f"{exam.get('time_taken_secs',0)%60}s)"],
            ["Total Violations", str(len(raw_violations))],
        ]
        risk = await compute_risk_score(session_id, teacher_id=tid)
        info.append(["Behavioral Risk Score",
                     f"{risk['risk_score']}/100 — {risk['label']}"])
        t = Table(info, colWidths=[160, 310])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 10),
            ("ROWBACKGROUNDS", (0,1), (-1,-1),
             [colors.HexColor("#f0f4ff"), colors.white]),
            ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
            ("PADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        story.append(Paragraph(
            f"Violations ({len(raw_violations)} total)", styles["Heading2"]))
        story.append(Spacer(1, 8))

        CONF_MAP = {
            "face_missing": 0.95, "multiple_faces": 0.92,
            "wrong_person": 0.78, "eyes_closed": 0.88,
            "earphone_detected": 0.72, "cheat_object_detected": 0.85,
            "gaze_away": 0.70, "head_away": 0.80,
            "voice_detected": 0.75, "window_focus_lost": 0.99,
            "tab_hidden": 0.99, "lighting_issue": 0.90,
            "shortcut_blocked": 0.99, "camera_covered": 0.95,
        }

        def get_conf(vtype, details):
            det = str(details or "")
            if "confidence:" in det:
                try:
                    raw = det.split("confidence:")[1].split("|")[0].strip()
                    return raw if "%" in raw else f"{raw}%"
                except Exception:
                    pass
            if "conf:" in det:
                try:
                    raw = det.split("conf:")[1].split(" ")[0].strip()
                    val = float(raw)
                    return f"{int(val)}%" if val > 1 else f"{int(val*100)}%"
                except Exception:
                    pass
            return f"{int(CONF_MAP.get(vtype, 0.75) * 100)}%"

        def clean_details(details):
            det = str(details or "")
            raw = det.split("| confidence:")[0].strip()[:40] \
                   if "| confidence:" in det else det[:40]
            return _html_escape(raw)

        if raw_violations:
            total_conf_vals = []
            vd = [["#", "Type", "Severity", "Time", "Conf", "Details"]]
            for i, v in enumerate(raw_violations, 1):
                conf_str = get_conf(v["violation_type"], v.get("details"))
                try:
                    total_conf_vals.append(float(conf_str.strip("%")) / 100)
                except Exception:
                    pass
                ts_part = ""
                if v.get("created_at"):
                    _fmted = fmt_ist(v["created_at"])
                    _comma = _fmted.find(",")
                    if _comma >= 0:
                        ts_part = _fmted[_comma+1:].replace("IST","").strip()
                    else:
                        ts_part = _fmted
                vd.append([
                    str(i),
                    v["violation_type"].replace("_", " ").title()[:22],
                    v["severity"].upper(),
                    ts_part,
                    conf_str,
                    clean_details(v.get("details"))[:35],
                ])
            vt = Table(vd, colWidths=[20, 120, 55, 70, 40, 165])
            vt.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#c0392b")),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 8),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                 [colors.HexColor("#fff5f5"), colors.white]),
                ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                ("PADDING", (0,0), (-1,-1), 5),
                ("ALIGN",   (0,0), (0,-1), "CENTER"),
            ]))
            story.append(vt)
            story.append(Spacer(1, 8))

            if total_conf_vals:
                avg_conf  = sum(total_conf_vals) / len(total_conf_vals)
                high_conf = len([c for c in total_conf_vals if c >= 0.85])
                conf_data = [[
                    f"Overall Detection Confidence: {avg_conf:.0%}",
                    f"High Confidence Violations: {high_conf}/{len(raw_violations)}",
                    f"Reliability: {'High' if avg_conf>=0.85 else 'Medium' if avg_conf>=0.70 else 'Low'}",
                ]]
                ct = Table(conf_data, colWidths=[160, 160, 150])
                ct.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#eafaf1")),
                    ("TEXTCOLOR",  (0,0), (-1,-1), colors.HexColor("#1e8449")),
                    ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
                    ("FONTSIZE",   (0,0), (-1,-1), 8),
                    ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                    ("PADDING", (0,0), (-1,-1), 6),
                    ("ALIGN",   (0,0), (-1,-1), "CENTER"),
                ]))
                story.append(ct)
        else:
            story.append(Paragraph("No violations recorded.", styles["Normal"]))

        roll_for_evidence = exam.get("roll_number") or (
            session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
        )
        evidence_paths = _collect_session_screenshots(roll_for_evidence, str(tid))
        evidence_items = []
        for idx, v in enumerate(raw_violations, 1):
            match = _match_screenshot_for_violation(v, evidence_paths)
            if match is not None and match.exists():
                evidence_items.append((idx, v, match))

        if evidence_items:
            story.append(Spacer(1, 18))
            story.append(PageBreak())
            story.append(Paragraph(
                f"Visual Evidence ({len(evidence_items)} captures)",
                styles["Heading2"]))
            story.append(Paragraph(
                "Screenshots are listed in the same order as the violations table above.",
                styles["Italic"]))
            story.append(Spacer(1, 10))

            evidence_caption_style = ParagraphStyle(
                "EvidenceCaption", parent=styles["Normal"],
                fontSize=9, leading=12, spaceAfter=4,
            )

            for idx, v, img_path in evidence_items:
                ts_str = fmt_ist(v.get("created_at", ""))
                sev = v["severity"].upper()
                sev_color = "#c0392b" if v["severity"] == "high" else "#d68910"
                vtype_pretty = v["violation_type"].replace("_", " ").title()
                caption = (
                    f'<b>#{idx} — {vtype_pretty}</b>  ·  '
                    f'<font color="{sev_color}"><b>{sev}</b></font>  ·  '
                    f'{ts_str}'
                )
                detail = clean_details(v.get("details"))
                if detail:
                    caption += f'<br/><font size="8" color="#666">{detail}</font>'

                try:
                    img_flowable = Image(
                        str(img_path),
                        width=4.5 * inch, height=3.4 * inch,
                        kind="proportional",
                    )
                    story.append(KeepTogether([
                        Paragraph(caption, evidence_caption_style),
                        img_flowable,
                        Spacer(1, 14),
                    ]))
                except Exception as img_err:
                    story.append(Paragraph(
                        caption + f'  <font color="#999">(image unreadable: {img_err})</font>',
                        evidence_caption_style))
                    story.append(Spacer(1, 8))

        story.append(Spacer(1, 20))
        story.append(Paragraph("Answer Sheet", styles["Heading2"]))
        story.append(Spacer(1, 8))

        try:
            pdf_questions = await _load_questions(teacher_id=tid, exam_id=exam.get("exam_id"))
            q_correct = {q["id"]: q["correct"] for q in pdf_questions}
            q_texts = {q["id"]: q.get("question", "")[:50] for q in pdf_questions}
        except Exception as e:
            logger.warning("Failed to load questions for PDF export: %s", e)
            q_correct = {}
            q_texts = {}

        if answers:
            ad = [["#", "Question", "Student", "Correct", "Result"]]
            for a in answers:
                qid = str(a["question_id"])
                correct = q_correct.get(qid, "?")
                is_right = str(a["answer"]) == correct
                ad.append([
                    f"Q{qid}",
                    q_texts.get(qid, "")[:40],
                    a["answer"],
                    correct,
                    "\u2713" if is_right else "\u2717",
                ])
            at = Table(ad, colWidths=[30, 180, 50, 50, 40])
            at.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 9),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                 [colors.HexColor("#f8f9fa"), colors.white]),
                ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                ("PADDING", (0,0), (-1,-1), 6),
            ]))
            story.append(at)

        story.append(Spacer(1, 20))
        story.append(Paragraph(
            f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST | "
            f"Session: {session_id[:20]}...",
            styles["Normal"]))

        doc.build(story)
        buf.seek(0)
        fname = (f"report_{_safe_filename(exam.get('roll_number'), 'unknown')}_"
                 f"{now_ist().strftime('%Y%m%d')}.pdf")
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={fname}"})

    except HTTPException:
        raise
    except Exception as e:
        _admin_log.error("[PDF] %s", e)
        raise HTTPException(status_code=500, detail=f"PDF error: {e}")


@router.get("/api/v1/admin/scorecard-pdf/{session_id:path}")
@limiter.limit("10/minute")
async def scorecard_pdf(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    try:
        pdf_bytes, fname, _ = await _build_scorecard_pdf(session_id, tid)
        return StreamingResponse(
            io.BytesIO(pdf_bytes), media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={fname}"})
    except HTTPException:
        raise
    except Exception as e:
        _admin_log.error("[Scorecard PDF] %s", e)
        raise HTTPException(status_code=500, detail=f"Scorecard PDF error: {e}")


@router.get("/api/v1/admin/scorecard-zip")
@limiter.limit("5/minute")
async def scorecard_zip(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = teacher["id"]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet

        sess_q = _atable("exam_sessions")\
            .select("session_key,roll_number,full_name,score,total,percentage,time_taken_secs,risk_score,started_at,submitted_at,exam_id")\
            .eq("status", SessionStatus.COMPLETED).eq("teacher_id", str(tid))
        if exam_id:
            sess_q = sess_q.eq("exam_id", exam_id)
        sessions = (await sess_q.execute()).data or []
        if not sessions:
            raise HTTPException(status_code=404, detail="No completed sessions found")

        eid = exam_id or (sessions[0].get("exam_id") if sessions else None)
        questions = await _load_questions(teacher_id=tid, exam_id=eid)
        config = None
        try:
            config = await _load_exam_config(str(tid), exam_id=eid)
        except Exception as e:
            logger.debug("Failed to load exam config for ZIP export: %s", e)
        exam_title = (config or {}).get("title", "Exam")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
                for sess in sessions:
                    sid = sess["session_key"]
                    ans_rows = (await _atable("answers").select("question_id,answer")
                                .eq("session_key", sid)
                                .eq("teacher_id", str(tid)).execute()).data or []
                    ans_map = {str(a["question_id"]): a["answer"] for a in ans_rows}

                    pdf_buf = io.BytesIO()
                    doc = SimpleDocTemplate(pdf_buf, pagesize=A4, topMargin=40, bottomMargin=40)
                    styles = getSampleStyleSheet()
                    story = []

                    story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
                    story.append(Spacer(1, 12))

                    score = sess.get("score", 0)
                    total = sess.get("total", 0)
                    pct = sess.get("percentage", 0)
                    passed = pct >= 40

                    info = [
                        ["Field", "Value"],
                        ["Student Name", sess.get("full_name", "")],
                        ["Roll Number", sess.get("roll_number", "")],
                        ["Date", fmt_ist(sess.get("submitted_at", sess.get("started_at", "")))],
                        ["Score", f"{score}/{total}"],
                        ["Percentage", f"{pct}%"],
                        ["Result", "PASS" if passed else "FAIL"],
                        ["Time Taken", f"{sess.get('time_taken_secs', 0) // 60}m {sess.get('time_taken_secs', 0) % 60}s"],
                    ]
                    t = Table(info, colWidths=[140, 330])
                    t.setStyle(TableStyle([
                        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
                        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                        ("FONTNAME",  (0,0), (-1,0), "Helvetica-Bold"),
                        ("FONTSIZE",  (0,0), (-1, -1), 10),
                        ("ROWBACKGROUNDS", (0,1), (-1, -1),
                         [colors.HexColor("#f0f4ff"), colors.white]),
                        ("GRID",    (0,0), (-1, -1), 0.5, colors.grey),
                        ("PADDING", (0,0), (-1, -1), 8),
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 20))

                    story.append(Paragraph("Question-wise Results", styles["Heading2"]))
                    story.append(Spacer(1, 8))

                    if questions:
                        qd = [["#", "Question", "Your Answer", "Correct", "Result"]]
                        for i, q in enumerate(questions, 1):
                            qid = str(q.get("question_id", q.get("id", "")))
                            correct_ans = str(q.get("correct", ""))
                            student_ans = ans_map.get(qid, "\u2014")
                            is_right = str(student_ans) == correct_ans
                            q_text = q.get("question", "")
                            if len(q_text) > 60:
                                q_text = q_text[:57] + "..."
                            qd.append([str(i), q_text, str(student_ans)[:20], correct_ans[:20],
                                       "\u2713" if is_right else "\u2717"])
                        qt = Table(qd, colWidths=[25, 230, 80, 80, 35])
                        qt.setStyle(TableStyle([
                            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
                            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                            ("FONTNAME",  (0,0), (-1,0), "Helvetica-Bold"),
                            ("FONTSIZE",  (0,0), (-1, -1), 9),
                            ("ROWBACKGROUNDS", (0,1), (-1, -1),
                             [colors.HexColor("#f8f9fa"), colors.white]),
                            ("GRID",    (0,0), (-1, -1), 0.5, colors.grey),
                            ("PADDING", (0,0), (-1, -1), 6),
                            ("ALIGN", (4,1), (4, -1), "CENTER"),
                        ]))
                        story.append(qt)

                    story.append(Spacer(1, 20))
                    story.append(Paragraph(
                        f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST",
                        styles["Normal"]))

                    doc.build(story)
                    pdf_buf.seek(0)
                    roll = _safe_filename(sess.get("roll_number"), "unknown")
                    zf.writestr(f"scorecard_{roll}.pdf", pdf_buf.getvalue())

            fname = f"scorecards_{_safe_filename(exam_id, 'all')}_{now_ist().strftime('%Y%m%d')}.zip"
            return FileResponse(
                tmp.name,
                media_type="application/zip",
                filename=fname,
                background=BackgroundTask(os.unlink, tmp.name))
        except Exception:
            os.unlink(tmp.name)
            raise

    except HTTPException:
        raise
    except Exception as e:
        _admin_log.error("[Scorecard ZIP] %s", e)
        raise HTTPException(status_code=500, detail=f"Scorecard ZIP error: {e}")


@router.post("/api/v1/admin/exams/{exam_id}/email-scorecards")
@limiter.limit("5/minute")
async def email_scorecards(exam_id: str, request: Request, body: EmailScorecardsIn = Body(default=EmailScorecardsIn())):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    resend_all = body.resend_all
    custom_message = body.custom_message.strip() or None
    teacher_name = teacher.get("full_name") or teacher.get("email") or "Your teacher"

    sess_q = (await _atable("exam_sessions").select(
        "session_key,roll_number,full_name,exam_id,scorecard_emailed_at"
    ).eq("teacher_id", tid).eq("status", SessionStatus.COMPLETED).eq("exam_id", exam_id)
        .limit(1000))
    sessions = (await sess_q.execute()).data or []
    if not sessions:
        raise HTTPException(status_code=404, detail="No completed sessions found for this exam")

    roll_emails: dict[str, str] = {}
    try:
        inv_rows = (await _atable("student_invites").select("roll_number,email")
                    .eq("teacher_id", tid).eq("exam_id", exam_id).execute()).data or []
        for r in inv_rows:
            roll = str(r.get("roll_number") or "").strip().upper()
            email = str(r.get("email") or "").strip().lower()
            if roll and email:
                roll_emails[roll] = email
    except Exception as e:
        _admin_log.warning("[email-scorecards] invite lookup failed: %s", e)
    try:
        stud_rows = (await _atable("students").select("roll_number,email")
                     .eq("teacher_id", tid).execute()).data or []
        for r in stud_rows:
            roll = str(r.get("roll_number") or "").strip().upper()
            email = str(r.get("email") or "").strip().lower()
            if roll and email and roll not in roll_emails:
                roll_emails[roll] = email
    except Exception as e:
        _admin_log.warning("[email-scorecards] student lookup failed: %s", e)

    sent = 0
    failed = 0
    already_sent = 0
    skipped_no_email = 0
    failures: list[dict] = []

    for sess in sessions:
        sid = sess["session_key"]
        roll = str(sess.get("roll_number") or "").strip().upper()
        full_name = sess.get("full_name") or "Student"

        if sess.get("scorecard_emailed_at") and not resend_all:
            already_sent += 1
            continue

        email = roll_emails.get(roll)
        if not email:
            skipped_no_email += 1
            failures.append({"roll": roll, "reason": "no email on file"})
            continue

        if not resend_all:
            claim = await asyncio.to_thread(
                lambda: supabase.rpc("claim_scorecard_email",
                    {"p_session_key": sid, "p_teacher_id": tid}).execute()
            )
            claimed = claim.data
            if isinstance(claimed, list):
                claimed = claimed[0] if claimed else False
            if not claimed:
                already_sent += 1
                continue

        job_result = enqueue_job(
            send_scorecard_email_job,
            session_key=sid,
            teacher_id=tid,
            email=email,
            full_name=full_name,
            teacher_name=teacher_name,
            custom_message=custom_message,
            resend_all=resend_all,
        )
        if job_result is None:
            sent += 1
        elif job_result.get("ok"):
            sent += 1
        else:
            failed += 1
            failures.append({"roll": roll, "reason": job_result.get("error", "send failed")})

    return {
        "sent": sent,
        "failed": failed,
        "already_sent": already_sent,
        "skipped_no_email": skipped_no_email,
        "total": len(sessions),
        "failures": failures[:50],
    }


@router.get("/api/v1/admin-failed-sessions")
@limiter.limit("30/minute")
async def failed_sessions(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    failed = await _atable("violations").select("session_key")\
        .eq("violation_type", "submit_failed")\
        .eq("teacher_id", tid)\
        .execute()
    failed_keys = {r["session_key"] for r in (failed.data or [])}
    sub_query = _atable("exam_sessions").select("session_key")\
        .eq("status", SessionStatus.COMPLETED)\
        .eq("teacher_id", tid)\
        .in_("session_key", list(failed_keys) or ["__none__"])
    if exam_id:
        sub_query = sub_query.eq("exam_id", exam_id)
    submitted = await sub_query.execute()
    submitted_keys = {r["session_key"] for r in (submitted.data or [])}
    if exam_id:
        es = await _atable("exam_sessions").select("session_key")\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
        exam_skeys = {r["session_key"] for r in (es.data or [])}
        failed_keys = failed_keys & exam_skeys
    unrecovered = [k for k in failed_keys if k not in submitted_keys]
    return {"failed_sessions": unrecovered, "count": len(unrecovered)}


__all__ = ["router"]
