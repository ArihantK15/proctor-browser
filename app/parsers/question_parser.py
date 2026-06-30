"""Rule-based question extractor. Pure: text (+optional layout) -> questions.

Pipeline: block segmentation -> option detection -> type inference ->
answer resolution (inline, then answer-key) -> confidence/flags -> (layout-aware)
math/visual routing for image preservation.

No I/O. The endpoint layer rasterizes any `_region` and sets `image_url`.
"""
import re
from typing import Any

from dataclasses import dataclass, field

from .answer_key import parse_answer_key, find_answer_key_block

BLOCKING_FLAGS = {"no_answer", "low_confidence", "few_options", "parse_error"}

# Question-number markers at line start: "1." "1)" "1 -" "Q1" "Q.1" "Q1."
_QNUM = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})\s*[\.\)\-]\s+|^\s*Q\.?\s*(\d{1,3})\b", re.I)
# Option markers: "(a)" "a)" "A." "(A)" "(1)"
_OPT = re.compile(r"^\s*[\(\[]?\s*([A-Fa-f1-6])\s*[\)\.\]]\s+(.*)$")
# A parenthesised/bracketed single marker, used to split a one-line option row
# like "(1) a (2) b (3) c (4) d" (the dominant Indian-coaching format).
_MULTI_OPT = re.compile(r"[\(\[]\s*([A-Fa-f1-9])\s*[\)\.\]]")
# Inline / keyworded answer: "Ans: C", "Answer - 4", "MathonGo Answer Key : (3)".
# Requires the answer keyword (so a stray "(3)" in a stem isn't an answer), and
# a trailing non-letter so a word like "carefully" can't be read as "C".
_INLINE_ANS = re.compile(
    r"(?i)\bans(?:wer)?(?:\s*keys?)?\s*[:\-]?\s*\(?\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)\s*\)?(?![A-Za-z])")
# A line that is just "[C]" / "[42]"
_INLINE_BRACKET = re.compile(r"^\s*\[\s*([A-Fa-f]{1,6}|\d+(?:\.\d+)?)\s*\]\s*$")
# A line that is exactly an answer-key heading (single-line form).
_HEADING_LINE = re.compile(r"(?i)^\s*(answers?|answer\s*keys?|solutions?)\s*:?\s*$")

_OPT_LETTERS = ["A", "B", "C", "D", "E", "F"]

# Math-font name fragments (Computer Modern math, symbol fonts, etc.).
_MATH_FONT_HINTS = ("CMMI", "CMSY", "CMEX", "MSAM", "MSBM", "Symbol",
                    "MathJax", "STIX", "Math")


@dataclass
class ParsedQuestion:
    question: str
    question_type: str
    options: dict[str, Any]
    correct: str
    tags: list[Any] = field(default_factory=list)
    image_url: str = ""
    confidence: float = 1.0
    flags: list[Any] = field(default_factory=list)
    _region: dict[str, Any] | None = None

    def to_public(self) -> dict[str, Any]:
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
    s = _QNUM.sub("", line, count=1).strip()
    # A bare "Q1." marker (number caught without its trailing separator) leaves
    # an orphan ".", ")" etc. — drop it so it doesn't head the stem.
    return re.sub(r"^[\.\)\-:]\s*", "", s).strip()


def _segment_blocks(text: str) -> list[Any]:
    """Return [(qnum, [text lines...]), ...] using question-number markers."""
    blocks = []
    cur_num = None
    cur_lines: list[Any] = []
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


def _strip_star(val: str) -> tuple[str, bool]:
    """A trailing/leading '*' on an option marks it correct (a common
    answer-embedded style). Returns (clean_value, was_starred)."""
    v = val.strip()
    if v.endswith("*"):
        return v[:-1].strip(), True
    if v.startswith("*"):
        return v[1:].strip(), True
    return v, False


def _split_inline_options(line: str):
    """Split a one-line option row "(1) a (2) b (3) c (4) d" into [a, b, c, d].

    Returns None unless there are >=2 markers forming a clean 1,2,3.. (or
    a,b,c..) sequence, so a stray "(3)" in a math stem won't be mistaken for
    options."""
    ms = list(_MULTI_OPT.finditer(line))
    if len(ms) < 2:
        return None

    def _idx(mk: str) -> int:
        mk = mk.upper()
        return int(mk) if mk.isdigit() else (ord(mk) - ord("A") + 1)

    seq = [_idx(m.group(1)) for m in ms]
    if seq[0] != 1 or any(seq[i + 1] - seq[i] != 1 for i in range(len(seq) - 1)):
        return None
    out = []
    for i, m in enumerate(ms):
        start = m.end()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(line)
        out.append(line[start:end].strip())
    return out


def _normalize_answer(ans: str, options: dict[str, Any]) -> str:
    """Canonicalise a raw answer token. Letters → upper. A bare number that
    indexes an existing option (1..N) → its letter — this maps "(3)" on a
    numbered-option MCQ to "C". A number with no options (numeric question)
    stays as the literal value."""
    if not ans:
        return ""
    a = str(ans).strip()
    if re.fullmatch(r"[A-Fa-f]{1,6}", a):
        return a.upper()
    if re.fullmatch(r"\d+", a) and options:
        n = int(a)
        if 1 <= n <= len(options):
            return _OPT_LETTERS[n - 1]
    return a


def _parse_block(qnum: int, lines: list[Any]) -> ParsedQuestion:
    stem_parts: list[Any] = []
    options: dict[str, Any] = {}
    inline_ans = ""
    starred: list[Any] = []
    next_letter = 0
    for ln in lines:
        mb = _INLINE_BRACKET.match(ln)
        if mb:
            inline_ans = mb.group(1)
            continue
        # Answer line first — "Answer Key : (3)" must not be mistaken for an
        # option row by the multi-option splitter below.
        ma = _INLINE_ANS.search(ln)
        if ma:
            inline_ans = ma.group(1)
            continue
        # One-line numbered options: "(1) a (2) b (3) c (4) d".
        multi = _split_inline_options(ln)
        if multi and next_letter == 0:
            for raw in multi:
                if next_letter >= len(_OPT_LETTERS):
                    break
                val, was_star = _strip_star(raw)
                letter = _OPT_LETTERS[next_letter]
                options[letter] = val
                if was_star:
                    starred.append(letter)
                next_letter += 1
            continue
        mo = _OPT.match(ln)
        if mo and next_letter < len(_OPT_LETTERS):
            letter = _OPT_LETTERS[next_letter]
            val, was_star = _strip_star(mo.group(2))
            options[letter] = val
            if was_star:
                starred.append(letter)
            next_letter += 1
            continue
        if not options:           # still accumulating the stem
            stem_parts.append(ln.strip())
        # lines after options that aren't answers are dropped (footers, etc.)

    # A starred option (or several, for multi-answer) is the answer when no
    # explicit "Ans:"/"[X]" was found.
    if starred and not inline_ans:
        inline_ans = "".join(starred)

    stem = " ".join(p for p in stem_parts if p).strip()
    inline_ans = _normalize_answer(inline_ans, options)

    opt_vals = [v.strip().lower() for v in options.values()]
    if options and set(opt_vals) <= {"true", "false"} and len(options) == 2:
        qtype = "true_false"
    elif not options:
        qtype = "numeric"
    else:
        qtype = "mcq_single"   # may be promoted to mcq_multi by answer length

    return ParsedQuestion(question=stem, question_type=qtype, options=options,
                          correct=inline_ans, tags=[])


def _finalise(q: ParsedQuestion) -> None:
    flags: list[Any] = []
    if q.options and re.fullmatch(r"[A-F]{2,6}", q.correct or ""):
        q.question_type = "mcq_multi"
    if not q.correct:
        flags.append("no_answer")
    if q.question_type in ("mcq_single", "mcq_multi") and len(q.options) < 2:
        flags.append("few_options")
    if not q.question and not q.image_url:
        flags.append("low_confidence")
    conf = 1.0 - 0.25 * len(flags)
    if len(q.question) < 5 and not q.image_url:
        conf -= 0.2
        if "low_confidence" not in flags:
            flags.append("low_confidence")
    q.flags = flags
    q.confidence = max(0.0, min(1.0, conf))


def _looks_garbled(text: str) -> bool:
    if "�" in text:        # U+FFFD replacement char
        return True
    bad = sum(text.count(c) for c in ("Î", "Ã", "Â", "â€"))
    return bad >= 2


def _line_is_mathy(dl) -> bool:
    if any(any(h in (f or "") for h in _MATH_FONT_HINTS) for f in (dl.fonts or set())):
        return True
    if getattr(dl, "has_image", False):
        return True
    return _looks_garbled(dl.text)


def _apply_layout(q: ParsedQuestion, block_lines: list[Any]) -> None:
    """block_lines: list[DocLine] for this question. Sets _region / flags."""
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


_PAGE_MARK = re.compile(r"(?i)^\s*(page\s+\d+(\s+of\s+\d+)?|\d+\s*/\s*\d+)\s*$")


def _norm_furniture(t: str) -> str:
    return re.sub(r"\d+", "#", t or "").strip().lower()


def _is_content_line(text: str) -> bool:
    """Lines that carry question structure must never be removed as furniture
    — critically question markers, which digit-normalise to a common "q#." and
    would otherwise look like a repeating running-head."""
    t = text or ""
    return (_qnum_of(t) is not None or _OPT.match(t) is not None
            or _INLINE_ANS.search(t) is not None)


def _strip_furniture(lines: list[Any]) -> list[Any]:
    """Drop header/footer furniture so it never bleeds into a question stem.

    Two signals, both conservative:
      1. A standalone page marker ("Page 3", "Page 3 of 10", "3/10").
      2. A line in the top/bottom 12% edge zone of its page whose digit-
         normalised text repeats across >=2 pages (institute names, running
         heads/feet). The edge-zone + repetition gate avoids nuking a real
         repeated option like "None of the above" that sits mid-page.
    DOCX lines (no bbox) only get the page-marker rule.
    """
    if not lines:
        return lines
    page_span: dict[str, Any] = {}
    for dl in lines:
        if dl.bbox is None:
            continue
        top, bot = dl.bbox[1], dl.bbox[3]
        lo, hi = page_span.get(dl.page, (top, bot))
        page_span[dl.page] = (min(lo, top), max(hi, bot))

    def in_edge_zone(dl) -> bool:
        if dl.bbox is None:
            return False
        span = page_span.get(dl.page)
        if not span:
            return False
        lo, hi = span
        h = hi - lo
        if h <= 0:
            return True
        band = 0.12 * h
        return dl.bbox[1] <= lo + band or dl.bbox[3] >= hi - band

    repeated: set[str] = set()
    pages = {dl.page for dl in lines}
    if len(pages) >= 2:
        from collections import defaultdict
        seen = defaultdict(set)
        for dl in lines:
            if in_edge_zone(dl) and not _is_content_line(dl.text):
                n = _norm_furniture(dl.text)
                if n:
                    seen[n].add(dl.page)
        repeated = {n for n, ps in seen.items() if len(ps) >= 2}

    out = []
    for dl in lines:
        if _is_content_line(dl.text):       # never strip question structure
            out.append(dl)
            continue
        if _PAGE_MARK.match(dl.text or ""):
            continue
        if repeated and in_edge_zone(dl) and _norm_furniture(dl.text) in repeated:
            continue
        out.append(dl)
    return out


def _split_lines_on_key(lines: list[Any]) -> tuple[list[Any], dict[int, Any]]:
    """Split DocLines into (body_lines, answer_key) on the last heading line."""
    heading_idx = None
    for i, dl in enumerate(lines):
        if _HEADING_LINE.match(dl.text.strip()):
            heading_idx = i
    if heading_idx is None:
        return lines, {}
    body = lines[:heading_idx]
    key_text = "\n".join(dl.text for dl in lines[heading_idx + 1:])
    return body, parse_answer_key(key_text)


def _segment_doclines(lines: list[Any]) -> list[Any]:
    """Return [[DocLine, ...], ...] grouped by question-number markers."""
    blocks: list[Any] = []
    cur: list[Any] | None = None
    for dl in lines:
        if _qnum_of(dl.text) is not None:
            if cur is not None:
                blocks.append(cur)
            cur = [dl]
        elif cur is not None:
            cur.append(dl)
    if cur is not None:
        blocks.append(cur)
    return blocks


def _build_question(qnum: int, text_lines: list[Any], key: dict[int, Any]) -> ParsedQuestion:
    try:
        q = _parse_block(qnum, text_lines)
        if not q.correct and qnum in key:
            q.correct = _normalize_answer(key[qnum], q.options)
        _finalise(q)
        return q
    except Exception:
        return ParsedQuestion(question=" ".join(text_lines)[:200],
                              question_type="mcq_single", options={}, correct="",
                              flags=["parse_error"], confidence=0.0)


def parse_questions(text: str, lines: list[Any] | None = None) -> list[Any]:
    """text (+optional DocLine list for math/image routing) -> questions.

    Text-only path is used by unit tests and DOCX-without-layout. When `lines`
    is provided, segmentation runs over the DocLines so text and layout stay
    perfectly aligned, and math/visual blocks are routed to image preservation.
    """
    out: list[Any] = []
    if lines:
        lines = _strip_furniture(lines)
        body_lines, key = _split_lines_on_key(lines)
        for dl_block in _segment_doclines(body_lines):
            qnum = _qnum_of(dl_block[0].text)
            text_lines = [_strip_qnum(dl_block[0].text)] + \
                         [dl.text.rstrip() for dl in dl_block[1:]]
            q = _build_question(qnum, text_lines, key)
            if "parse_error" not in q.flags:
                _apply_layout(q, dl_block)
            out.append(q)
        return out

    body, key = find_answer_key_block(text or "")
    for qnum, text_lines in _segment_blocks(body):
        out.append(_build_question(qnum, text_lines, key))
    return out
