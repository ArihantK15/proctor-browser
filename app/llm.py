"""Groq-backed LLM helpers.

Why Groq specifically and not OpenAI/Anthropic/Bedrock:
  • Sub-second latency on Llama 3.3 70B is the difference between
    "click generate, get coffee" and "click generate, see results."
    The teacher UX falls apart at >3s.
  • OpenAI-compatible API, so we don't pay the cost of yet another
    SDK; one httpx call is the whole client.
  • JSON mode is reliable on the Groq Llama deployments — we get
    valid JSON back ~99% of the time without retry logic.

Single-file module on purpose. If we later add other LLM features
(scorecard insights, auto-tag) they belong here too — keeping all
prompt + token + provider concerns in one place is worth more than
"clean separation by feature." When this file gets to ~400 lines we
split.

Failure mode: every public function is wrapped in a single try/except
that returns a structured error rather than raising. Endpoints turn
that into a 502. We never crash a request because the LLM is flaky.
"""

from __future__ import annotations

import json
import os
import logging
from typing import Optional

import httpx


log = logging.getLogger("llm")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
# Llama 3.3 70B is the sweet spot — big enough to follow a structured
# JSON schema reliably, small enough that Groq serves it at ~600 tok/s
# so a 10-question generation comes back in <2 s. Override per-call
# if a future feature needs Mixtral or 8B.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/")
# 30 s is generous — Groq's median latency is ~1 s, but we'd rather
# cap a hung request than wedge a worker.
GROQ_TIMEOUT = float(os.environ.get("GROQ_TIMEOUT", "30"))


def is_configured() -> bool:
    """Whether the LLM features are usable. Endpoints check this and
    return 503 with a clear "set GROQ_API_KEY" message rather than
    leaking a generic 502 from httpx."""
    return bool(GROQ_API_KEY)


def _chat_json(system: str, user: str, *, max_tokens: int = 4000,
               temperature: float = 0.7) -> dict:
    """Single-shot chat completion that returns parsed JSON.

    Raises a generic Exception on transport / JSON / API errors so the
    caller can decide how to surface it. We force ``response_format``
    to JSON object mode so the model can't sneak in prose around the
    payload — that was the #1 source of breakage on earlier providers.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=GROQ_TIMEOUT) as client:
        r = client.post(f"{GROQ_BASE_URL}/chat/completions",
                        json=payload, headers=headers)
        if r.status_code >= 400:
            # Don't echo the model's full error body to clients —
            # Groq's errors sometimes include the prompt back, which
            # could leak teacher input via logs. Keep the body for
            # server-side debugging only.
            log.warning("groq %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        body = r.json()
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


# ── Question generation ──────────────────────────────────────────────

_QGEN_SYSTEM = """You are an expert exam writer for Indian school and college students. \
You produce multiple-choice questions that are clear, unambiguous, and have exactly one correct answer per question \
unless the user requests a multi-answer type. You always return valid JSON matching the user's requested schema.

Rules:
- Each question has exactly 4 options labelled A, B, C, D.
- The correct answer is one of A/B/C/D (or comma-separated like "A,C" only if question_type is "mcq_multi").
- Avoid trick wording, double negatives, or "all of the above" / "none of the above" — those test reading comprehension, not the subject.
- Distractors must be plausible. A bad distractor is "Paris" on a chemistry question; a good distractor is a related-but-wrong concept.
- Tags should be 2-4 short lowercase words (subject, topic, difficulty). Example: ["physics","kinematics","easy"].
- Never copy questions verbatim from copyrighted textbooks. Paraphrase any source material."""


def generate_questions(
    topic: str,
    count: int,
    *,
    difficulty: str = "mixed",     # "easy" | "medium" | "hard" | "mixed"
    question_type: str = "mcq_single",
    source_text: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> list[dict]:
    """Generate question-bank-shaped dicts ready for the import endpoint.

    Returns rows in the same shape ``import_bank_questions`` expects:
    ``{question, question_type, option_A..D, correct, tags}``. The
    caller is responsible for showing them to the teacher for review
    before inserting — we never auto-insert generated content.

    Length sanity: count is capped at 25 because longer batches start
    to lose schema discipline (the model gets sloppy past ~3000 tok
    output) and because a teacher reviewing 25 questions is already a
    lot of clicking. If they want more they can run generate twice.
    """
    count = max(1, min(int(count or 10), 25))
    difficulty = difficulty if difficulty in ("easy", "medium", "hard", "mixed") else "mixed"
    qtype = question_type if question_type in ("mcq_single", "mcq_multi") else "mcq_single"

    grade_hint = f" Target grade level: {grade_level}." if grade_level else ""
    src_hint = ""
    if source_text:
        # Cap source text — Llama 3.3 70B has 128k context but Groq
        # bills per token and a teacher pasting a textbook chapter
        # would burn budget for no quality gain over the first ~4 k
        # chars (which is enough to pin the topic).
        clipped = source_text.strip()[:4000]
        src_hint = (
            "\n\nUse this source material as the basis for the questions. "
            "Paraphrase — do not copy sentences verbatim:\n---\n"
            + clipped + "\n---"
        )

    user = f"""Generate {count} {qtype} questions on: {topic}.{grade_hint}

Difficulty: {difficulty}.

Return JSON in exactly this shape (no extra keys, no commentary):

{{
  "questions": [
    {{
      "question": "...",
      "question_type": "{qtype}",
      "option_A": "...",
      "option_B": "...",
      "option_C": "...",
      "option_D": "...",
      "correct": "A",
      "tags": ["...", "..."]
    }}
  ]
}}{src_hint}"""

    parsed = _chat_json(_QGEN_SYSTEM, user, max_tokens=4000, temperature=0.7)
    qs = parsed.get("questions") or []
    if not isinstance(qs, list):
        raise RuntimeError("LLM returned non-list 'questions' field")

    # Defensive normalisation — Llama occasionally drops a key or
    # returns ``correct`` as a full option string instead of a letter.
    # We coerce here so the import endpoint never sees garbage.
    cleaned: list[dict] = []
    for q in qs[:count]:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question") or "").strip()
        if not question:
            continue
        opts = {
            "option_A": str(q.get("option_A") or "").strip(),
            "option_B": str(q.get("option_B") or "").strip(),
            "option_C": str(q.get("option_C") or "").strip(),
            "option_D": str(q.get("option_D") or "").strip(),
        }
        if not all(opts.values()):
            continue  # incomplete option set — skip rather than insert blanks
        correct = str(q.get("correct") or "").strip().upper()
        # If the model returned the option text instead of the letter,
        # try to map it back. Fall through to "A" only as last resort.
        if correct not in ("A", "B", "C", "D") and not _looks_like_multi(correct):
            for letter, text in opts.items():
                if correct.lower() == text.lower():
                    correct = letter[-1]
                    break
            else:
                correct = "A"
        tags = q.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        tags = [str(t).strip().lower() for t in tags if str(t).strip()][:6]
        cleaned.append({
            "question": question,
            "question_type": qtype,
            **opts,
            "correct": correct,
            "tags": tags,
            "image_url": "",
        })
    return cleaned


def _looks_like_multi(s: str) -> bool:
    """Whether a 'correct' string looks like a valid multi-answer key
    (e.g. 'A,C' or 'B, D'). Used to skip the option-text fallback for
    multi-answer questions."""
    parts = [p.strip().upper() for p in s.split(",") if p.strip()]
    return bool(parts) and all(p in ("A", "B", "C", "D") for p in parts)


# ── Auto-tag (one-shot) ──────────────────────────────────────────────

def suggest_tags(question: str, options: dict, correct: str) -> list[str]:
    """Return 3-5 lowercase tags for a single question. Used by the
    'Generate tags' button on Save-to-Bank — turns a chore into a
    one-click action and produces consistent taxonomy across the
    bank (so the search box actually finds related questions).
    """
    if not question.strip():
        return []
    user = f"""Suggest 3 to 5 short lowercase tags for this exam question. \
Tags should describe subject, topic, difficulty, and grade level when inferable. \
Use single words or hyphenated terms. Return JSON: {{"tags": ["...", "..."]}}.

Question: {question}
Options: {json.dumps(options)}
Correct: {correct}"""
    parsed = _chat_json(
        "You are a precise taxonomist. Return only the JSON object.",
        user, max_tokens=200, temperature=0.3,
    )
    tags = parsed.get("tags") or []
    if not isinstance(tags, list):
        return []
    return [str(t).strip().lower() for t in tags if str(t).strip()][:5]
