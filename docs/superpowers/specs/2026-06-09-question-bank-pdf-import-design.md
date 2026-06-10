# Question Bank — PDF/DOCX Import (v1) Design

**Date:** 2026-06-09
**Status:** Approved (design); implementation not yet started
**Author:** Arihant + Claude (brainstorming session)

---

## Goal

Let a teacher upload a PDF or Word document of existing questions and import them
into the **existing** Procta question bank, with a mandatory human-in-the-loop
review step — turning "re-type 400 questions" into "confirm ~350, fix ~50."

## Why (business context)

The ICP is coaching institutes (Allen, Aakash, PW) sitting on years of legacy
question banks as PDFs/Word docs. The single largest switching cost to adopting
Procta is re-entering those banks by hand. Removing that friction is the feature
most likely to convert a pilot. It is moat-adjacent: it compounds with the
existing AI-grading and analytics story.

## Non-negotiable constraints

- **On-device for v1.** Extraction runs in the FastAPI server (the institute's
  own DigitalOcean deployment). Question content is **never** sent to a
  third-party AI API in v1. Rationale: a coaching institute's question bank is
  its crown-jewel IP; "your questions never leave your server" is a sales
  weapon, not just a privacy stance. (Note: the *existing* Groq-backed
  `generate`/`suggest-tags` features remain as-is; a separate honesty-disclosure
  is noted as future work, out of scope here.)
- **Math must never come out garbled.** For a JEE/NEET ICP, garbled math is a
  dealbreaker. v1 sidesteps garbling by *preserving* garble-prone math/diagram
  questions as faithful page-rasterized images rather than converting them to
  text. Editable LaTeX math (math-OCR → LaTeX → KaTeX) is the committed **Phase
  2** upgrade and requires no rework of v1 image questions.
- **Additive & low-risk.** Reuse the existing `question_bank` table, the existing
  `import` persistence path, and the existing question-image upload/serve path.
  No new tables in v1.
- **Standing rules:** Self-Review-Before-Commit HARD RULE; never commit/stage/push
  (the user commits); no exec/eval, no shelling out, no external fetch.

## Decisions (locked during brainstorming)

| # | Decision |
|---|----------|
| 1 | On-device v1; cloud "AI enhance" deferred (Phase 2). |
| 2 | Inputs: **text-based PDF + DOCX**. Scanned/image-only PDF → friendly 422 guard. |
| 3 | Engine: **rule-based block parser** + mandatory human review (not an ML model). |
| 4 | Types: **mcq_single, mcq_multi, numeric/integer, true_false** (all auto-gradable downstream; verified supported in scoring.py). |
| 5 | Math/diagrams: **faithful-image preservation** in v1 (never garbles, still auto-graded). **LaTeX-OCR is Phase 2**, no rework. |
| 6 | Diagrams auto-attached via the same region→image mechanism as math. |

---

## Architecture & Data Flow

The feature is a 5-stage pipeline behind the existing `question_bank` router and
table. Stages ②–④ are **pure transforms with no DB writes** — nothing touches
the bank until the teacher confirms.

```
①  Upload          POST /api/v1/admin/question-bank/extract   (multipart: file)
        │           require_admin → tenant-scoped to teacher_id; in-memory only
        ▼
②  Text extract    parsers/document.py
        │           PDF  → pdfplumber (text + per-char font/bbox metadata)
        │           DOCX → python-docx
        │           scanned/image-only PDF (≈no extractable text) → 422
        ▼
③  Parse           parsers/question_parser.py   (pure function, the rule engine)
        │           text+layout → question blocks → options → answer-key match
        │           per-question confidence + flags[]
        ▼
④  Math/visual     parsers/region_render.py
        │           garble-prone / image regions → rasterize page rect → PNG
        │           via pypdfium2 → stored through EXISTING upload-question-image
        │           path → image_url
        ▼
⑤  Review payload  returns a PREVIEW (nothing persisted):
                    [{question, type, options, correct, image_url, tags,
                      confidence, flags[]}]

   Teacher edits/confirms in review UI → "Add to bank" →
   POST /api/v1/admin/question-bank/extract/confirm  (re-validates flags
   server-side) → routes through existing import persistence.
```

### New files (one responsibility each)

- `app/parsers/__init__.py`
- `app/parsers/document.py` — bytes → normalized text + layout metadata
  (PDF via pdfplumber, DOCX via python-docx). **Knows nothing about questions.**
- `app/parsers/question_parser.py` — text+layout → structured question list with
  confidence/flags. **Pure function, no I/O — unit-testable with text fixtures.**
- `app/parsers/region_render.py` — (PDF bytes, page index, bbox) → PNG bytes
  (the math/diagram preserver, via pypdfium2).
- Two new endpoints in the existing `app/routers/question_bank.py`:
  `/extract` and `/extract/confirm`.
- New Pydantic models in `app/models.py` for the extract/confirm payloads.

### New dependencies (all permissive, on-device, no system binaries)

- `pdfplumber` (MIT / pdfminer.six) — PDF text + per-char font & bbox.
- `python-docx` (MIT) — DOCX text.
- `pypdfium2` (BSD/Apache, PDFium) — page-region rasterization.
- **Deliberately NOT PyMuPDF** (AGPL — license-incompatible with a proprietary
  product).

---

## The parser engine (`question_parser.py`)

**Stage A — Block segmentation.** Split text into question blocks by detecting
number markers at line start: `1.` `1)` `Q1` `Q.1` `1 -`. Lock onto the
*dominant* marker style in the doc (a stray "1." inside an option must not
false-trigger). Everything between marker N and N+1 is block N.

**Stage B — Option detection.** Within a block, lines matching option markers
(`(a)` `a)` `A.` `(A)` `(1)`) become options A–F; text before the first option
is the stem. Zero options + numeric answer → **numeric/integer**. Exactly two
true/false options → **true_false**.

**Stage C — Answer resolution** (priority order):
1. **Inline** — `Ans: C`, `Answer - C`, `[C]`, or a bolded/asterisked option.
2. **Answer-key section** — trailing `1-C 2-A 3-D` block or `Answers` table,
   parsed separately and joined **by question number** (how most institute PDFs
   store answers).
3. **None found** → `correct` blank, flag `no_answer`.

A key listing multiple letters (`1-AC`) promotes the question to **mcq_multi**.

**Stage D — Math/visual detection → image preservation.** Route a block to
`region_render.py` when ANY of:
- characters use **symbol/math fonts** (pdfplumber per-char font-name heuristics),
- **encoding artifacts** present — replacement chars (`U+FFFD`), high ratio of
  unmapped glyphs, suspicious `Î`/`Ã` mojibake sequences,
- the page has an **embedded image object** overlapping the block bbox (diagram).

On trigger: rasterize the block bbox → PNG → store via existing
upload-question-image path → set `image_url`. Keep stem text if clean; else the
image carries the whole question. **Bias: when in doubt on a math-flagged doc,
preserve as image.** DOCX has no reliable bbox → DOCX math falls back to keeping
text + `math_review` flag (DOCX garble is rare; Word stores OMML/Unicode).

**Stage E — Confidence & flags.** Each question carries a 0–1 confidence and a
`flags[]` list:

| Flag | Meaning | Review behavior |
|------|---------|-----------------|
| *(none, conf ≥ 0.8)* | clean parse | pre-checked, ready |
| `no_answer` | answer not found | **blocking** — teacher must set correct |
| `low_confidence` | ambiguous stem/options | **blocking** — highlighted, confirm |
| `has_image` | diagram/math preserved as image | shows image preview |
| `few_options` | <2 options on an MCQ | **blocking** — highlighted |
| `math_review` (DOCX) | possible math, no bbox | highlighted |
| `parse_error` | block raised during parse | skipped from commit, surfaced |

**Safety contract:** a question with any **blocking** flag cannot be committed
until resolved — enforced **server-side at `/extract/confirm`**, not just in UI.
High-confidence questions are pre-checked. Nothing wrong slips in silently.

---

## Review UI (existing dashboard Questions/Bank tab — no new tab)

- An **"Import from PDF/Word"** button beside the existing import. File picker →
  upload to `/extract` → spinner: "Reading your document… on-device, nothing
  leaves your server."
- A **review table**, one row per parsed question: editable stem, type dropdown,
  editable options, `correct` selector, tags, image thumbnail where preserved.
  Flagged rows are visually highlighted with their reason; clean rows pre-checked.
- Header summary: *"47 questions found — 38 ready, 9 need your attention."*
- **"Add N to bank"** disabled until every *checked* row is clean. Confirm →
  routes through existing import → success toast → bank refreshes.

---

## Error handling (server — always a clean message, never a 500)

- Not PDF/DOCX, or over size cap (20 MB) → `415` / `413`.
- Scanned/image-only PDF (≈no extractable text) → `422` "This looks like a
  scanned PDF — text extraction isn't supported yet."
- Encrypted/corrupt PDF → `422` "Couldn't open this file."
- Zero questions parsed → `200` with empty list + "No questions detected" (not an
  error).
- Per-block parse exception → that block skipped with `parse_error` flag; the
  rest still return (one bad question never sinks the batch).
- Rasterization failure → fall back to text + `math_review` flag.
- `/extract/confirm` **re-validates** blocking flags server-side (defense in
  depth — never trust the client's "this is clean").

## Security (standing rules)

- `require_admin` + tenant-scoped `teacher_id` on both endpoints; images stored
  under the teacher's own dir via the existing path-safe uploader.
- Uploaded bytes parsed **in-memory**; the original file is never persisted to
  disk. Only derived question images persist.
- Size + page caps prevent giant-PDF DoS; rate-limited like other bank endpoints.
- No exec/eval, no shelling out, no external fetch — pure library parsing.

## Testing (TDD; pure-function core = easy)

- `question_parser.py` unit tests, text fixtures, **zero mocks**: numbered MCQs,
  `(a)`-vs-`A.` option styles, inline answers, end-of-doc answer key, multi-answer
  key, numeric, true/false, missing answers, mixed numbering, junk between
  questions.
- `document.py` tests with tiny committed sample fixtures: a clean PDF, a
  scanned-image PDF (→422), an encrypted PDF; a clean DOCX.
- `region_render.py` test: a math-font fixture routes to image; assert PNG bytes
  produced.
- Endpoint tests: auth required, tenant scoping, each error path, and the
  **confirm-revalidates-flags** safety contract (a forged "clean" payload
  carrying `no_answer` is rejected).
- Full-suite regression (currently 807 passed / 33 skipped) stays green.

## Out of scope for v1 (explicit)

- Cloud "AI enhance" extraction.
- LaTeX-OCR editable math (**Phase 2**, no rework — v1 image questions stay valid).
- Scanned-PDF OCR.
- Auto-positional image matching beyond block-bbox.
- Passage/comprehension ("read paragraph, then Q1–Q5") grouping.
- Honesty-disclosure retrofit for the existing Groq features (separate effort).

## Phase 2 (committed follow-up)

Math-OCR → LaTeX → KaTeX rendering across student exam UI + dashboard, replacing
image-backed math questions with editable/searchable LaTeX. v1 image questions
remain valid; no migration required.
