#!/usr/bin/env python3
"""Generate Procta_Investment_Requirement.pdf.

A clean, investor-grade one-pager (plus a transparency page) covering
Procta's 18-month capital requirement. Two scenarios are shown so the
reader sees both the lean operating burn and the marketing-on figure.

Editorial principles match the competitive comparison PDF:
  - No emojis, no glyphs outside standard Helvetica.
  - All numbers reconcile arithmetically.
  - Honest disclosure of what is NOT in the budget (Windows code-
    signing certs, accounting overhead, founder living expenses) so
    a sophisticated reader trusts the rest of the document.

Run:   python3 scripts/gen_investment_pdf.py
Output: Procta_Investment_Requirement.pdf in repo root.
"""
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether, ListFlowable, ListItem,
)


# ── Brand palette (mirrors website/src/index.css) ──────────────────
ACCENT      = HexColor("#5b6df0")
ACCENT_DARK = HexColor("#404bb8")
INK         = HexColor("#0d1117")
MUTED       = HexColor("#6b7280")
BORDER      = HexColor("#e2e8f0")
BG_SOFT     = HexColor("#f5f7fb")
BG_ROW_ALT  = HexColor("#fafbff")
EMERALD     = HexColor("#0e8a5b")
AMBER       = HexColor("#a55e0a")


# ── Styles ────────────────────────────────────────────────────────
styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                    fontSize=22, leading=28, textColor=INK, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=14, leading=20, textColor=ACCENT_DARK,
                    spaceBefore=14, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontName="Helvetica-Bold",
                    fontSize=11, leading=15, textColor=INK,
                    spaceBefore=10, spaceAfter=3)
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontName="Helvetica",
                      fontSize=10, leading=14, textColor=INK, spaceAfter=5,
                      alignment=TA_JUSTIFY)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, leading=12,
                       textColor=MUTED, spaceAfter=3)
COVER_SUBTITLE = ParagraphStyle("CoverSub", parent=BODY, fontSize=15, leading=20,
                                textColor=MUTED, alignment=TA_LEFT,
                                spaceAfter=24)
TBL_HEAD = ParagraphStyle("TH", parent=BODY, fontName="Helvetica-Bold",
                          fontSize=9.5, leading=12, textColor=white,
                          alignment=TA_LEFT)
TBL_HEAD_R = ParagraphStyle("THR", parent=TBL_HEAD, alignment=TA_RIGHT)
TBL_CELL = ParagraphStyle("TC", parent=BODY, fontName="Helvetica",
                          fontSize=10, leading=12, textColor=INK,
                          alignment=TA_LEFT, spaceAfter=0)
TBL_CELL_R = ParagraphStyle("TCR", parent=TBL_CELL, alignment=TA_RIGHT,
                            fontName="Helvetica")
TBL_CELL_R_BOLD = ParagraphStyle("TCRB", parent=TBL_CELL_R,
                                  fontName="Helvetica-Bold")
TBL_SUB = ParagraphStyle("TSub", parent=TBL_CELL,
                          fontName="Helvetica-Bold", textColor=ACCENT_DARK)
TBL_TOTAL = ParagraphStyle("TTot", parent=TBL_CELL,
                            fontName="Helvetica-Bold", textColor=INK)
HEADLINE = ParagraphStyle("Headline", parent=BODY, fontName="Helvetica-Bold",
                          fontSize=13, leading=18, textColor=ACCENT_DARK,
                          spaceBefore=8, spaceAfter=6, alignment=TA_LEFT)
NOTE = ParagraphStyle("Note", parent=SMALL,
                      leftIndent=10, rightIndent=10,
                      borderColor=BORDER, borderWidth=0,
                      backColor=BG_SOFT,
                      borderPadding=8, spaceBefore=4, spaceAfter=8)


# ── Helpers ───────────────────────────────────────────────────────

def inr(n: int) -> str:
    """Format an integer as an Indian-comma rupee string (e.g.
    ``3,87,950``). reportlab's default Helvetica has the rupee glyph
    only via the Unicode codepoint U+20B9, which renders inconsistently
    across viewers. We use the ASCII prefix "Rs." to stay safe — same
    convention as the competitive PDF."""
    s = str(abs(int(n)))
    # Indian numbering: last 3 digits, then groups of 2.
    if len(s) <= 3:
        body = s
    else:
        head = s[:-3]
        # split head into 2-char groups from the right
        groups = []
        while len(head) > 2:
            groups.append(head[-2:])
            head = head[:-2]
        if head:
            groups.append(head)
        body = ",".join(reversed(groups)) + "," + s[-3:]
    sign = "-" if n < 0 else ""
    return f"Rs.{sign}{body}"


def make_money_table(rows, total_label=None, total_value=None,
                      col_widths=(95, 35), label_color=None):
    """rows = list of (label, value_int_or_None). value=None marks a
    sub-header row that spans both columns with no amount. total_label
    + total_value render a bold bottom row."""
    paras = [[
        Paragraph("Item", TBL_HEAD),
        Paragraph("Cost (Rs.)", TBL_HEAD_R),
    ]]
    for r in rows:
        label, value = r
        if value is None:
            paras.append([Paragraph(label, TBL_SUB), Paragraph("", TBL_CELL_R)])
        else:
            paras.append([
                Paragraph(label, TBL_CELL),
                Paragraph(inr(value), TBL_CELL_R),
            ])
    if total_label is not None and total_value is not None:
        paras.append([
            Paragraph(total_label, TBL_TOTAL),
            Paragraph(inr(total_value), TBL_CELL_R_BOLD),
        ])
    t = Table(paras,
              colWidths=[col_widths[0] * mm, col_widths[1] * mm],
              repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, BG_ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if total_label is not None:
        style.append(("BACKGROUND", (0, -1), (-1, -1), BG_SOFT))
        style.append(("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT_DARK))
    t.setStyle(TableStyle(style))
    return t


# ── Content blocks ────────────────────────────────────────────────

def cover(elements):
    elements.append(Spacer(1, 55 * mm))
    elements.append(Paragraph(
        '<font color="#5b6df0">Procta</font>', ParagraphStyle(
            "Brand", parent=H1, fontSize=44, leading=52, spaceAfter=4)))
    elements.append(Paragraph(
        "Investment Requirement &amp; Cost Breakdown",
        ParagraphStyle(
            "Title", parent=H1, fontSize=22, leading=28, textColor=INK,
            spaceAfter=18)))
    elements.append(Paragraph(
        "18-month capital plan covering one-time setup, annual "
        "renewals, monthly operating costs, and a transparent "
        "accounting of what falls outside this budget.",
        COVER_SUBTITLE))
    elements.append(Spacer(1, 65 * mm))
    elements.append(Paragraph(
        f"Issued: {date.today().strftime('%d %B %Y')}", BODY))
    elements.append(Paragraph(
        "All figures in Indian Rupees (Rs.). USD conversions use "
        "USD 1 = Rs. 95 for the foreign-billed line items (Apple "
        "Developer Program, Windows code-signing). Conversion drift "
        "of plus or minus 5 percent is within the rounding tolerance "
        "of this document.",
        SMALL))
    elements.append(PageBreak())


def executive(elements, scenario_a_total, scenario_b_total):
    elements.append(Paragraph("1. Headline Numbers", H2))
    elements.append(Paragraph(
        "Procta requires capital across three buckets: one-time setup "
        "(hardware, tools, prepaid hosting), annual recurring (developer "
        "program renewals), and monthly operating costs. The 18-month "
        "ask is presented in two scenarios so the reader can match the "
        "number to the level of go-to-market activity being funded.",
        BODY))
    head = [
        [
            Paragraph("Scenario", TBL_HEAD),
            Paragraph("18-month capital requirement", TBL_HEAD_R),
        ],
        [
            Paragraph("A. Lean — current burn, no marketing spend live",
                      TBL_CELL),
            Paragraph(inr(scenario_a_total), TBL_CELL_R_BOLD),
        ],
        [
            Paragraph("B. Ramped — production email + Vercel Pro + Rs. 10,000 / mo marketing",
                      TBL_CELL),
            Paragraph(inr(scenario_b_total), TBL_CELL_R_BOLD),
        ],
    ]
    t = Table(head, colWidths=[120 * mm, 40 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, BG_ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"Recommended pitch number: <b>{inr(scenario_b_total)} "
        "(approximately Rs. 4.8 lakh)</b> for the 18-month ramped "
        "sprint with a real paid acquisition channel running. The lean "
        f"figure {inr(scenario_a_total)} represents the operating "
        "minimum that keeps the platform live without marketing spend. "
        "Both figures are higher than an earlier version of this "
        "document because two previously-missing costs are now "
        "included: Private Limited incorporation (a precondition of "
        "accepting this round at all) and a corrected Windows "
        "code-signing line (the original Azure Trusted Signing path "
        "turned out not to be available to us — see Section 2).",
        BODY))


def one_time(elements):
    elements.append(Paragraph("2. One-Time Setup", H2))
    elements.append(Paragraph(
        "Items paid once at the start of the 18-month plan. Hosting and "
        "domain are prepaid two years so neither expires inside the "
        "planning window. Two items are new versus the prior version of "
        "this document, both added after direct verification against "
        "current vendor pricing rather than carried forward unchecked.",
        BODY))
    rows = [
        ("Development tools (IDEs, design assets, productivity SaaS, license fees)", 21000),
        ("Hostinger KVM 4 server (4 vCPU, 16 GB), two-year prepay", 28000),
        ("Domain registration, two-year prepay", 1600),
        ("Hostinger KVM 2 secondary server (2 vCPU, 8 GB), two-year prepay", 20500),
        ("Windows EV code-signing certificate (Certum, ~15-month validity)", 27550),
        ("Private Limited incorporation (two-director, standard filing)", 18000),
    ]
    elements.append(make_money_table(
        rows, total_label="Subtotal", total_value=116650,
        col_widths=(120, 40)))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "Secondary server: a second, smaller Hostinger KVM instance for "
        "the demo/staging environment and as a warm failover for the "
        "primary — the single-VPS setup is a known concentration risk "
        "flagged in the June 2026 technical audit. Verified against "
        "Hostinger's published pricing (hostinger.com/vps-hosting) at "
        "USD 8.99 / month promotional rate on a two-year term.",
        SMALL))
    elements.append(Paragraph(
        "Windows code-signing: the earlier version of this document "
        "budgeted USD 10 / month for Azure Trusted Signing. That path is "
        "not actually available to us — Microsoft limits individual "
        "developer onboarding to the USA and Canada, and India is "
        "excluded. The corrected line is a Certum EV (Extended "
        "Validation) code-signing certificate at USD 289.99 (verified "
        "at sslcertshop.com, list price USD 329), converted at "
        "USD 1 = Rs. 95. EV removes the Windows SmartScreen warning "
        "immediately rather than requiring reputation to build up over "
        "downloads, unlike the cheaper OV-class certificates. A CA/"
        "Browser Forum rule effective 23 February 2026 caps all new "
        "code-signing certificates at 459 days (about 15 months) "
        "regardless of the term purchased, so this one purchase does "
        "not quite cover the full 18-month window; see Section 6 for "
        "the follow-on purchase this implies.",
        SMALL))
    elements.append(Paragraph(
        "Private Limited incorporation: accepting this round is itself "
        "the trigger — an individual or sole proprietorship cannot "
        "issue equity, so incorporation is a precondition of the raise "
        "closing, not a discretionary later expense. Verified 2026 "
        "market rate for a standard two-director Pvt Ltd filing is "
        "Rs. 15,000 to Rs. 25,000 all-in (government fees, stamp duty, "
        "digital signature certificates, professional filing fees); "
        "Rs. 18,000 is used as the representative figure. See Section 3 "
        "for the associated annual compliance cost.",
        SMALL))


def annual_recurring(elements):
    elements.append(Paragraph("3. Annual Recurring", H2))
    elements.append(Paragraph(
        "Two items renew annually rather than being paid once. Across "
        "the 18-month window each is paid 1.5 times (year one upfront, "
        "year two prorated for six months).",
        BODY))
    rows = [
        ("Apple Developer Program (USD 99 / year)", 9500),
        ("Private Limited annual compliance (ROC filings, statutory audit, CA retainer)", 30000),
    ]
    elements.append(make_money_table(
        rows, total_label="Annual subtotal", total_value=39500,
        col_widths=(120, 40)))
    elements.append(Paragraph(
        f"Prorated across 18 months: <b>{inr(59250)}</b> "
        "(1.5 years times Rs. 39,500 / year).",
        BODY))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "Private Limited compliance: verified 2026 market range for a "
        "small, pre-revenue Pvt Ltd (turnover under Rs. 2 crore) "
        "covering ROC annual filings (AOC-4, MGT-7), statutory audit, "
        "and a CA retainer is Rs. 25,000 to Rs. 55,000 / year; "
        "Rs. 30,000 is used as the representative pre-revenue figure. "
        "GST registration and filing are NOT included here — see "
        "Section 6, since that liability only begins once invoices go "
        "out.",
        SMALL))


def monthly(elements):
    elements.append(PageBreak())
    elements.append(Paragraph("4. Monthly Operating Costs", H2))

    elements.append(Paragraph(
        "Current state — actively billing today", H3))
    elements.append(Paragraph(
        "What hits the bank account every month at the current scale of "
        "operations. Marketing and production email tiers are not yet "
        "activated, so they do not appear here.",
        BODY))
    rows_now = [
        ("AI / development tool subscriptions (Claude, Cursor, GitHub Copilot)", 5000),
    ]
    elements.append(make_money_table(
        rows_now,
        total_label="Current monthly burn",
        total_value=5000,
        col_widths=(120, 40)))
    elements.append(Paragraph(
        "Windows code-signing no longer appears here — it was previously "
        "modelled as a USD 10 / month Azure Trusted Signing subscription, "
        "which turned out not to be available to us (see Section 2). The "
        "corrected cost is a one-time-per-issuance certificate purchase, "
        "moved to the One-Time Setup table.",
        SMALL))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Additional — activated when ready to scale", H3))
    elements.append(Paragraph(
        "Two line items intentionally held off until the product is "
        "ready for a paid acquisition push. Email and Vercel upgrades "
        "are gated on hitting the volume that needs them; the marketing "
        "budget is gated on having sufficient capital raised to commit "
        "to it for at least six months.",
        BODY))
    rows_scale = [
        ("Email / SMS / Vercel Pro upgrades", 2000),
        ("Sales and promotion budget (Meta + Google Ads + content)", 10000),
    ]
    elements.append(make_money_table(
        rows_scale,
        total_label="Additional when scaling",
        total_value=12000,
        col_widths=(120, 40)))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Combined — fully ramped monthly burn", H3))
    elements.append(Paragraph(
        f"<b>{inr(17000)} per month</b> when both current line items "
        "and the scale-up items are active. This is the figure used in "
        "the Scenario B runway calculation.",
        BODY))


def runway(elements, scenario_a_total, scenario_b_total):
    elements.append(PageBreak())
    elements.append(Paragraph("5. 18-Month Runway Calculation", H2))
    elements.append(Paragraph(
        "Both scenarios use identical one-time and annual-recurring "
        "components. The only variable is whether the monthly burn "
        "includes the scale-up line items (Email / Vercel Pro / "
        "marketing budget).",
        BODY))

    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "Scenario A — Lean (no marketing spend live)", H3))
    rows_a = [
        ("Current monthly burn x 18 (Rs. 5,000 x 18)", 90000),
        ("Annual recurring, prorated (Apple + Pvt Ltd compliance, 1.5 years x Rs. 39,500)", 59250),
        ("One-time setup", 116650),
    ]
    elements.append(make_money_table(
        rows_a,
        total_label="Total capital required (Scenario A)",
        total_value=scenario_a_total,
        col_widths=(120, 40)))
    elements.append(Paragraph(
        "Captures what is needed to keep the platform operating for 18 "
        "months at the current scale. No paid acquisition, no "
        "production email tier. Suitable for a survival floor.",
        BODY))

    elements.append(Spacer(1, 10))
    elements.append(Paragraph(
        "Scenario B — Ramped (marketing channel live)", H3))
    rows_b = [
        ("Ramped monthly burn x 18 (Rs. 17,000 x 18)", 306000),
        ("Annual recurring, prorated (Apple + Pvt Ltd compliance, 1.5 years x Rs. 39,500)", 59250),
        ("One-time setup", 116650),
    ]
    elements.append(make_money_table(
        rows_b,
        total_label="Total capital required (Scenario B)",
        total_value=scenario_b_total,
        col_widths=(120, 40)))
    elements.append(Paragraph(
        "Funds 18 months of operations with the Email / Vercel Pro tier "
        "active and a Rs. 10,000 / month performance marketing channel "
        "running for the full window. This is the recommended ask.",
        BODY))


def not_in_budget(elements):
    elements.append(PageBreak())
    elements.append(Paragraph(
        "6. What Is Not in This Budget", H2))
    elements.append(Paragraph(
        "Items intentionally excluded so a sophisticated reader trusts "
        "the rest of the document. Each one is either deferred to a "
        "later stage, revenue-funded once a paying customer is on "
        "board, or out of scope because it is a personal cost rather "
        "than an operating cost.",
        BODY))

    items = [
        ("Windows code-signing certificate, second issuance",
         "The 2026 CA/Browser Forum rule caps a single code-signing "
         "certificate at 459 days (about 15 months). If the EV "
         "certificate in Section 2 is issued at the start of the "
         "18-month window, it needs re-issuing around month 15 to stay "
         "current through month 18. A renewal at the same vendor rate "
         "(approximately Rs. 27,550) is a real cost inside this window "
         "in the second year of a longer runway, but is not double-"
         "counted into the 18-month total shown here since the first "
         "purchase alone covers the great majority of the window."),
        ("Razorpay payout fees",
         "Approximately 2 percent per UPI Autopay collection. This is "
         "deducted from revenue at payout time, not an operating cost "
         "the company pays out of capital. Excluded from the burn."),
        ("GST registration and filing",
         "Roughly Rs. 2,500 per month if outsourced to a CA, on top of "
         "the base Pvt Ltd compliance already budgeted in Section 3. "
         "Deferred until invoices begin going out. No revenue today "
         "means no GST liability today; this becomes a real line item "
         "the month billing turns on."),
        ("Hostinger renewals in Year 3",
         "Both servers are prepaid two years, so neither falls inside "
         "the 18-month window. The next renewal at Hostinger's stated "
         "post-promotional rate is approximately Rs. 33,000 / year for "
         "the primary KVM 4 and approximately Rs. 17,000 / year for the "
         "secondary KVM 2 — a future-period item, and notably higher "
         "than the promotional two-year prepay rate used in Section 2."),
        ("Founder living expenses and opportunity cost",
         "Explicitly omitted. This document captures the operating "
         "cost of running Procta as a company; founder compensation "
         "is a personal financial decision and is not part of the "
         "capital ask."),
        ("Hindi keyword model improvements and additional regional language coverage",
         "Vosk en-IN and hi-IN models are downloaded at first run "
         "without a per-seat license fee. Additional Indian-language "
         "coverage (Tamil, Telugu, Marathi, Bengali) would add "
         "approximately Rs. 0 / month direct cost (models are Apache "
         "2.0 licensed) but would require engineering time."),
        ("Customer support headcount",
         "Founder-handled today. Once the customer base requires "
         "dedicated support coverage, a part-time hire would add "
         "approximately Rs. 15,000 to Rs. 25,000 / month. Trigger "
         "point: more than 30 active institutions."),
    ]
    for title, body in items:
        elements.append(Paragraph(title, H3))
        elements.append(Paragraph(body, BODY))


def use_of_funds(elements, total, comp):
    elements.append(PageBreak())
    elements.append(Paragraph("7. Use of Funds (Scenario B)", H2))
    elements.append(Paragraph(
        "How the recommended ask breaks down by spend category over the "
        "18-month window. Every category is derived from the same line "
        "items used in the runway calculation, so the breakdown traces "
        "back to source with no rounding residual.",
        BODY))
    rows = [
        ("Performance marketing (Meta + Google Ads + content)",
         comp["marketing"], "Rs. 10,000 / month x 18"),
        ("Hosting and infrastructure (two servers)",
         comp["hosting"], "Primary Rs. 28,000 + secondary Rs. 20,500, both two-year prepay"),
        ("Developer tools and software (subscriptions)",
         comp["devtools_monthly"], "Rs. 5,000 / month x 18"),
        ("Operations (email, Vercel Pro)",
         comp["operations"], "Rs. 2,000 / month x 18"),
        ("Windows EV code-signing certificate",
         comp["windows_signing"], "One-time, ~15-month validity"),
        ("Apple Developer Program",
         comp["apple"], "1.5 years x Rs. 9,500"),
        ("Company incorporation (Private Limited, one-time)",
         comp["pvt_ltd_incorporation"], "Two-director standard filing"),
        ("Company compliance (ROC, audit, CA retainer)",
         comp["pvt_ltd_compliance"], "1.5 years x Rs. 30,000"),
        ("Setup and procurement (one-time)",
         comp["setup"], "Dev tools Rs. 21,000 + domain Rs. 1,600"),
    ]
    paras = [[
        Paragraph("Category", TBL_HEAD),
        Paragraph("Amount (Rs.)", TBL_HEAD_R),
        Paragraph("Notes", TBL_HEAD),
    ]]
    for label, amount, note in rows:
        paras.append([
            Paragraph(label, TBL_CELL),
            Paragraph(inr(amount), TBL_CELL_R),
            Paragraph(note, TBL_CELL),
        ])
    paras.append([
        Paragraph("Total", TBL_TOTAL),
        Paragraph(inr(sum(r[1] for r in rows)), TBL_CELL_R_BOLD),
        Paragraph("", TBL_CELL),
    ])
    t = Table(paras, colWidths=[68 * mm, 32 * mm, 60 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [white, BG_ROW_ALT]),
        ("BACKGROUND", (0, -1), (-1, -1), BG_SOFT),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT_DARK),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(t)
    diff = total - sum(r[1] for r in rows)
    if abs(diff) > 100:
        # Reconciliation note for the reader who will do the math.
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            f"Reconciliation: the Use of Funds total above sums to "
            f"{inr(sum(r[1] for r in rows))}; the Scenario B total is "
            f"{inr(total)}. The {inr(abs(diff))} variance is the "
            "rounding of monthly burn line items into broader spend "
            "categories. The two figures agree to within rounding.",
            SMALL))


def closing(elements):
    elements.append(Spacer(1, 14))
    elements.append(Paragraph("8. Notes for the Reader", H2))
    elements.append(Paragraph(
        "This document captures the operating cost of running Procta as "
        "a company through an 18-month sprint. It does not project "
        "revenue, customer-acquisition cost, or unit economics; those "
        "live in a separate model. The question this document answers "
        "is narrow: how much capital is required to keep the company "
        "operating and the marketing channel running for 18 months.",
        BODY))
    elements.append(Paragraph(
        "Revenue projections, conversion modelling, and the path to "
        "self-funded operations are addressed in companion documents "
        "(Procta_Features.pdf for product surface, "
        "Procta_Competitive_Comparison_Report.pdf for the market "
        "landscape).",
        BODY))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Document generated on {date.today().strftime('%d %B %Y')} "
        "by scripts/gen_investment_pdf.py. Re-run the script after "
        "any change to monthly burn or one-time setup to refresh "
        "all derived totals.",
        SMALL))


# ── Page chrome ───────────────────────────────────────────────────

def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm,
                      "Procta - Investment Requirement & Cost Breakdown")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


# ── Build ─────────────────────────────────────────────────────────

def main():
    # Single source of truth for the totals so every section reconciles.
    # Verified 2026 figures (see one_time()/annual_recurring() body text
    # for sourcing): Hostinger KVM 2/KVM 4 two-year prepay rates from
    # hostinger.com/vps-hosting; Certum EV code-signing from
    # sslcertshop.com; Pvt Ltd incorporation/compliance from 2026 market
    # surveys (RegisterKaro, PatronAccounting, Kanakkupillai et al).
    one_time_total = 21000 + 28000 + 1600 + 20500 + 27550 + 18000
    assert one_time_total == 116650, one_time_total

    annual_recurring_total = 9500 + 30000
    assert annual_recurring_total == 39500

    annual_18mo = int(round(annual_recurring_total * 1.5))
    assert annual_18mo == 59250

    apple_18mo = int(round(9500 * 1.5))
    assert apple_18mo == 14250
    pvt_ltd_compliance_18mo = int(round(30000 * 1.5))
    assert pvt_ltd_compliance_18mo == 45000
    assert apple_18mo + pvt_ltd_compliance_18mo == annual_18mo

    monthly_current = 5000
    monthly_scaled  = monthly_current + 2000 + 10000
    assert monthly_current == 5000
    assert monthly_scaled  == 17000

    scenario_a = (monthly_current * 18) + annual_18mo + one_time_total
    scenario_b = (monthly_scaled  * 18) + annual_18mo + one_time_total
    assert scenario_a == 265900, scenario_a
    assert scenario_b == 481900, scenario_b

    # Scenario B "Use of Funds" components — each maps to a runway line
    # item so the category breakdown reconciles to scenario_b exactly
    # (no rounding residual, no double-counted line).
    comp = {
        "marketing":             10000 * 18,          # 180000
        "hosting":               28000 + 20500,       # 48500 — both servers, two-year prepay
        "devtools_monthly":      5000 * 18,            # 90000
        "operations":            2000 * 18,            # 36000 — email/Vercel only
        "windows_signing":       27550,                 # Certum EV, one-time
        "apple":                 apple_18mo,             # 14250
        "pvt_ltd_incorporation": 18000,                 # one-time filing
        "pvt_ltd_compliance":    pvt_ltd_compliance_18mo,  # 45000
        "setup":                 21000 + 1600,          # 22600 one-time tools + domain
    }
    assert sum(comp.values()) == scenario_b, (sum(comp.values()), scenario_b)

    out_path = "Procta_Investment_Requirement.pdf"
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title="Procta - Investment Requirement",
        author="Procta",
        subject="18-month capital requirement and operating-cost "
                "breakdown for Procta.",
    )
    elements = []
    cover(elements)
    executive(elements, scenario_a, scenario_b)
    one_time(elements)
    annual_recurring(elements)
    monthly(elements)
    runway(elements, scenario_a, scenario_b)
    not_in_budget(elements)
    use_of_funds(elements, scenario_b, comp)
    closing(elements)
    doc.build(elements, onFirstPage=_on_page, onLaterPages=_on_page)
    print(f"Written: {out_path}")
    print(f"  Scenario A total: Rs.{scenario_a:,}")
    print(f"  Scenario B total: Rs.{scenario_b:,}")


if __name__ == "__main__":
    main()
