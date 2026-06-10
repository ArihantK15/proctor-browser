# AI Questions from Notes (v1) Design

**Date:** 2026-06-10 · **Status:** Approved (design)

## Goal
Teacher uploads study material (PDF / DOCX / PPTX); Procta extracts the text and
uses the existing cloud LLM `generate_questions(source_text=...)` to draft
review-ready questions for the bank.

## Decisions
1. **Cloud (Groq) + explicit consent label.** Reuses the existing (already-cloud)
   generate path. Add the same one-line "sends text to our AI provider"
   disclosure to the existing generate + suggest-tags controls.
2. **Notes drive it; controls optional.** Topic OPTIONAL (focus hint); teacher
   sets count / difficulty / question_type. No mandatory topic.
3. **Inputs:** PDF, DOCX, PPTX. Scanned/image-only/empty → 422 guard.
4. **Big docs:** use first ~20k chars + clear "used first ~15 pages" warning.
   (Chunking is a fast-follow, out of scope.)

## Architecture
One new endpoint; everything else reused.

```
POST /api/v1/admin/question-bank/generate-from-file  (multipart: file + params)
  require_admin → tenant-scoped
  ① extract text  — document.py: PDF (pdfplumber), DOCX (python-docx) reused;
                    PPTX via NEW _extract_pptx (python-pptx). empty/scanned → 422
  ② truncate to ~20k chars; set truncated=True if longer
  ③ llm.generate_questions(source_text, topic?, count, difficulty, question_type)
     — 503 if is_configured() is false
  ④ return preview (same bank-ready shape the existing /generate flow renders)
     + truncated notice. Teacher reviews/edits → existing confirmed bank-insert.
```

**Reuse:** existing generate review→bank UI (output shape already matches
`generate_questions`); existing bank-insert path; `document.py` PDF/DOCX
extraction. **New:** `_extract_pptx` (slides' text frames + speaker notes +
tables → text, one DocLine per slide, bbox=None), one endpoint, file-upload UI +
consent label.

**Never auto-insert** — LLM output is always human-reviewed before it reaches the
bank.

## Errors (clean status, never 500)
- not PDF/DOCX/PPTX or >20MB → 415 / 413
- scanned/image-only/empty text → 422 "Couldn't find enough text…"
- corrupt/encrypted → 422 "Couldn't open this file."
- LLM unconfigured → 503 (mirrors existing generate)
- LLM empty/malformed → 200 empty list + "Couldn't generate… try a clearer section."
- oversized → 200 with truncated:true + notice

## Security
require_admin + tenant scope; bytes parsed in-memory, file never persisted; only
egress is extracted text → existing LLM client (no new third party); explicit
consent label; disclosure retrofit on existing generate/suggest-tags.

## Testing (TDD)
- `_extract_pptx` units: tiny .pptx fixture (slide text + speaker note + table) →
  all text present, slide order preserved.
- Endpoint: auth, tenant scope, each error path (415/413/422/503), truncated flag;
  `generate_questions` MOCKED (no real cloud call); preview shape asserted.
- Full suite stays green.

## Out of scope (v1)
Chunking large docs; slide/diagram OCR; on-device generation; auto-insert.
