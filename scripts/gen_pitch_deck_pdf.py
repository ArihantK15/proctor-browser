#!/usr/bin/env python3
"""Generate Procta_Pitch_Deck.pdf.

An investor-grade pitch deck (A4 landscape, one idea per page) that
pulls together the three companion documents already in this repo:

  - scripts/gen_investment_pdf.py     (the capital ask + use of funds)
  - scripts/gen_competitive_pdf.py    (the vetted competitor landscape)
  - scripts/gen_features_pdf.py        (the shipped feature surface)

Editorial principles (inherited from the companion generators):
  - No emojis, no glyphs outside standard Helvetica.
  - Every number reconciles arithmetically and is asserted in main().
  - Every competitor figure is reused VERBATIM from the already-vetted
    competitive report, carries the same "indicative, not a committed
    quote" hedge, and is converted to INR at the same USD 1 = Rs. 95
    rate used across the other documents. Nothing new is fabricated.
  - All charts and diagrams are drawn with reportlab.graphics, so they
    embed as vector directly in the PDF stream. There are no external
    image files, nothing to fail to load, nothing a viewer can block.

Run:    python3 scripts/gen_pitch_deck_pdf.py
Output: Procta_Pitch_Deck.pdf in repo root.
"""
from datetime import date

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, Color
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether, ListFlowable, ListItem, Flowable,
)
from reportlab.graphics.shapes import (
    Drawing, Rect, String, Line, Polygon, Group,
)
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie


# ── Brand palette (mirrors the companion generators) ───────────────
ACCENT      = HexColor("#5b6df0")
ACCENT_DARK = HexColor("#404bb8")
ACCENT_SOFT = HexColor("#aab2f7")
INK         = HexColor("#0d1117")
MUTED       = HexColor("#6b7280")
BORDER      = HexColor("#e2e8f0")
BG_SOFT     = HexColor("#f5f7fb")
BG_ROW_ALT  = HexColor("#fafbff")
EMERALD     = HexColor("#0e8a5b")
EMERALD_SOFT= HexColor("#d7f0e4")
AMBER       = HexColor("#a55e0a")
RED         = HexColor("#9b2c2c")
RED_SOFT    = HexColor("#f3d9d9")

# Distinct categorical colours for charts (color-blind-considerate, no
# pure red/green adjacency).
CHART_COLORS = [
    HexColor("#5b6df0"),  # indigo
    HexColor("#0e8a5b"),  # emerald
    HexColor("#a55e0a"),  # amber
    HexColor("#7c3aed"),  # violet
    HexColor("#0891b2"),  # cyan
    HexColor("#be185d"),  # magenta
]


# ── Styles ─────────────────────────────────────────────────────────
styles = getSampleStyleSheet()
KICKER = ParagraphStyle("Kicker", parent=styles["Normal"],
                        fontName="Helvetica-Bold", fontSize=10, leading=12,
                        textColor=ACCENT, spaceAfter=4, alignment=TA_LEFT)
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                    fontSize=26, leading=30, textColor=INK, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=20, leading=24, textColor=INK, spaceAfter=8)
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontName="Helvetica-Bold",
                    fontSize=12, leading=16, textColor=ACCENT_DARK,
                    spaceBefore=6, spaceAfter=3)
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontName="Helvetica",
                      fontSize=11, leading=16, textColor=INK, spaceAfter=6,
                      alignment=TA_LEFT)
BODY_J = ParagraphStyle("BodyJ", parent=BODY, alignment=TA_JUSTIFY)
LEAD = ParagraphStyle("Lead", parent=BODY, fontSize=14, leading=20,
                      textColor=INK, spaceAfter=10)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, leading=11,
                       textColor=MUTED, spaceAfter=3)
BIGNUM = ParagraphStyle("BigNum", parent=H1, fontSize=34, leading=36,
                        textColor=ACCENT_DARK, spaceAfter=2)
STAT_LABEL = ParagraphStyle("StatLabel", parent=SMALL, fontSize=9, leading=12,
                            textColor=MUTED, spaceAfter=0)
TBL_HEAD = ParagraphStyle("TH", parent=BODY, fontName="Helvetica-Bold",
                          fontSize=9.5, leading=12, textColor=white,
                          alignment=TA_CENTER)
TBL_HEAD_L = ParagraphStyle("THL", parent=TBL_HEAD, alignment=TA_LEFT)
TBL_CELL = ParagraphStyle("TC", parent=BODY, fontSize=9.5, leading=12,
                          textColor=INK, alignment=TA_LEFT, spaceAfter=0)
TBL_CELL_C = ParagraphStyle("TCC", parent=TBL_CELL, alignment=TA_CENTER)
TBL_CELL_B = ParagraphStyle("TCB", parent=TBL_CELL, fontName="Helvetica-Bold")


# ── INR formatter (Indian comma grouping) ──────────────────────────
def inr(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) <= 3:
        body = s
    else:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.append(head[-2:]); head = head[:-2]
        if head:
            groups.append(head)
        body = ",".join(reversed(groups)) + "," + tail
    return f"Rs.{'-' if n < 0 else ''}{body}"


# ── Page geometry (A4 landscape) ───────────────────────────────────
PAGE_W, PAGE_H = landscape(A4)
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN     # usable width


# ── Generic table builder ──────────────────────────────────────────
def make_table(rows, col_widths, header=True, center_from_col=1,
               font_size=9.5, header_align_left=False):
    paras = []
    for r_i, row in enumerate(rows):
        prow = []
        for c_i, cell in enumerate(row):
            txt = cell if cell else ""
            if header and r_i == 0:
                prow.append(Paragraph(txt, TBL_HEAD_L if header_align_left
                                      else (TBL_HEAD_L if c_i == 0 else TBL_HEAD)))
            elif c_i == 0:
                prow.append(Paragraph(txt, TBL_CELL_B))
            elif c_i >= center_from_col:
                prow.append(Paragraph(txt, TBL_CELL_C))
            else:
                prow.append(Paragraph(txt, TBL_CELL))
        paras.append(prow)
    t = Table(paras, colWidths=[w * mm for w in col_widths], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, BG_ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    t.setStyle(TableStyle(style))
    return t


# ── Two-column page layout helper ──────────────────────────────────
def two_col(left_flowables, right_flowables, left_w=None, right_w=None,
            gutter=10):
    """Place two stacks of flowables side by side via an invisible table."""
    if left_w is None and right_w is None:
        left_w = right_w = (CONTENT_W / mm - gutter) / 2
    inner = Table(
        [[left_flowables, right_flowables]],
        colWidths=[left_w * mm, gutter * mm + right_w * mm - gutter * mm
                   if False else right_w * mm],
    )
    # simpler: explicit widths
    inner = Table([[left_flowables, right_flowables]],
                  colWidths=[left_w * mm, right_w * mm])
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), gutter * mm / 2),
        ("LEFTPADDING", (1, 0), (1, 0), gutter * mm / 2),
        ("RIGHTPADDING", (1, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return inner


def stat_card(big, label, color=ACCENT_DARK, width_mm=60):
    """A single KPI card flowable (tinted box). width_mm must be set by
    the caller to the column it lives in - never the full page width."""
    p_big = Paragraph(big, ParagraphStyle("sb", parent=BIGNUM, textColor=color,
                                          fontSize=22, leading=24))
    p_lab = Paragraph(label, STAT_LABEL)
    t = Table([[p_big], [p_lab]], colWidths=[width_mm * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_SOFT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, 0), (0, 0), 1),
        ("TOPPADDING", (1, 0), (1, 0), 0),
        ("BOTTOMPADDING", (-1, -1), (-1, -1), 8),
    ]))
    return t


def kpi_row(specs, total_w_mm):
    """specs = list of (big, label, color). total_w_mm = the width of the
    column this row sits in, so cards never overflow into a neighbour."""
    n = len(specs)
    gap = 4  # mm between cards
    cw = (total_w_mm - gap * (n - 1)) / n
    cells = [stat_card(b, label, c, width_mm=cw - 4) for (b, label, c) in specs]
    t = Table([cells], colWidths=[cw * mm] * n)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 0),
        ("LEFTPADDING", (1, 0), (-1, 0), gap * mm / 2),
        ("RIGHTPADDING", (0, 0), (-2, 0), gap * mm / 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


# ── CHARTS (all vector, embedded directly) ─────────────────────────

def bar_chart(labels, values, value_fmt, width=230, height=95,
              bar_colors=None, title_each=None, highlight_index=0):
    """Vertical bar chart. Returns a Drawing flowable. Values are drawn
    as data labels above each bar so the chart reads even in greyscale."""
    d = Drawing(width, height)
    bc = VerticalBarChart()
    bc.x = 30
    bc.y = 22
    bc.width = width - 45
    bc.height = height - 42
    bc.data = [values]
    bc.barWidth = 8
    bc.groupSpacing = 10
    bc.valueAxis.valueMin = 0
    bc.valueAxis.valueMax = max(values) * 1.18
    bc.valueAxis.visible = False
    bc.categoryAxis.labels.boxAnchor = "n"
    bc.categoryAxis.labels.fontName = "Helvetica"
    bc.categoryAxis.labels.fontSize = 7
    bc.categoryAxis.labels.dy = -3
    bc.categoryAxis.labels.angle = 0
    bc.categoryAxis.categoryNames = labels
    bc.categoryAxis.strokeColor = BORDER
    # per-bar colour
    if bar_colors is None:
        bar_colors = [ACCENT if i == highlight_index else ACCENT_SOFT
                      for i in range(len(values))]
    for i, c in enumerate(bar_colors):
        bc.bars[(0, i)].fillColor = c
        bc.bars[(0, i)].strokeColor = c
    d.add(bc)
    # data labels above bars
    vmax = bc.valueAxis.valueMax
    for i, v in enumerate(values):
        n = len(values)
        bx = bc.x + bc.groupSpacing / 2 + (bc.width - bc.groupSpacing) * (i + 0.5) / n
        by = bc.y + (bc.height * (v / vmax)) + 3
        d.add(String(bx, by, value_fmt(v), fontName="Helvetica-Bold",
                     fontSize=7.5, fillColor=INK, textAnchor="middle"))
    return d


def pie_chart(labels, values, colors=None, width=180, height=150):
    """Donut-style pie with an external legend table. Returns a Table
    flowable [pie | legend] so labels never overlap the slices."""
    if colors is None:
        colors = [CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(values))]
    d = Drawing(width, height)
    pie = Pie()
    pie.x = 20
    pie.y = 18
    pie.width = 112
    pie.height = 112
    pie.data = values
    pie.innerRadiusFraction = 0.45
    pie.simpleLabels = 1
    pie.slices.strokeColor = white
    pie.slices.strokeWidth = 1.2
    for i, c in enumerate(colors):
        pie.slices[i].fillColor = c
    # percent labels on slices
    pie.labels = [f"{round(100*v/sum(values))}%" for v in values]
    pie.slices.fontName = "Helvetica-Bold"
    pie.slices.fontSize = 8
    pie.slices.fontColor = INK
    d.add(pie)
    # build legend rows as a small table
    sum(values)
    legend_rows = []
    for i, lab in enumerate(labels):
        swatch = Drawing(9, 9)
        swatch.add(Rect(0, 0, 9, 9, fillColor=colors[i], strokeColor=colors[i]))
        legend_rows.append([
            swatch,
            Paragraph(f"<b>{lab}</b>", ParagraphStyle(
                "lg", parent=SMALL, fontSize=8.5, textColor=INK, leading=11)),
            Paragraph(inr(values[i]), ParagraphStyle(
                "lgv", parent=SMALL, fontSize=8.5, textColor=MUTED,
                leading=11, alignment=TA_RIGHT)),
        ])
    legend = Table(legend_rows, colWidths=[5 * mm, 52 * mm, 20 * mm])
    legend.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    wrap = Table([[d, legend]], colWidths=[width, 80 * mm])
    wrap.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return wrap


# ── Architecture / data-flow diagram (pure vector) ─────────────────
def _box(g, x, y, w, h, lines, fill, stroke, text_color=INK, bold_first=True):
    g.add(Rect(x, y, w, h, fillColor=fill, strokeColor=stroke,
               strokeWidth=1, rx=4, ry=4))
    n = len(lines)
    line_h = 11
    start_y = y + h / 2 + (n - 1) * line_h / 2 - 3
    for i, ln in enumerate(lines):
        fn = "Helvetica-Bold" if (bold_first and i == 0) else "Helvetica"
        fs = 8.5 if (bold_first and i == 0) else 7.5
        g.add(String(x + w / 2, start_y - i * line_h, ln, fontName=fn,
                     fontSize=fs, fillColor=text_color, textAnchor="middle"))


def _arrow(g, x1, y, x2, color=ACCENT_DARK, label=None):
    g.add(Line(x1, y, x2 - 6, y, strokeColor=color, strokeWidth=1.5))
    g.add(Polygon([x2, y, x2 - 7, y + 4, x2 - 7, y - 4],
                  fillColor=color, strokeColor=color))
    if label:
        g.add(String((x1 + x2) / 2, y + 6, label, fontName="Helvetica",
                     fontSize=6.8, fillColor=MUTED, textAnchor="middle"))


def architecture_diagram(width=640, height=150):
    d = Drawing(width, height)
    g = Group()
    bw, bh = 116, 66
    gap = 56                  # room for an arrow + its label
    pad = 6                   # left inset
    yb = 46
    boxes = [
        (["Student device", "Camera + mic", "On-device ML", "(face / gaze /",
          "object / voice)"], BG_SOFT, ACCENT),
        (["On-device decision", "Frames analysed,", "then DISCARDED",
          "(no raw video)"], EMERALD_SOFT, EMERALD),
        (["Mumbai backend", "FastAPI + Postgres", "+ Redis", "Stores only",
          "flagged JPEGs"], BG_SOFT, ACCENT),
        (["Teacher dashboard", "AI triage +", "cluster review", "+ scorecards"],
         BG_SOFT, ACCENT),
    ]
    xs = [pad + i * (bw + gap) for i in range(4)]
    for x, (lines, fill, stroke) in zip(xs, boxes):
        _box(g, x, yb, bw, bh, lines, fill, stroke, bold_first=True)
    labels = ["analyse", "flagged JPEG only", "live + review"]
    for i in range(3):
        _arrow(g, xs[i] + bw, yb + bh / 2, xs[i + 1], label=labels[i])
    g.add(String(width / 2, 18,
                 "Raw camera frames never leave the student device - only "
                 "short JPEG snapshots of flagged moments are uploaded.",
                 fontName="Helvetica-Oblique", fontSize=8.5, fillColor=MUTED,
                 textAnchor="middle"))
    g.add(String(width / 2, height - 10, "On-device ML pipeline (data-flow)",
                 fontName="Helvetica-Bold", fontSize=10, fillColor=INK,
                 textAnchor="middle"))
    d.add(g)
    return d


def coding_flow_diagram(width=640, height=150):
    d = Drawing(width, height)
    g = Group()
    bw, bh = 116, 66
    gap = 56
    pad = 6
    yb = 46
    boxes = [
        (["Student", "CodeMirror editor", "Python / JS / TS /",
          "C / C++ / Java"], BG_SOFT, ACCENT),
        (["Run (sample tests)", "Judged against", "public examples,",
          "instant feedback"], BG_SOFT, ACCENT),
        (["Submit (hidden tests)", "isolate sandbox,", "no network,",
          "CPU/mem/time capped"], EMERALD_SOFT, EMERALD),
        (["Gradebook", "Pass/fail per case", "recorded", "authoritatively"],
         BG_SOFT, ACCENT),
    ]
    xs = [pad + i * (bw + gap) for i in range(4)]
    for x, (lines, fill, stroke) in zip(xs, boxes):
        _box(g, x, yb, bw, bh, lines, fill, stroke, bold_first=True)
    labels = ["writes + runs", "when ready", "graded server-side"]
    for i in range(3):
        _arrow(g, xs[i] + bw, yb + bh / 2, xs[i + 1], label=labels[i])
    g.add(String(width / 2, 18,
                 "The student never sees hidden-test inputs or expected "
                 "outputs - only pass/fail after Submit.",
                 fontName="Helvetica-Oblique", fontSize=8.5, fillColor=MUTED,
                 textAnchor="middle"))
    g.add(String(width / 2, height - 10,
                 "Inbuilt coding questions (execution flow)",
                 fontName="Helvetica-Bold", fontSize=10, fillColor=INK,
                 textAnchor="middle"))
    d.add(g)
    return d


# ── Section title helper ───────────────────────────────────────────
def section_head(elements, kicker, title):
    elements.append(Paragraph(kicker.upper(), KICKER))
    elements.append(Paragraph(title, H2))
    elements.append(Spacer(1, 4))


# ════════════════════════════════════════════════════════════════════
#  SLIDES
# ════════════════════════════════════════════════════════════════════

def slide_cover(elements):
    elements.append(Spacer(1, 48 * mm))
    elements.append(Paragraph('<font color="#5b6df0">Procta</font>',
                              ParagraphStyle("Brand", parent=H1, fontSize=58,
                                             leading=62, spaceAfter=6)))
    elements.append(Paragraph(
        "AI-proctored exams, built for India.",
        ParagraphStyle("Tag", parent=H1, fontSize=24, leading=30,
                       textColor=INK, spaceAfter=14)))
    elements.append(Paragraph(
        "On-device AI proctoring with zero raw-video storage, native UPI "
        "and GST billing, and a published per-student price roughly an "
        "order of magnitude below the incumbents.",
        ParagraphStyle("Sub", parent=LEAD, fontSize=15, textColor=MUTED,
                       leading=22)))
    elements.append(Spacer(1, 14 * mm))
    elements.append(Paragraph(
        f"Investor briefing &nbsp;|&nbsp; {date.today().strftime('%B %Y')} "
        "&nbsp;|&nbsp; Seeking pre-seed (~Rs. 4.8 lakh, 18-month runway)",
        BODY))
    elements.append(PageBreak())


def slide_problem(elements):
    section_head(elements, "The problem",
                 "India runs more high-stakes exams than anywhere on earth - "
                 "and the proctoring tools don't fit.")
    left = [
        Paragraph("Coaching institutes and universities run millions of "
                  "proctored exams a year, but the available platforms were "
                  "built for someone else:", BODY),
        ListFlowable([
            ListItem(Paragraph("<b>Priced in dollars.</b> US vendors "
                     "(Honorlock, Proctortrack) charge per-attempt rates "
                     "that convert to hundreds of rupees, plus FX risk.", BODY)),
            ListItem(Paragraph("<b>Incumbents drifted away.</b> Mercer Mettl "
                     "pivoted toward enterprise hiring assessments; education "
                     "is no longer the core motion.", BODY)),
            ListItem(Paragraph("<b>Heavy by design.</b> Continuous video "
                     "upload means high bandwidth on budget student laptops "
                     "and a large privacy surface under the DPDP Act.", BODY)),
            ListItem(Paragraph("<b>Opaque pricing.</b> No competitor "
                     "publishes a per-student rate, so every procurement "
                     "conversation starts from zero.", BODY)),
        ], bulletType="bullet", start="circle"),
    ]
    right = [
        kpi_row([
            ("Dollar-priced", "incumbent per-attempt cost", AMBER),
            ("Video-heavy", "continuous upload by default", AMBER),
        ], total_w_mm=125),
        Spacer(1, 6),
        Paragraph("The gap is structural, not cosmetic: it is a cost-base "
                  "and architecture mismatch that an India-built, on-device "
                  "platform is positioned to close.", BODY_J),
    ]
    elements.append(two_col(left, right))
    elements.append(PageBreak())


def slide_solution(elements):
    section_head(elements, "The solution",
                 "Procta does the AI on the student's device, so the cost "
                 "base and the privacy surface both collapse.")
    elements.append(Paragraph(
        "Face detection, gaze tracking, head-pose, cheat-object detection, "
        "voice activity and spoken-keyword spotting all run client-side on "
        "the student's CPU. The server only ever sees short JPEG snapshots "
        "of flagged moments - never a continuous video stream.", BODY_J))
    elements.append(Spacer(1, 6))
    elements.append(architecture_diagram())
    elements.append(PageBreak())


def slide_features(elements):
    section_head(elements, "Product",
                 "A complete exam platform, not just a camera widget.")
    col1 = [
        Paragraph("Live proctoring engine", H3),
        ListFlowable([
            ListItem(Paragraph("8 detection signals: face, gaze, head-pose, "
                     "YOLO cheat-objects, audio, behavioural correlation, "
                     "wrong-person, multi-face.", BODY)),
            ListItem(Paragraph("Spoken-keyword detection (Vosk en-IN + hi-IN) "
                     "- unique among the compared vendors.", BODY)),
            ListItem(Paragraph("Phone-camera secondary view via QR pairing, "
                     "no separate app install.", BODY)),
        ], bulletType="bullet", start="circle"),
        Paragraph("Lockdown client", H3),
        ListFlowable([
            ListItem(Paragraph("Electron kiosk browser with process-integrity "
                     "scan, auto-update, and offline answer resilience.", BODY)),
        ], bulletType="bullet", start="circle"),
    ]
    col2 = [
        Paragraph("Teacher dashboard", H3),
        ListFlowable([
            ListItem(Paragraph("Live sessions, AI triage, forensics timeline, "
                     "cluster review for bulk false-positive dismissal.", BODY)),
            ListItem(Paragraph("Question bank, templates, groups, in-exam "
                     "chat, onboarding wizard.", BODY)),
        ], bulletType="bullet", start="circle"),
        Paragraph("Grading and assessment", H3),
        ListFlowable([
            ListItem(Paragraph("Auto-graded MCQ, AI-suggested short-answer "
                     "grading, composite risk score, AI session narrative.", BODY)),
            ListItem(Paragraph("Inbuilt coding questions: server-side "
                     "sandboxed execution judges Python, JS/TS, C/C++, and "
                     "Java against hidden tests - a wedge into CS-department "
                     "and technical-hiring evaluation none of the compared "
                     "vendors compete in.", BODY)),
            ListItem(Paragraph("Scorecard PDFs, bulk ZIP export, emailed "
                     "results.", BODY)),
        ], bulletType="bullet", start="circle"),
    ]
    elements.append(two_col(col1, col2))
    elements.append(Spacer(1, 10))
    elements.append(coding_flow_diagram())
    elements.append(PageBreak())


def slide_low_cost(elements):
    section_head(elements, "Why our running cost is so low",
                 "We removed the two biggest cost drivers of every other "
                 "platform: server-side inference and continuous video.")
    left = [
        Paragraph("Most proctoring platforms pay twice per student: once to "
                  "run ML on their own servers, and again to ingest and store "
                  "continuous video. Procta does neither.", BODY_J),
        ListFlowable([
            ListItem(Paragraph("<b>No server inference.</b> All ML runs on the "
                     "student device, so cost does not scale with concurrent "
                     "exam-takers - the expensive part is free to us.", BODY)),
            ListItem(Paragraph("<b>No continuous video.</b> Only flagged JPEG "
                     "snapshots are uploaded - roughly <b>88x</b> less data "
                     "per exam-hour than continuous-upload competitors.", BODY)),
            ListItem(Paragraph("<b>One Mumbai region.</b> A single Hostinger VPS "
                     "plus Redis, not multi-region video-streaming infrastructure.", BODY)),
        ], bulletType="bullet", start="circle"),
        Paragraph("Net effect: the entire company runs on an 18-month budget "
                  "of about Rs. 4.8 lakh - where a video-first competitor's "
                  "cloud bill alone would exceed that.", BODY_J),
    ]
    chart = bar_chart(
        ["Procta\n(flagged only)", "Continuous-upload\ncompetitor"],
        [12, 1055],
        value_fmt=lambda v: f"{v:.0f} MB",
        width=200, height=150,
        bar_colors=[EMERALD, AMBER], highlight_index=0)
    right = [
        Paragraph("Data uploaded per student per exam-hour", H3),
        chart,
        Paragraph("Procta ~12 MB/hr (flagged JPEGs only) vs ~1,055 MB/hr for "
                  "continuous video at 300 KB/s. Lower data = lower cost and "
                  "a smaller DPDP privacy surface. Figures are engineering "
                  "estimates from the architecture, consistent with the "
                  "30-100x range in the competitive report.", SMALL),
    ]
    elements.append(two_col(left, right, left_w=150, right_w=110))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "The trade-off, stated plainly: on-device ML makes the desktop "
        "client heavy, not light - a one-time ~130 MB installer that caches "
        "roughly 2 GB of models locally and uses the student's CPU during "
        "the exam. That is the deliberate design choice, not a flaw: we shift "
        "the compute cost off our servers and onto hardware the student "
        "already owns, which is precisely what makes our operating cost and "
        "privacy surface so low. The CPU-adaptive hardware governor keeps it "
        "usable on budget laptops, and the install and model download happen "
        "once, not per exam.", SMALL))
    elements.append(PageBreak())


def slide_security(elements):
    section_head(elements, "Security and privacy",
                 "Privacy-by-construction, and DPDP Act alignment that "
                 "shortens the IT security review.")
    rows = [
        ["Measure", "How Procta implements it"],
        ["Zero raw-video storage",
         "Camera frames analysed on-device and discarded; only JPEG snapshots "
         "of flagged moments are uploaded as evidence."],
        ["On-device ML",
         "RetinaFace, gaze, YOLOv8n, Silero VAD and Vosk all run client-side - "
         "no model inference or video on Procta servers."],
        ["DPDP Act alignment",
         "Explicit pre-exam consent, data minimisation, 90-day default "
         "retention with auto-purge, Mumbai-region data residency, export and "
         "erasure endpoints."],
        ["Tenant isolation",
         "Row-Level Security (native Postgres) on every table; JWT-scoped "
         "session ownership; evidence encrypted at rest in AWS S3 Mumbai; "
         "answer keys envelope-encrypted (AES-256-GCM) at the application "
         "layer."],
        ["Hardened client",
         "Electron kiosk lockdown, sandboxed renderers, CSP, and runtime "
         "process-integrity scanning."],
        ["Transport and headers",
         "HSTS (preload-eligible), CSP, ETag and GZip; Sentry error monitoring; "
         "structured audit logging."],
    ]
    elements.append(make_table(rows, col_widths=[55, 210], center_from_col=99,
                               header_align_left=True))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph(
        "Because Procta uploads far less personal data than continuous-video "
        "competitors, the data-minimisation and residency obligations under "
        "DPDP are easier to satisfy by construction - a procurement unlock for "
        "Indian institutions.", SMALL))
    elements.append(PageBreak())


def slide_why_us(elements):
    section_head(elements, "Why customers choose Procta",
                 "The only vendor that is on-device, zero-raw-video, "
                 "INR-priced, and publicly priced - all at once.")
    rows = [
        ["", "Procta", "US vendors", "Mercer Mettl"],
        ["On-device ML (no server inference)", "Yes", "No", "No"],
        ["Zero raw-video storage by default", "Yes", "No", "No"],
        ["Spoken-keyword detection (Hindi/English)", "Yes", "No", "No"],
        ["Phone-camera secondary view, no app install", "Yes", "Partial", "Partial"],
        ["INR pricing + GST + UPI Autopay", "Yes", "No", "Partial"],
        ["Public per-student rate card", "Yes", "No", "No"],
        ["Built for DPDP Act / Mumbai residency", "Yes", "Partial", "Yes"],
        ["Inbuilt coding-question judging", "Yes", "No", "No"],
        ["Live human-proctor team (where we are weaker)", "No", "Yes", "Yes"],
    ]
    elements.append(make_table(rows, col_widths=[120, 30, 38, 38],
                               center_from_col=1))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph(
        "Honest framing: incumbents still lead on live human-proctor "
        "headcount, deep Canvas/D2L LMS connectors, and assessment-authoring "
        "breadth. Procta competes on architecture, India fit, and price - not "
        "on enterprise feature depth. (Detail in the Competitive Comparison "
        "Report.)", SMALL))
    elements.append(PageBreak())


def slide_market(elements):
    section_head(elements, "Market target",
                 "Land in the wedge that pays cash now: Indian coaching "
                 "institutes.")
    left = [
        Paragraph("Beachhead: coaching institutes", H3),
        Paragraph("Allen, Aakash, Vedantu, PW and their peers run more "
                  "proctored exams per month than most universities run per "
                  "year, carry real edtech budgets, and have no loyalty to the "
                  "incumbents. They are comfortable self-hosting and they pay "
                  "in cash today.", BODY_J),
        Paragraph("Expand: universities and certification bodies", H3),
        Paragraph("State universities and Tier-2/3 colleges follow on price "
                  "and DPDP fit; AI short-answer grading is the wedge into "
                  "larger institutions.", BODY_J),
        Paragraph("Budget figures are founder estimates from primary market "
                  "conversations, not audited market research.", SMALL),
    ]
    right = [
        kpi_row([
            ("Cash now", "coaching institutes buy today", EMERALD),
            ("DPDP", "compliance is a procurement unlock", ACCENT_DARK),
        ], total_w_mm=110),
        Spacer(1, 8),
        Paragraph("Go-to-market sequence", H3),
        ListFlowable([
            ListItem(Paragraph("1. One coaching-chain pilot -> reference logo.", BODY)),
            ListItem(Paragraph("2. Per-student pricing + UPI Autopay self-serve loop.", BODY)),
            ListItem(Paragraph("3. Privacy/DPDP story to unlock universities.", BODY)),
        ], bulletType="bullet", start="circle"),
    ]
    elements.append(two_col(left, right, left_w=150, right_w=110))
    elements.append(PageBreak())


def slide_comparison(elements, pricing):
    section_head(elements, "Company comparison",
                 "Same capability lens, honest about where we are weaker.")
    rows = [
        ["Capability", "Procta", "Mettl", "Talview", "Proctortrk", "Honorlock"],
        ["On-device ML inference", "Y", "N", "N", "N", "N"],
        ["Zero raw-video by default", "Y", "N", "N", "N", "N"],
        ["Spoken-keyword detection", "Y", "N", "N", "N", "N"],
        ["Phone-camera secondary view", "Y", "P", "P", "Y", "N"],
        ["UPI Autopay subscriptions", "Y", "N", "N", "N", "N"],
        ["Public per-student price card", "Y", "N", "N", "N", "N"],
        ["Live human-proctor escalation", "N", "Y", "Y", "Y", "Y"],
        ["Canvas LMS depth", "P", "Y", "P", "Y", "Y"],
        ["Inbuilt coding-question judging", "Y", "N", "N", "N", "N"],
    ]
    left = [
        make_table(rows, col_widths=[50, 16, 15, 17, 24, 24], center_from_col=1),
        Spacer(1, 3),
        Paragraph("Y = advertised; P = partial; N = not advertised. Reused "
                  "verbatim from the vetted Competitive Comparison Report.", SMALL),
    ]
    labels = ["Procta", "Honorlk", "Mettl", "Proctk", "Talview"]
    values = [pricing[k] for k in ["procta", "honorlock", "mettl",
                                   "proctortrack", "talview"]]
    chart = bar_chart(labels, values,
                      value_fmt=lambda v: inr(v).replace("Rs.", ""),
                      width=200, height=150,
                      bar_colors=[EMERALD] + [ACCENT_SOFT] * 4,
                      highlight_index=0)
    right = [
        Paragraph("Indicative price per attempt (INR)", H3),
        chart,
        Paragraph("Competitor figures are public-source midpoints converted "
                  "at USD 1 = Rs. 95 (Mettl from Indian RFP disclosures). "
                  "Indicative, not committed quotes - see the Competitive "
                  "Comparison Report for ranges and sources.", SMALL),
    ]
    elements.append(two_col(left, right, left_w=150, right_w=110))
    elements.append(PageBreak())


def slide_ask(elements, comp, scenario_a, scenario_b):
    section_head(elements, "The ask",
                 "Pre-seed ~Rs. 4.8 lakh for an 18-month ramped sprint.")
    left = [
        kpi_row([
            (inr(scenario_b), "Scenario B (ramped, 18 mo)", ACCENT_DARK),
            (inr(scenario_a), "Scenario A (lean floor)", MUTED),
        ], total_w_mm=128),
        Spacer(1, 8),
        Paragraph("What the capital buys", H3),
        ListFlowable([
            ListItem(Paragraph("18 months of operations with a real paid "
                     "acquisition channel running.", BODY)),
            ListItem(Paragraph("Rs. 10,000/month performance marketing for the "
                     "full window.", BODY)),
            ListItem(Paragraph("Prepaid 2-year hosting (primary + secondary "
                     "server) and domain, dev tools, and Windows EV "
                     "code-signing.", BODY)),
            ListItem(Paragraph("Private Limited incorporation and its first "
                     "18 months of ROC/audit/CA compliance - a precondition "
                     "of this round closing, not a deferred cost.", BODY)),
        ], bulletType="bullet", start="circle"),
        Paragraph("Excluded by design (so the rest is trusted): the second "
                  "Windows cert issuance the 459-day validity cap forces "
                  "around month 15, GST filing, founder living costs, "
                  "Year-3 hosting renewal, and a future support hire.", SMALL),
    ]
    right = [
        Paragraph("Use of funds (Scenario B)", H3),
        pie_chart(
            ["Performance marketing", "Developer tools / software",
             "Operations (email, Vercel)", "Hosting and infrastructure",
             "Setup and procurement (one-time)", "Windows EV code-signing",
             "Apple Developer Program", "Pvt Ltd incorporation",
             "Pvt Ltd compliance (18 mo)"],
            [comp["marketing"], comp["devtools"], comp["operations"],
             comp["hosting"], comp["setup"], comp["windows_signing"],
             comp["apple"], comp["pvt_ltd_incorporation"],
             comp["pvt_ltd_compliance"]],
        ),
        Paragraph(f"Total {inr(scenario_b)} - every category derives from the "
                  "runway line items and reconciles exactly (no rounding "
                  "residual). Detail in the Investment Requirement document.",
                  SMALL),
    ]
    elements.append(two_col(left, right, left_w=128, right_w=132))
    elements.append(PageBreak())


def slide_close(elements):
    section_head(elements, "Why now",
                 "Working product, a market that pays cash, and a compliance "
                 "moat that is opening right now.")
    elements.append(ListFlowable([
        ListItem(Paragraph("<b>Product is in active development</b> - 70+ "
                 "features already built across proctoring, dashboard, grading "
                 "and billing, with desktop clients for macOS and Windows; "
                 "release-readiness work is ongoing.", BODY)),
        ListItem(Paragraph("<b>DPDP Act is forcing the issue</b> - data "
                 "minimisation and residency are now procurement criteria, and "
                 "our architecture satisfies them by construction.", BODY)),
        ListItem(Paragraph("<b>The cost base is the moat</b> - an order-of-"
                 "magnitude price advantage that competitors cannot match "
                 "without re-architecting away from server-side video.", BODY)),
        ListItem(Paragraph("<b>The ask is small and the runway is long</b> - "
                 "~Rs. 4.8 lakh funds an 18-month sprint to the first named "
                 "coaching-chain logo.", BODY)),
    ], bulletType="bullet", start="circle"))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(
        "Companion documents: Procta_Investment_Requirement.pdf (capital "
        "detail), Procta_Competitive_Comparison_Report.pdf (sourced "
        "competitor analysis), Procta_Features.pdf (full feature surface).",
        SMALL))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"Procta &nbsp;|&nbsp; procta.net &nbsp;|&nbsp; "
        f"{date.today().strftime('%d %B %Y')}",
        ParagraphStyle("c", parent=BODY, fontName="Helvetica-Bold",
                       textColor=ACCENT_DARK)))


# ── Page chrome ────────────────────────────────────────────────────
def _on_page(canvas, doc):
    canvas.saveState()
    # top accent rule
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(2)
    canvas.line(MARGIN, PAGE_H - 10 * mm, PAGE_W - MARGIN, PAGE_H - 10 * mm)
    # footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 8 * mm, "Procta - Investor Pitch Deck")
    canvas.drawRightString(PAGE_W - MARGIN, 8 * mm, f"{doc.page}")
    canvas.restoreState()


# ── Build ──────────────────────────────────────────────────────────
def main():
    # Single source of truth (mirrors gen_investment_pdf.py) ----------
    # Revised 2026-07-03: the $10/month Azure Trusted Signing line was
    # never actually buildable (not available to Indian individuals);
    # corrected to a verified Certum EV code-signing certificate. Pvt Ltd
    # incorporation + compliance added — accepting this round requires an
    # entity to issue equity into, so it's a precondition of the raise,
    # not a deferred expense. A secondary/demo Hostinger VPS was also
    # added (single-VPS was a concentration risk flagged in the June 2026
    # technical audit). See gen_investment_pdf.py for full sourcing.
    one_time_total = 21000 + 28000 + 1600 + 20500 + 27550 + 18000
    annual_recurring_total = 9500 + 30000
    annual_18mo = int(round(annual_recurring_total * 1.5))
    apple_18mo = int(round(9500 * 1.5))
    pvt_ltd_compliance_18mo = int(round(30000 * 1.5))
    monthly_current = 5000
    monthly_scaled = monthly_current + 2000 + 10000
    scenario_a = monthly_current * 18 + annual_18mo + one_time_total
    scenario_b = monthly_scaled * 18 + annual_18mo + one_time_total
    assert one_time_total == 116650
    assert annual_18mo == 59250
    assert apple_18mo == 14250
    assert apple_18mo + pvt_ltd_compliance_18mo == annual_18mo
    assert scenario_a == 265900 and scenario_b == 481900, (scenario_a, scenario_b)

    comp = {
        "marketing":             10000 * 18,
        "hosting":               28000 + 20500,
        "devtools":              5000 * 18,
        "operations":            2000 * 18,
        "windows_signing":       27550,
        "apple":                 apple_18mo,
        "pvt_ltd_incorporation": 18000,
        "pvt_ltd_compliance":    pvt_ltd_compliance_18mo,
        "setup":                 21000 + 1600,
    }
    assert sum(comp.values()) == scenario_b, (sum(comp.values()), scenario_b)

    # Indicative competitor pricing (INR/attempt). Procta = published
    # rate card. Competitors = public-source midpoints from the vetted
    # competitive report, converted at USD 1 = Rs. 95.
    USD = 95
    pricing = {
        "procta":       80,                 # published rate card
        "honorlock":    round(5.5 * USD),   # USD 4-7 midpoint  -> 522
        "proctortrack": round(10 * USD),    # USD 8-12 midpoint -> 950
        "talview":      round(11 * USD),    # USD 7-15 midpoint -> 1045
        "mettl":        700,                # Rs. 400-1000 midpoint
    }

    out_path = "Procta_Pitch_Deck.pdf"
    doc = SimpleDocTemplate(
        out_path, pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=16 * mm, bottomMargin=14 * mm,
        title="Procta - Investor Pitch Deck", author="Procta",
        subject="Procta investor pitch deck: product, security, market, "
                "competitive comparison, and the capital ask.")
    e = []
    slide_cover(e)
    slide_problem(e)
    slide_solution(e)
    slide_features(e)
    slide_low_cost(e)
    slide_security(e)
    slide_why_us(e)
    slide_market(e)
    slide_comparison(e, pricing)
    slide_ask(e, comp, scenario_a, scenario_b)
    slide_close(e)
    doc.build(e, onFirstPage=_on_page, onLaterPages=_on_page)
    print(f"Written: {out_path}")
    print(f"  Scenario A: {inr(scenario_a)}   Scenario B: {inr(scenario_b)}")
    print(f"  Use-of-funds reconciles: {sum(comp.values()) == scenario_b}")


if __name__ == "__main__":
    main()
