# Question Bank PDF/DOCX Import (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a teacher upload a text-based PDF or DOCX of questions and import them into the existing Procta question bank through an on-device rule-based parser plus a mandatory human-review step, preserving garble-prone math/diagram questions as faithful images.

**Architecture:** A 5-stage pipeline behind the existing `question_bank` router/table. `document.py` turns bytes → normalized text + per-line layout metadata; `question_parser.py` (pure function) turns that → structured questions with confidence/flags; `region_render.py` rasterizes garble-prone/visual regions to PNG; two new endpoints (`/extract`, `/extract/confirm`) expose preview + tenant-safe persistence (reusing existing import). Stages are pure transforms — nothing hits the DB until the teacher confirms.

**Tech Stack:** FastAPI, Pydantic, asyncpg (via `async_table`), `pdfplumber` (PDF text+layout), `python-docx` (DOCX), `pypdfium2` (region rasterization). Tests: pytest (existing `client`, `make_admin_token`, `_atable` mock patterns).

**Standing constraints:** Self-Review-Before-Commit HARD RULE; **never commit/stage/push — the user commits**; on-device only (no external fetch / no third-party AI in v1); no exec/eval/shell-out. The commit steps below are written for completeness but the executing agent MUST NOT run them — leave changes in the working tree for the user.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `app/parsers/__init__.py` | Package marker. |
| `app/parsers/answer_key.py` | Parse a trailing answer-key section → `{qnum: "C"}` / `{qnum: "AC"}`. Pure. |
| `app/parsers/question_parser.py` | text+layout → `list[ParsedQuestion]` with confidence/flags. Pure. |
| `app/parsers/document.py` | bytes (+filename) → `ExtractedDoc` (normalized text + line layout + raw PDF handle for rasterization). PDF/DOCX. Scanned-guard. |
| `app/parsers/region_render.py` | (pdf bytes, page index, bbox) → PNG bytes via pypdfium2; `store_region_png()` writes through the existing question-image path. |
| `app/routers/question_bank.py` (modify) | Add `ExtractConfirmIn` model + `/extract` and `/extract/confirm` endpoints. |
| `app/static/dashboard-app.js` (modify) | "Import from PDF/Word" button + review-table modal + confirm. |
| `app/static/dashboard.html` (modify) | Button + modal markup. |
| `tests/test_qbank_answer_key.py` | Answer-key parser units. |
| `tests/test_qbank_parser.py` | Question parser units (the core). |
| `tests/test_qbank_document.py` | Document extraction units (fixtures). |
| `tests/test_qbank_region_render.py` | Rasterization unit. |
| `tests/test_qbank_extract_endpoint.py` | Endpoint auth/scope/errors + confirm-revalidation safety contract. |
| `tests/fixtures/qbank/` | Tiny sample PDF/DOCX fixtures. |

### Shared data shapes (defined once, used everywhere)

```python
# app/parsers/question_parser.py
from dataclasses import dataclass, field

BLOCKING_FLAGS = {"no_answer", "low_confidence", "few_options", "parse_error"}

@dataclass
class ParsedQuestion:
    question: str                       # stem text ("" if image carries it)
    question_type: str                  # mcq_single | mcq_multi | numeric | true_false
    options: dict                       # {"A": "...", "B": "..."} ({} for numeric)
    correct: str                        # "C" / "AC" / "42" / "" if unknown
    tags: list[str] = field(default_factory=list)
    image_url: str = ""                 # set later by region stage
    confidence: float = 1.0
    flags: list[str] = field(default_factory=list)
    # internal-only, stripped before JSON: which page+bbox to rasterize if math/visual
    _region: dict | None = None         # {"page": int, "bbox": [x0,y0,x1,y1]} or None

    def to_public(self) -> dict:
        return {
            "question": self.question, "type": self.question_type,
            "options": self.options, "correct": self.correct, "tags": self.tags,
            "image_url": self.image_url, "confidence": round(self.confidence, 2),
            "flags": self.flags,
        }
```

```python
# app/parsers/document.py
from dataclasses import dataclass

@dataclass
class DocLine:
    text: str
    page: int                 # 0 for DOCX
    bbox: list | None         # [x0, y0, x1, y1] for PDF; None for DOCX
    fonts: set                # set of font names on the line (empty for DOCX)
    has_image: bool = False   # an image object overlaps this line's bbox (PDF)

@dataclass
class ExtractedDoc:
    lines: list               # list[DocLine]
    pdf_bytes: bytes | None   # original PDF bytes for rasterization; None for DOCX
    kind: str                 # "pdf" | "docx"

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)
```

---

## Task 0: Dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements.lock` (note only — actual pins added when user runs pip-compile)

- [ ] **Step 1: Add runtime deps to `requirements.txt`**

Append (group with a comment):

```
# Question-bank PDF/DOCX import (on-device, permissive licenses only)
pdfplumber>=0.11.0      # MIT / pdfminer.six — PDF text + per-char font & bbox
python-docx>=1.1.2      # MIT — DOCX text
pypdfium2>=4.30.0       # BSD/Apache (PDFium) — page-region rasterization
```

- [ ] **Step 2: Install locally to unblock TDD**

Run: `pip install "pdfplumber>=0.11.0" "python-docx>=1.1.2" "pypdfium2>=4.30.0"`
Expected: installs cleanly, no build (all wheels).

- [ ] **Step 3: Verify imports**

Run: `python3 -c "import pdfplumber, docx, pypdfium2; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit** *(DO NOT RUN — user commits)*

```bash
git add requirements.txt && git commit -m "build(qbank): add on-device PDF/DOCX parse deps"
```

---

## Task 1: Answer-key parser (`answer_key.py`)

**Files:**
- Create: `app/parsers/__init__.py` (empty)
- Create: `app/parsers/answer_key.py`
- Test: `tests/test_qbank_answer_key.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_qbank_answer_key.py
from app.parsers.answer_key import parse_answer_key, find_answer_key_block

def test_inline_dash_pairs():
    assert parse_answer_key("1-C 2-A 3-D 4-B") == {1: "C", 2: "A", 3: "D", 4: "B"}

def test_dot_and_paren_separators():
    assert parse_answer_key("1. C\n2) A\n3 - D") == {1: "C", 2: "A", 3: "D"}

def test_multi_letter_answer_preserved():
    assert parse_answer_key("1-AC 2-B") == {1: "AC", 2: "B"}

def test_numeric_answer():
    assert parse_answer_key("1-42 2-3.14") == {1: "42", 2: "3.14"}

def test_lowercase_normalised_to_upper_letters():
    assert parse_answer_key("1-c 2-a") == {1: "C", 2: "A"}

def test_find_block_detects_answers_heading():
    text = "Q1 ...\n(a) ..\n\nAnswers\n1-C 2-A 3-D"
    body, key = find_answer_key_block(text)
    assert "Answers" not in body
    assert key == {1: "C", 2: "A", 3: "D"}

def test_find_block_returns_empty_when_no_key():
    body, key = find_answer_key_block("Q1 ...\n(a) ..\n(b) ..")
    assert key == {}
    assert body.startswith("Q1")
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_answer_key.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# app/parsers/answer_key.py
"""Parse a trailing answer-key section into {question_number: answer}.

Pure, no I/O. Handles the dominant institute formats:
  1-C 2-A 3-D   |   1. C  2) A   |   1 - AC (multi)   |   1-42 (numeric)
"""
import re

# qnum <sep> answer, where answer is letters (A-F, one or more) or a number.
_PAIR = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,3})\s*[-.\)\:]\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)(?![A-Za-z0-9])"
)
_HEADING = re.compile(r"(?im)^\s*(answers?|answer\s*keys?|solutions?)\s*:?\s*$")


def parse_answer_key(text: str) -> dict:
    out: dict = {}
    for m in _PAIR.finditer(text or ""):
        num = int(m.group(1))
        ans = m.group(2)
        # Letters → upper; numbers stay as-is.
        if re.fullmatch(r"[A-Fa-f]{1,6}", ans):
            ans = ans.upper()
        out[num] = ans
    return out


def find_answer_key_block(text: str) -> tuple[str, dict]:
    """Split body from a trailing answer-key section.

    Strategy: if an 'Answers'/'Answer Key'/'Solutions' heading exists, everything
    after it is the key and everything before is the body. Otherwise return the
    whole text as body with an empty key (answers, if any, are inline).
    """
    if not text:
        return "", {}
    m = None
    for m in _HEADING.finditer(text):
        pass  # take the LAST heading occurrence
    if not m:
        return text, {}
    body = text[: m.start()].rstrip()
    key = parse_answer_key(text[m.end():])
    return body, key
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_answer_key.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/parsers/__init__.py app/parsers/answer_key.py tests/test_qbank_answer_key.py
git commit -m "feat(qbank): answer-key section parser"
```

---

## Task 2: Question parser core (`question_parser.py`)

**Files:**
- Create: `app/parsers/question_parser.py`
- Test: `tests/test_qbank_parser.py`

This is the heart. It consumes plain text (layout/math wiring comes in Task 5 via the optional `lines` arg, which defaults to `None` so the text-only path is independently testable).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_qbank_parser.py
from app.parsers.question_parser import parse_questions, BLOCKING_FLAGS

MCQ = """1. What is 2+2?
(a) 3
(b) 4
(c) 5
(d) 6

2. Capital of France?
(a) London
(b) Paris
(c) Rome
(d) Berlin

Answers
1-B 2-B
"""

def test_parses_two_mcqs_with_answers():
    qs = parse_questions(MCQ)
    assert len(qs) == 2
    assert qs[0].question.startswith("What is 2+2")
    assert qs[0].question_type == "mcq_single"
    assert qs[0].options["B"] == "4"
    assert qs[0].correct == "B"
    assert qs[0].flags == []          # clean
    assert qs[0].confidence >= 0.8

def test_letter_dot_option_style():
    qs = parse_questions("1. Pick one\nA. red\nB. blue\nAns: A")
    assert qs[0].options == {"A": "red", "B": "blue"}
    assert qs[0].correct == "A"

def test_missing_answer_is_blocking_flag():
    qs = parse_questions("1. No answer here\n(a) x\n(b) y")
    assert "no_answer" in qs[0].flags
    assert qs[0].correct == ""
    assert set(qs[0].flags) & BLOCKING_FLAGS

def test_multi_answer_promotes_type():
    qs = parse_questions("1. Select all\n(a) x\n(b) y\n(c) z\nAnswers\n1-AC")
    assert qs[0].question_type == "mcq_multi"
    assert qs[0].correct == "AC"

def test_true_false_detected():
    qs = parse_questions("1. The sky is blue.\n(a) True\n(b) False\nAns: A")
    assert qs[0].question_type == "true_false"

def test_numeric_no_options():
    qs = parse_questions("1. Compute 6*7.\nAnswers\n1-42")
    assert qs[0].question_type == "numeric"
    assert qs[0].options == {}
    assert qs[0].correct == "42"

def test_few_options_flagged():
    qs = parse_questions("1. Only one option?\n(a) lonely\nAns: A")
    assert "few_options" in qs[0].flags

def test_qnum_markers_q_prefix():
    qs = parse_questions("Q1. first\n(a) x\n(b) y\nQ2. second\n(a) p\n(b) q")
    assert len(qs) == 2

def test_junk_between_questions_ignored():
    qs = parse_questions("Page 1 of 3\n\n1. Real Q\n(a) x\n(b) y\nAns: A\n\n-- footer --")
    assert len(qs) == 1
    assert qs[0].question == "Real Q"

def test_empty_input_returns_empty():
    assert parse_questions("") == []
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_parser.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# app/parsers/question_parser.py
"""Rule-based question extractor. Pure: text (+optional layout) -> questions.

Pipeline: block segmentation -> option detection -> type inference ->
answer resolution (inline, then answer-key) -> confidence/flags.
"""
import re
from dataclasses import dataclass, field

from .answer_key import find_answer_key_block, parse_answer_key

BLOCKING_FLAGS = {"no_answer", "low_confidence", "few_options", "parse_error"}

# Question-number markers at line start: "1." "1)" "Q1" "Q.1" "1 -"
_QNUM = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})\s*[\.\)\-]\s+|^\s*Q\.?\s*(\d{1,3})\b", re.I)
# Option markers: "(a)" "a)" "A." "(A)" "(1)"
_OPT = re.compile(r"^\s*[\(\[]?\s*([A-Fa-f1-6])\s*[\)\.\]]\s+(.*)$")
# Inline answer: "Ans: C" "Answer - C" "[C]"
_INLINE_ANS = re.compile(r"(?i)\bans(?:wer)?\s*[:\-]?\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)\b")
_INLINE_BRACKET = re.compile(r"^\s*\[\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)\s*\]\s*$")

_OPT_LETTERS = ["A", "B", "C", "D", "E", "F"]


@dataclass
class ParsedQuestion:
    question: str
    question_type: str
    options: dict
    correct: str
    tags: list = field(default_factory=list)
    image_url: str = ""
    confidence: float = 1.0
    flags: list = field(default_factory=list)
    _region: dict | None = None

    def to_public(self) -> dict:
        return {
            "question": self.question, "type": self.question_type,
            "options": self.options, "correct": self.correct, "tags": self.tags,
            "image_url": self.image_url, "confidence": round(self.confidence, 2),
            "flags": self.flags,
        }


def _qnum_of(line: str):
    m = _QNUM.match(line)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _strip_qnum(line: str) -> str:
    return _QNUM.sub("", line, count=1).strip()


def _segment_blocks(text: str) -> list:
    """Return [(qnum, [lines...]), ...] using the question-number markers."""
    blocks = []
    cur_num = None
    cur_lines: list = []
    for raw in text.splitlines():
        n = _qnum_of(raw)
        if n is not None:
            if cur_num is not None:
                blocks.append((cur_num, cur_lines))
            cur_num = n
            cur_lines = [_strip_qnum(raw)]
        elif cur_num is not None:
            cur_lines.append(raw.rstrip())
    if cur_num is not None:
        blocks.append((cur_num, cur_lines))
    return blocks


def _parse_block(qnum: int, lines: list) -> ParsedQuestion:
    stem_parts: list = []
    options: dict = {}
    inline_ans = ""
    next_letter = 0
    for ln in lines:
        mb = _INLINE_BRACKET.match(ln)
        if mb:
            inline_ans = mb.group(1)
            continue
        mo = _OPT.match(ln)
        if mo and next_letter < len(_OPT_LETTERS):
            letter = _OPT_LETTERS[next_letter]
            options[letter] = mo.group(2).strip()
            next_letter += 1
            continue
        ma = _INLINE_ANS.search(ln)
        if ma:
            inline_ans = ma.group(1)
            # keep any non-answer text on the line out of the stem
            continue
        if not options:           # still in the stem
            stem_parts.append(ln.strip())
        # lines after options that aren't answers are dropped (footers, etc.)

    stem = " ".join(p for p in stem_parts if p).strip()
    if inline_ans and re.fullmatch(r"[A-Fa-f]{1,6}", inline_ans):
        inline_ans = inline_ans.upper()

    # Type inference
    opt_vals = [v.strip().lower() for v in options.values()]
    if options and set(opt_vals) <= {"true", "false"} and len(options) == 2:
        qtype = "true_false"
    elif not options:
        qtype = "numeric"
    else:
        qtype = "mcq_single"   # may be promoted to mcq_multi by answer length

    q = ParsedQuestion(question=stem, question_type=qtype, options=options,
                       correct=inline_ans, tags=[])
    return q


def _finalise(q: ParsedQuestion) -> None:
    flags: list = []
    # multi-answer promotion
    if q.options and re.fullmatch(r"[A-F]{2,6}", q.correct or ""):
        q.question_type = "mcq_multi"
    # answer presence
    if not q.correct:
        flags.append("no_answer")
    # option count for MCQ types
    if q.question_type in ("mcq_single", "mcq_multi") and len(q.options) < 2:
        flags.append("few_options")
    # empty / tiny stem with no image → low confidence
    if not q.question and not q.image_url:
        flags.append("low_confidence")
    # confidence: start 1.0, subtract for each issue
    conf = 1.0 - 0.25 * len(flags)
    if len(q.question) < 5 and not q.image_url:
        conf -= 0.2
        if "low_confidence" not in flags:
            flags.append("low_confidence")
    q.flags = flags
    q.confidence = max(0.0, min(1.0, conf))


def parse_questions(text: str, lines: list | None = None) -> list:
    """text (+optional DocLine list for math/image wiring in Task 5) -> questions."""
    body, key = find_answer_key_block(text or "")
    blocks = _segment_blocks(body)
    out: list = []
    for qnum, blines in blocks:
        try:
            q = _parse_block(qnum, blines)
            if not q.correct and qnum in key:
                q.correct = key[qnum]
                if re.fullmatch(r"[A-Fa-f]{1,6}", q.correct):
                    q.correct = q.correct.upper()
            _finalise(q)
        except Exception:
            q = ParsedQuestion(question=" ".join(blines)[:200], question_type="mcq_single",
                               options={}, correct="", flags=["parse_error"], confidence=0.0)
        out.append(q)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_parser.py -q`
Expected: PASS (10 passed). If a test fails, fix the implementation — not the test — unless the test encodes a wrong expectation.

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/parsers/question_parser.py tests/test_qbank_parser.py
git commit -m "feat(qbank): rule-based question parser core"
```

---

## Task 3: Document extraction (`document.py`)

**Files:**
- Create: `app/parsers/document.py`
- Create: `tests/fixtures/qbank/` (generated fixtures via a helper in the test)
- Test: `tests/test_qbank_document.py`

- [ ] **Step 1: Write failing tests** (fixtures are generated in-test so no binary blobs are committed by hand)

```python
# tests/test_qbank_document.py
import io
import pytest
from app.parsers.document import extract_document, ScannedPdfError, UnreadableDocError

def _make_text_pdf(text: str) -> bytes:
    import pypdfium2 as pdfium  # not used to write; use reportlab if present
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in text.splitlines():
        c.drawString(72, y, line)
        y -= 18
    c.showPage(); c.save()
    return buf.getvalue()

def _make_docx(text: str) -> bytes:
    import docx
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()

def test_pdf_text_extraction():
    pdf = _make_text_pdf("1. Q one\n(a) x\n(b) y")
    doc = extract_document(pdf, "bank.pdf")
    assert doc.kind == "pdf"
    assert "Q one" in doc.text
    assert doc.pdf_bytes is not None
    assert all(l.bbox is not None for l in doc.lines)

def test_docx_text_extraction():
    data = _make_docx("1. Q one\n(a) x\n(b) y")
    doc = extract_document(data, "bank.docx")
    assert doc.kind == "docx"
    assert "Q one" in doc.text
    assert doc.pdf_bytes is None

def test_scanned_pdf_raises():
    # A PDF with no extractable text (single blank page) → scanned-like.
    blank = _make_text_pdf("")     # no drawString calls of substance
    with pytest.raises(ScannedPdfError):
        extract_document(blank, "scan.pdf")

def test_unknown_extension_raises():
    with pytest.raises(UnreadableDocError):
        extract_document(b"hello", "notes.txt")

def test_corrupt_pdf_raises_unreadable():
    with pytest.raises(UnreadableDocError):
        extract_document(b"%PDF-broken", "x.pdf")
```

NOTE: tests use `reportlab` to synthesize PDFs. Add `reportlab` to the **dev/test** install only (not runtime):
Run: `pip install reportlab`

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_document.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# app/parsers/document.py
"""Document → normalized text + line layout. PDF (pdfplumber) and DOCX (python-docx).

Raises ScannedPdfError for image-only PDFs and UnreadableDocError for unknown /
corrupt inputs. Keeps the original PDF bytes for later region rasterization.
"""
import io
import logging
from dataclasses import dataclass

logger = logging.getLogger("qbank.document")


class ScannedPdfError(Exception):
    """PDF has effectively no extractable text (scanned/image-only)."""


class UnreadableDocError(Exception):
    """Unknown extension or corrupt/encrypted file."""


@dataclass
class DocLine:
    text: str
    page: int
    bbox: list | None
    fonts: set
    has_image: bool = False


@dataclass
class ExtractedDoc:
    lines: list
    pdf_bytes: bytes | None
    kind: str

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


_MIN_CHARS_FOR_TEXT_PDF = 20   # below this total → treat as scanned


def _extract_pdf(data: bytes) -> ExtractedDoc:
    import pdfplumber
    lines: list = []
    total_chars = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pidx, page in enumerate(pdf.pages):
                images = page.images or []
                # group chars into visual lines via pdfplumber's text lines
                words = page.extract_words(use_text_flow=True, keep_blank_chars=False,
                                           extra_attrs=["fontname"]) or []
                # cluster words by their 'top' coordinate into lines
                rows: dict = {}
                for w in words:
                    key = round(w["top"] / 3.0)   # ~3pt bucket
                    rows.setdefault(key, []).append(w)
                for key in sorted(rows):
                    ws = sorted(rows[key], key=lambda w: w["x0"])
                    text = " ".join(w["text"] for w in ws)
                    total_chars += len(text)
                    x0 = min(w["x0"] for w in ws); x1 = max(w["x1"] for w in ws)
                    top = min(w["top"] for w in ws); bot = max(w["bottom"] for w in ws)
                    fonts = {w.get("fontname", "") for w in ws}
                    bbox = [x0, top, x1, bot]
                    has_img = any(not (im["x1"] < x0 or im["x0"] > x1 or
                                       im["bottom"] < top or im["top"] > bot)
                                  for im in images)
                    lines.append(DocLine(text=text, page=pidx, bbox=bbox,
                                         fonts=fonts, has_image=has_img))
    except ScannedPdfError:
        raise
    except Exception as e:
        logger.warning("[qbank] PDF open failed: %s", e)
        raise UnreadableDocError("Couldn't open this file.") from e
    if total_chars < _MIN_CHARS_FOR_TEXT_PDF:
        raise ScannedPdfError("This looks like a scanned PDF.")
    return ExtractedDoc(lines=lines, pdf_bytes=data, kind="pdf")


def _extract_docx(data: bytes) -> ExtractedDoc:
    import docx
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise UnreadableDocError("Couldn't open this file.") from e
    lines = [DocLine(text=p.text, page=0, bbox=None, fonts=set())
             for p in d.paragraphs if p.text and p.text.strip()]
    return ExtractedDoc(lines=lines, pdf_bytes=None, kind="docx")


def extract_document(data: bytes, filename: str) -> ExtractedDoc:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _extract_pdf(data)
    if name.endswith(".docx"):
        return _extract_docx(data)
    raise UnreadableDocError("Only PDF and Word (.docx) files are supported.")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_document.py -q`
Expected: PASS (5 passed). If the scanned-PDF test is flaky because reportlab still emits whitespace, lower the empty-PDF content further or assert on `_MIN_CHARS_FOR_TEXT_PDF` directly.

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/parsers/document.py tests/test_qbank_document.py
git commit -m "feat(qbank): PDF/DOCX text+layout extraction with scanned guard"
```

---

## Task 4: Region rasterization (`region_render.py`)

**Files:**
- Create: `app/parsers/region_render.py`
- Test: `tests/test_qbank_region_render.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_qbank_region_render.py
import io
from app.parsers.region_render import render_region_png

def _make_text_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, text); c.showPage(); c.save()
    return buf.getvalue()

def test_render_region_returns_png_bytes():
    pdf = _make_text_pdf("E = mc^2 region")
    png = render_region_png(pdf, page_index=0, bbox=[60, 60, 300, 100])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100

def test_bad_page_returns_empty():
    pdf = _make_text_pdf("x")
    assert render_region_png(pdf, page_index=99, bbox=[0, 0, 10, 10]) == b""
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_region_render.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# app/parsers/region_render.py
"""Rasterize a PDF page region to a PNG (math/diagram fidelity preservation).

pdfplumber bbox space is top-left origin in points. pypdfium2 renders a full
page to a bitmap; we crop in pixel space after scaling. Returns b"" on any
failure so the caller can fall back to text + a review flag.
"""
import io
import logging

logger = logging.getLogger("qbank.region")

_SCALE = 2.0   # render at 2x for crisp text


def render_region_png(pdf_bytes: bytes, page_index: int, bbox: list) -> bytes:
    try:
        import pypdfium2 as pdfium
        from PIL import Image  # pillow is already a runtime dep
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            if page_index < 0 or page_index >= len(pdf):
                return b""
            page = pdf[page_index]
            bitmap = page.render(scale=_SCALE)
            pil = bitmap.to_pil()
            x0, y0, x1, y1 = bbox
            # pad a few points so glyphs aren't clipped
            pad = 4
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_region_render.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/parsers/region_render.py tests/test_qbank_region_render.py
git commit -m "feat(qbank): PDF region rasterization to PNG"
```

---

## Task 5: Wire math/visual detection into the parser

**Files:**
- Modify: `app/parsers/question_parser.py` (use the optional `lines` arg)
- Test: `tests/test_qbank_parser.py` (add math-routing tests)

- [ ] **Step 1: Add failing tests**

```python
# append to tests/test_qbank_parser.py
from app.parsers.document import DocLine

def test_math_font_routes_region():
    text = "1. Solve\n(a) x\n(b) y\nAns: A"
    lines = [
        DocLine(text="1. Solve", page=0, bbox=[10, 10, 200, 24], fonts={"CMMI10"}),
        DocLine(text="(a) x", page=0, bbox=[10, 26, 80, 40], fonts={"Helvetica"}),
        DocLine(text="(b) y", page=0, bbox=[10, 42, 80, 56], fonts={"Helvetica"}),
        DocLine(text="Ans: A", page=0, bbox=[10, 58, 80, 72], fonts={"Helvetica"}),
    ]
    qs = parse_questions(text, lines=lines)
    assert qs[0]._region is not None
    assert "has_image" in qs[0].flags

def test_replacement_chars_route_region():
    text = "1. Bad � glyph\n(a) x\n(b) y\nAns: A"
    lines = [DocLine(text="1. Bad � glyph", page=0, bbox=[10, 10, 200, 24], fonts=set())]
    qs = parse_questions(text, lines=lines)
    assert "has_image" in qs[0].flags

def test_docx_math_gets_review_flag_not_region():
    # No bbox available (DOCX) → math_review flag, no region.
    text = "1. x² + 1 = 0 �\n(a) p\n(b) q\nAns: A"
    lines = [DocLine(text="1. x² + 1 = 0 �", page=0, bbox=None, fonts=set())]
    qs = parse_questions(text, lines=lines)
    assert qs[0]._region is None
    assert "math_review" in qs[0].flags
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_parser.py -q`
Expected: the 3 new tests FAIL (no math routing yet).

- [ ] **Step 3: Implement — add detection + bbox association**

Add to `question_parser.py`:

```python
# math-font name fragments (Computer Modern math, symbol fonts, etc.)
_MATH_FONT_HINTS = ("CMMI", "CMSY", "CMEX", "MSAM", "MSBM", "Symbol",
                    "MathJax", "STIX", "Math")

def _looks_garbled(text: str) -> bool:
    if "�" in text:
        return True
    # mojibake clusters typical of mis-decoded math fonts
    bad = sum(text.count(c) for c in ("Î", "Ã", "Â", "â€"))
    return bad >= 2

def _line_is_mathy(dl) -> bool:
    if any(any(h in (f or "") for h in _MATH_FONT_HINTS) for f in (dl.fonts or set())):
        return True
    return _looks_garbled(dl.text)
```

Change `_segment_blocks` to carry the `DocLine` objects, and `parse_questions` to
pass them through. Concretely, add a layout-aware segmentation that maps each
block to its lines, then after `_parse_block`/`_finalise`, run:

```python
def _apply_layout(q, block_lines):
    """block_lines: list[DocLine] for this question. Sets _region/flags."""
    mathy = [dl for dl in block_lines if _line_is_mathy(dl)]
    if not mathy:
        return
    have_bbox = [dl for dl in block_lines if dl.bbox is not None]
    if have_bbox:
        page = have_bbox[0].page
        x0 = min(dl.bbox[0] for dl in have_bbox)
        y0 = min(dl.bbox[1] for dl in have_bbox)
        x1 = max(dl.bbox[2] for dl in have_bbox)
        y1 = max(dl.bbox[3] for dl in have_bbox)
        q._region = {"page": page, "bbox": [x0, y0, x1, y1]}
        if "has_image" not in q.flags:
            q.flags.append("has_image")
    else:
        if "math_review" not in q.flags:
            q.flags.append("math_review")
```

Update `parse_questions` to build per-block `DocLine` lists when `lines` is
provided (match block text lines to DocLines by sequential consumption, since
both derive from the same source order), and call `_apply_layout(q, block_dls)`
after `_finalise(q)`. Also route `has_image` blocks: when `q._region` is set and
the stem extracted cleanly, keep the stem; the endpoint (Task 6) will rasterize
and set `image_url`. `has_image` is **not** in `BLOCKING_FLAGS` — an
image-preserved question is valid to commit.

Minimal sequential matcher:

```python
def parse_questions(text: str, lines: list | None = None) -> list:
    body, key = find_answer_key_block(text or "")
    # When layout lines are present, segment over them so each block keeps its
    # DocLines; otherwise fall back to text segmentation.
    if lines:
        text_for_seg = "\n".join(dl.text for dl in lines)
        body2, _ = find_answer_key_block(text_for_seg)
        dls = [dl for dl in lines if dl.text in body2 or dl.bbox is None]
    else:
        dls = None
    blocks = _segment_blocks(body)
    # Build parallel DocLine blocks by re-segmenting on the same markers.
    dl_blocks = _segment_doclines(dls) if dls is not None else [None] * len(blocks)
    out = []
    for (qnum, blines), dlblock in zip(blocks, dl_blocks + [None] * len(blocks)):
        try:
            q = _parse_block(qnum, blines)
            if not q.correct and qnum in key:
                q.correct = key[qnum]
                if re.fullmatch(r"[A-Fa-f]{1,6}", q.correct):
                    q.correct = q.correct.upper()
            _finalise(q)
            if dlblock:
                _apply_layout(q, dlblock)
        except Exception:
            q = ParsedQuestion(question=" ".join(blines)[:200], question_type="mcq_single",
                               options={}, correct="", flags=["parse_error"], confidence=0.0)
        out.append(q)
    return out


def _segment_doclines(dls):
    """Same marker logic as _segment_blocks but over DocLine objects."""
    blocks, cur = [], None
    for dl in dls:
        if _qnum_of(dl.text) is not None:
            if cur is not None:
                blocks.append(cur)
            cur = [dl]
        elif cur is not None:
            cur.append(dl)
    if cur is not None:
        blocks.append(cur)
    return blocks
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_parser.py -q`
Expected: PASS (13 passed). Iterate on the matcher until both the text-only and layout-aware tests pass; do not weaken the original 10.

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/parsers/question_parser.py tests/test_qbank_parser.py
git commit -m "feat(qbank): route math/visual blocks to image preservation"
```

---

## Task 6: `/extract` endpoint (upload → preview)

**Files:**
- Modify: `app/routers/question_bank.py` (new endpoint + a small PNG-store helper)
- Test: `tests/test_qbank_extract_endpoint.py`

The endpoint accepts a multipart file, runs document→parser→region, rasterizes any
`_region`, stores PNGs under the teacher's dir, and returns the preview list. It
must NOT persist any question.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_qbank_extract_endpoint.py
import io
from unittest.mock import patch
from tests.conftest import make_admin_token

TEACHER = {"id": "teacher-1", "email": "p@t.com", "org_id": "org-1",
           "org_role": "teacher", "full_name": "P", "status": "active"}

def _hdr():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1')}"}

def _pdf(text):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for ln in text.splitlines():
        c.drawString(72, y, ln); y -= 18
    c.showPage(); c.save(); return buf.getvalue()

def test_extract_requires_auth(client):
    r = client.post("/api/v1/admin/question-bank/extract",
                    files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 401

def test_extract_returns_preview(client):
    pdf = _pdf("1. What is 2+2?\n(a) 3\n(b) 4\nAns: B")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("bank.pdf", pdf, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["found"] == 1
    assert data["questions"][0]["correct"] == "B"
    assert "id" not in data["questions"][0]   # nothing persisted

def test_extract_scanned_pdf_422(client):
    blank = _pdf("")
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("scan.pdf", blank, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 422

def test_extract_bad_extension_415(client):
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("notes.txt", b"hi", "text/plain")},
                        headers=_hdr())
    assert r.status_code == 415

def test_extract_oversize_413(client):
    big = b"%PDF-" + b"0" * (21 * 1024 * 1024)
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract",
                        files={"file": ("big.pdf", big, "application/pdf")},
                        headers=_hdr())
    assert r.status_code == 413
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_extract_endpoint.py -q`
Expected: FAIL (404/endpoint missing).

- [ ] **Step 3: Implement**

In `app/routers/question_bank.py`, add imports near the top:

```python
from pathlib import Path
from fastapi import UploadFile, File
from ..constants import SCREENSHOTS_DIR  # NOT used; see QUESTION_IMG_DIR below
from ..constants import QUESTION_IMG_DIR
from ..utils import _safe_path_component
from ..parsers.document import extract_document, ScannedPdfError, UnreadableDocError
from ..parsers.question_parser import parse_questions
from ..parsers.region_render import render_region_png
import hashlib
```

Add a PNG-store helper (mirrors `upload_question_image`'s storage layout so the
existing `/api/v1/question-image/{tid}/{filename}` serve route works unchanged):

```python
def _store_region_png(tid: str, png: bytes) -> str:
    digest = hashlib.sha256(png, usedforsecurity=False).hexdigest()[:24]
    safe_tid = _safe_path_component(str(tid))
    tdir = Path(QUESTION_IMG_DIR) / safe_tid
    tdir.mkdir(parents=True, exist_ok=True)
    fpath = tdir / f"{digest}.png"
    if not fpath.exists():
        with open(fpath, "wb") as f:
            f.write(png)
    return f"/api/v1/question-image/{tid}/{digest}.png"
```

Add the endpoint:

```python
_MAX_UPLOAD = 20 * 1024 * 1024

@router.post("/api/v1/admin/question-bank/extract")
@limiter.limit("20/minute")
async def extract_questions(request: Request, file: UploadFile = File(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    name = (file.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".docx")):
        raise HTTPException(status_code=415,
            detail="Only PDF and Word (.docx) files are supported.")
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 20MB).")
    try:
        doc = extract_document(data, name)
    except ScannedPdfError:
        raise HTTPException(status_code=422,
            detail="This looks like a scanned PDF — text extraction isn't supported yet.")
    except UnreadableDocError as e:
        raise HTTPException(status_code=422, detail=str(e) or "Couldn't open this file.")
    questions = parse_questions(doc.text, lines=doc.lines)
    # Rasterize any region-flagged questions (PDF only).
    for q in questions:
        if q._region and doc.pdf_bytes is not None:
            png = render_region_png(doc.pdf_bytes, q._region["page"], q._region["bbox"])
            if png:
                q.image_url = _store_region_png(tid, png)
            else:
                if "has_image" in q.flags:
                    q.flags.remove("has_image")
                if "math_review" not in q.flags:
                    q.flags.append("math_review")
    public = [q.to_public() for q in questions]
    ready = sum(1 for q in public if not (set(q["flags"]) & {"no_answer", "low_confidence", "few_options", "parse_error"}))
    return {"found": len(public), "ready": ready, "questions": public}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_extract_endpoint.py -q`
Expected: PASS (5 passed). If `QUESTION_IMG_DIR` write fails under test, patch it to `tmp_path` in the preview test (only the math test writes).

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/routers/question_bank.py tests/test_qbank_extract_endpoint.py
git commit -m "feat(qbank): /extract endpoint (upload to preview)"
```

---

## Task 7: `/extract/confirm` endpoint (server-side safety + persist)

**Files:**
- Modify: `app/routers/question_bank.py` (model + endpoint)
- Test: `tests/test_qbank_extract_endpoint.py` (add confirm tests)

- [ ] **Step 1: Add failing tests**

```python
# append to tests/test_qbank_extract_endpoint.py
from unittest.mock import AsyncMock

def _confirm_body(qs):
    return {"questions": qs}

def test_confirm_rejects_blocking_flag(client):
    payload = _confirm_body([{
        "question": "x", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "", "tags": [], "image_url": "", "flags": ["no_answer"],
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400
    assert "no_answer" in r.text or "resolve" in r.text.lower()

def test_confirm_recomputes_flags_not_trusts_client(client):
    # Client lies: claims clean flags but the data still has no correct answer.
    payload = _confirm_body([{
        "question": "x", "type": "mcq_single", "options": {"A": "1", "B": "2"},
        "correct": "", "tags": [], "image_url": "", "flags": [],   # lying
    }])
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 400   # server recomputes no_answer

def test_confirm_persists_clean_questions(client):
    payload = _confirm_body([{
        "question": "2+2?", "type": "mcq_single", "options": {"A": "3", "B": "4"},
        "correct": "B", "tags": ["math"], "image_url": "", "flags": [],
    }])
    captured = {}
    class _Chain:
        def insert(self, rows): captured["rows"] = rows; return self
        async def execute(self):
            from unittest.mock import MagicMock
            r = MagicMock(); r.data = [{"id": "q-1"}]; return r
    with patch("app.auth.admin_auth._get_teacher_by_id", return_value=TEACHER), \
         patch("app.routers.question_bank._atable", side_effect=lambda t: _Chain()):
        r = client.post("/api/v1/admin/question-bank/extract/confirm",
                        json=payload, headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert captured["rows"][0]["teacher_id"] == "teacher-1"
    assert captured["rows"][0]["correct"] == "B"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_qbank_extract_endpoint.py -q -k confirm`
Expected: FAIL (endpoint missing).

- [ ] **Step 3: Implement**

Add model (inline, near the other bank models in `question_bank.py`):

```python
class ExtractConfirmIn(BaseModel):
    model_config = ConfigDict(strict=False)
    questions: list[dict]
```

Add the endpoint. It **recomputes** blocking flags server-side from the actual
data (never trusts client `flags`), then persists through the same insert shape
as the existing `import` endpoint:

```python
from ..parsers.question_parser import BLOCKING_FLAGS

def _recompute_blocking(item: dict) -> list:
    """Server-side truth for blocking flags — ignores client-sent flags."""
    flags = []
    qtype = str(item.get("type") or "mcq_single").lower()
    options = item.get("options") or {}
    correct = str(item.get("correct") or "").strip()
    has_image = bool(item.get("image_url"))
    if not correct:
        flags.append("no_answer")
    if qtype in ("mcq_single", "mcq_multi") and len(options) < 2:
        flags.append("few_options")
    if not str(item.get("question") or "").strip() and not has_image:
        flags.append("low_confidence")
    return flags

@router.post("/api/v1/admin/question-bank/extract/confirm")
@limiter.limit("20/minute")
async def confirm_extracted(request: Request, body: ExtractConfirmIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    items = body.questions or []
    if not items:
        raise HTTPException(status_code=400, detail="No questions to import.")
    if len(items) > 2000:
        raise HTTPException(status_code=413,
            detail=f"Too many questions ({len(items)}). Max 2000 — split the file.")
    # Server-side safety contract: recompute, reject if anything still blocks.
    for idx, item in enumerate(items):
        blocking = set(_recompute_blocking(item)) & BLOCKING_FLAGS
        if blocking:
            raise HTTPException(status_code=400,
                detail=f"Question {idx + 1} still needs attention ({', '.join(sorted(blocking))}). "
                       f"Resolve all flagged questions before importing.")
    rows = []
    for item in items:
        options = item.get("options") or {}
        raw_tags = item.get("tags", [])
        tags = ([str(t).strip() for t in raw_tags if t is not None and str(t).strip()]
                if isinstance(raw_tags, list)
                else [t.strip() for t in str(raw_tags).split(",") if t.strip()])
        rows.append({
            "teacher_id": tid,
            "question": str(item.get("question", "") or ""),
            "question_type": str(item.get("type", "mcq_single") or "mcq_single"),
            "options": json.dumps(options),
            "correct": str(item.get("correct", "")),
            "image_url": str(item.get("image_url", "") or ""),
            "tags": tags,
        })
    try:
        result = await _atable("question_bank").insert(rows).execute()
    except Exception as e:
        _qbank_log.error("[bank.confirm] insert failed tid=%s rows=%d: %s",
                         tid, len(rows), e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to import questions.")
    inserted = result.data or []
    return {"imported": len(inserted),
            "inserted_ids": [r.get("id") for r in inserted if r.get("id")]}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_qbank_extract_endpoint.py -q`
Expected: PASS (8 passed total).

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/routers/question_bank.py tests/test_qbank_extract_endpoint.py
git commit -m "feat(qbank): /extract/confirm with server-side flag revalidation"
```

---

## Task 8: Dashboard review UI

**Files:**
- Modify: `app/static/dashboard.html` (button + modal markup)
- Modify: `app/static/dashboard-app.js` (upload, render table, confirm)

No new JS test framework exists; verify with `node --check` and manual smoke.
Follow the existing dashboard patterns: `authFetch`, existing modal styles, and
the existing question-bank refresh function (`loadBankQuestions` or equivalent —
grep to confirm the exact name before wiring the refresh-on-success).

- [ ] **Step 1: Add the button + modal to `dashboard.html`**

Near the existing question-bank import control, add:

```html
<button id="qbank-pdf-import-btn" class="btn btn-secondary">Import from PDF/Word</button>
<input id="qbank-pdf-file" type="file" accept=".pdf,.docx" style="display:none">

<div id="qbank-extract-modal" class="modal" style="display:none">
  <div class="modal-card modal-lg">
    <div class="modal-head">
      <h3>Review imported questions</h3>
      <span id="qbank-extract-summary" class="muted"></span>
      <button id="qbank-extract-close" class="modal-x">&times;</button>
    </div>
    <div id="qbank-extract-body" class="modal-body"></div>
    <div class="modal-foot">
      <button id="qbank-extract-cancel" class="btn">Cancel</button>
      <button id="qbank-extract-confirm" class="btn btn-primary" disabled>Add to bank</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Wire the upload + render in `dashboard-app.js`**

```javascript
// ── Question-bank PDF/DOCX import ───────────────────────────
let _qbankExtracted = [];

document.getElementById('qbank-pdf-import-btn')?.addEventListener('click', () => {
  document.getElementById('qbank-pdf-file').click();
});

document.getElementById('qbank-pdf-file')?.addEventListener('change', async (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  e.target.value = '';                       // allow re-picking same file
  const fd = new FormData(); fd.append('file', f);
  const summary = document.getElementById('qbank-extract-summary');
  document.getElementById('qbank-extract-modal').style.display = 'flex';
  document.getElementById('qbank-extract-body').innerHTML =
    '<p class="muted">Reading your document… on-device, nothing leaves your server.</p>';
  summary.textContent = '';
  let res;
  try {
    res = await authFetch('/api/v1/admin/question-bank/extract',
                          { method: 'POST', body: fd });
  } catch (_) { res = null; }
  if (!res || !res.ok) {
    const msg = res ? (await res.json().catch(() => ({}))).detail : 'Upload failed';
    document.getElementById('qbank-extract-body').innerHTML =
      `<p class="error">${esc(msg || 'Could not read this file.')}</p>`;
    return;
  }
  const data = await res.json();
  _qbankExtracted = data.questions || [];
  _renderExtractTable(data);
});

const _BLOCKING = ['no_answer', 'low_confidence', 'few_options', 'parse_error'];

function _isBlocked(q){ return (q.flags || []).some(f => _BLOCKING.includes(f)); }

function _renderExtractTable(data){
  const body = document.getElementById('qbank-extract-body');
  document.getElementById('qbank-extract-summary').textContent =
    `${data.found} found — ${data.ready} ready, ${data.found - data.ready} need attention`;
  if (!_qbankExtracted.length){
    body.innerHTML = '<p class="muted">No questions detected — check the document format.</p>';
    return;
  }
  body.innerHTML = _qbankExtracted.map((q, i) => {
    const blocked = _isBlocked(q);
    const flagTxt = (q.flags || []).join(', ');
    const img = q.image_url ? `<img src="${esc(q.image_url)}" class="qbank-thumb" alt="">` : '';
    const opts = Object.entries(q.options || {}).map(([k, v]) =>
      `<div class="qbank-opt"><b>${k}.</b> <input data-i="${i}" data-opt="${k}" value="${esc(v)}"></div>`).join('');
    return `<div class="qbank-row ${blocked ? 'flagged' : ''}" data-i="${i}">
      <label><input type="checkbox" class="qbank-pick" data-i="${i}" ${blocked ? '' : 'checked'} ${blocked ? 'disabled' : ''}></label>
      ${img}
      <textarea class="qbank-stem" data-i="${i}">${esc(q.question)}</textarea>
      <div class="qbank-opts">${opts}</div>
      <input class="qbank-correct" data-i="${i}" placeholder="Correct" value="${esc(q.correct)}">
      ${flagTxt ? `<span class="qbank-flags">${esc(flagTxt)}</span>` : ''}
    </div>`;
  }).join('');
  _bindExtractEdits();
  _refreshConfirmEnabled();
}
```

- [ ] **Step 3: Wire edits + confirm**

```javascript
function _bindExtractEdits(){
  document.querySelectorAll('.qbank-stem').forEach(el => el.addEventListener('input', e => {
    _qbankExtracted[+e.target.dataset.i].question = e.target.value;
    _recheckRow(+e.target.dataset.i);
  }));
  document.querySelectorAll('.qbank-correct').forEach(el => el.addEventListener('input', e => {
    _qbankExtracted[+e.target.dataset.i].correct = e.target.value.trim();
    _recheckRow(+e.target.dataset.i);
  }));
  document.querySelectorAll('.qbank-opts input').forEach(el => el.addEventListener('input', e => {
    const i = +e.target.dataset.i;
    _qbankExtracted[i].options[e.target.dataset.opt] = e.target.value;
  }));
}

function _recheckRow(i){
  const q = _qbankExtracted[i];
  // mirror server _recompute_blocking so the UI unlocks rows the teacher fixed
  const flags = [];
  const opts = q.options || {};
  if (!String(q.correct || '').trim()) flags.push('no_answer');
  if (['mcq_single','mcq_multi'].includes(q.type) && Object.keys(opts).length < 2) flags.push('few_options');
  if (!String(q.question || '').trim() && !q.image_url) flags.push('low_confidence');
  q.flags = flags;
  const row = document.querySelector(`.qbank-row[data-i="${i}"]`);
  const pick = document.querySelector(`.qbank-pick[data-i="${i}"]`);
  const blocked = _isBlocked(q);
  if (row) row.classList.toggle('flagged', blocked);
  if (pick){ pick.disabled = blocked; if (blocked) pick.checked = false; }
  _refreshConfirmEnabled();
}

function _refreshConfirmEnabled(){
  const picked = [...document.querySelectorAll('.qbank-pick')].filter(c => c.checked && !c.disabled);
  document.getElementById('qbank-extract-confirm').disabled = picked.length === 0;
}

document.getElementById('qbank-extract-confirm')?.addEventListener('click', async () => {
  const picked = [...document.querySelectorAll('.qbank-pick')]
    .filter(c => c.checked && !c.disabled).map(c => _qbankExtracted[+c.dataset.i]);
  if (!picked.length) return;
  const res = await authFetch('/api/v1/admin/question-bank/extract/confirm',
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questions: picked }) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok){ alert(data.detail || 'Import failed'); return; }
  _closeExtractModal();
  if (typeof loadBankQuestions === 'function') loadBankQuestions();   // refresh bank
  if (typeof toast === 'function') toast(`Imported ${data.imported} questions`);
});

function _closeExtractModal(){
  document.getElementById('qbank-extract-modal').style.display = 'none';
  _qbankExtracted = [];
}
document.getElementById('qbank-extract-close')?.addEventListener('click', _closeExtractModal);
document.getElementById('qbank-extract-cancel')?.addEventListener('click', _closeExtractModal);
```

- [ ] **Step 4: Verify**

Run: `node --check app/static/dashboard-app.js`
Expected: no output (syntax OK).
Then manual smoke: upload a small PDF, confirm preview renders, fix a flagged row, import, see toast + bank refresh.

- [ ] **Step 5: Commit** *(DO NOT RUN)*

```bash
git add app/static/dashboard.html app/static/dashboard-app.js
git commit -m "feat(qbank): PDF/Word import review UI"
```

---

## Task 9: Full regression + audit sweep

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest -q`
Expected: previous 807 passed + the new tests (≈ +30), 33 skipped, 0 failed.

- [ ] **Step 2: JS lint/check**

Run: `node --check app/static/dashboard-app.js`
Expected: clean.

- [ ] **Step 3: Self-Review-Before-Commit (HARD RULE)**

Re-read every changed/new file. Audit each for: syntax, runtime (None/empty paths), config (deps present), cross-ref (imported names exist — e.g. `QUESTION_IMG_DIR`, `_safe_path_component`, `authFetch`, `esc`, `loadBankQuestions`/`toast`), auth (require_admin + tenant scoping on both endpoints), failure modes (every error path returns a clean status, never a 500). **State findings in the response before any commit.**

- [ ] **Step 4: Leave uncommitted**

Do **not** commit. Present a summary of all changes, test results, and audit findings. The user commits.

---

## Plan Self-Review

**Spec coverage:**
- On-device, no egress → Tasks 1–6 (pure libs, no network) ✓
- Text PDF + DOCX, scanned guard → Task 3 ✓
- Rule-based parser + review → Tasks 2/5 + Task 8 ✓
- All four types → Task 2 (true_false, numeric, mcq_single, mcq_multi promotion) ✓
- Math/diagram image preservation → Tasks 4/5/6 ✓
- Reuse existing import/table/image path → Tasks 6/7 (`_store_region_png` mirrors layout; confirm mirrors import insert) ✓
- Safety contract (server-side blocking revalidation) → Task 7 ✓
- Error handling matrix → Tasks 3/6/7 ✓
- Security (auth, tenant scope, in-memory, size cap, no exec) → Tasks 6/7 ✓
- Tests incl. confirm-revalidation → Tasks 1–7, 9 ✓

**Placeholder scan:** no TBD/TODO; all code blocks concrete. One named integration point (`loadBankQuestions`/`toast`) is explicitly gated with `typeof … === 'function'` and a grep-to-confirm note, not a placeholder.

**Type consistency:** `ParsedQuestion`/`DocLine` field names, `to_public()` keys (`type`, not `question_type`, in the JSON), `BLOCKING_FLAGS` set, and the `_recompute_blocking` mirror (server) ↔ `_recheckRow` (client) all align across tasks. The public JSON uses `type`; the DB insert maps `item.get("type")` → `question_type` column (matches existing `import`).

**Known risk to watch during execution:** Task 5's text↔DocLine block matching is the trickiest part. If sequential matching proves fragile on real multi-page PDFs, fall back to segmenting purely over DocLines (drop the text-only seg in the layout path) — the text-only `parse_questions(text)` path stays intact for the unit tests.
