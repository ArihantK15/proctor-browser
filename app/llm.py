"""Provider-agnostic LLM helpers.

Talks to any OpenAI-compatible chat-completions endpoint. The default
is Groq (free tier: 14,400 req/day on Llama 3.3 70B, no credit card),
but the same code transparently works with any provider exposing
`POST /chat/completions` with the standard schema:

  Provider     Free tier?           Base URL
  ──────────   ──────────────────   ───────────────────────────────
  Groq         14,400/day, fast     https://api.groq.com/openai/v1
  OpenRouter   :free model suffix   https://openrouter.ai/api/v1
  Cerebras     ~10,000/day          https://api.cerebras.ai/v1
  Together.ai  small models free    https://api.together.xyz/v1
  Anthropic    paid                 https://api.anthropic.com/v1
  OpenAI       paid                 https://api.openai.com/v1
  Local Ollama free, slow on CPU    http://localhost:11434/v1

To switch provider, just set three env vars (see .env.example):
  LLM_API_KEY    — bearer token
  LLM_BASE_URL   — provider's chat-completions root
  LLM_MODEL      — model identifier the provider uses

Backwards-compat: the old GROQ_* vars still work as fallbacks so a
running container with only GROQ_API_KEY set keeps working.

Why one provider and not retry-ladder fan-out across multiple:
  • Free tiers are fragile. Adding fallback to OpenAI when Groq fails
    surprises operators with a paid bill on a quiet night.
  • Latency: each fallback adds 1+ second of dead air per failed call.
  • Cleaner mental model: one knob (LLM_BASE_URL) flips everything.

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

# ── Provider config (env-driven) ────────────────────────────────────
# New canonical names: LLM_*. Legacy GROQ_* read as fallback so an
# existing deployment with only GROQ_API_KEY in .env keeps working
# without a config edit at deploy time.
LLM_API_KEY = (os.environ.get("LLM_API_KEY")
               or os.environ.get("GROQ_API_KEY") or "").strip()
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL")
                or os.environ.get("GROQ_BASE_URL")
                or "https://api.groq.com/openai/v1").strip().rstrip("/")
# Llama 3.3 70B is the sweet spot for question generation — big enough
# to follow a JSON schema reliably, small enough that the Groq /
# Cerebras free tiers serve it fast. Switch this to e.g.
# "google/gemini-2.0-flash-exp:free" when LLM_BASE_URL points at
# OpenRouter, or "llama3.1-8b" for local Ollama.
LLM_MODEL = (os.environ.get("LLM_MODEL")
             or os.environ.get("GROQ_MODEL")
             or "llama-3.3-70b-versatile").strip()
# 30 s is generous — most providers' median is ~1 s for our prompts;
# we'd rather cap a hung request than wedge a worker.
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT")
                    or os.environ.get("GROQ_TIMEOUT", "30"))

# Legacy aliases — keep so the rest of the module's API is stable.
GROQ_API_KEY = LLM_API_KEY
GROQ_MODEL = LLM_MODEL
GROQ_BASE_URL = LLM_BASE_URL
GROQ_TIMEOUT = LLM_TIMEOUT


def is_configured() -> bool:
    """Whether the LLM features are usable. Endpoints check this and
    return 503 with a clear error rather than leaking a generic 502."""
    return bool(LLM_API_KEY)


def _chat_json(system: str, user: str, *, max_tokens: int = 4000,
               temperature: float = 0.7) -> dict:
    """Single-shot chat completion that returns parsed JSON.

    Raises a generic Exception on transport / JSON / API errors so the
    caller can decide how to surface it. We force ``response_format``
    to JSON object mode so the model can't sneak in prose around the
    payload — that was the #1 source of breakage on earlier providers.
    Some providers (notably OpenRouter on certain :free models) ignore
    response_format silently; we don't error on missing JSON mode and
    rely on the system prompt's "Return only JSON" instruction as the
    backstop.
    """
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY (or GROQ_API_KEY) not configured")
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    # OpenRouter likes a Referer + Title header for attribution; harmless
    # on every other provider so we always send them.
    if "openrouter" in LLM_BASE_URL:
        headers["HTTP-Referer"] = "https://procta.net"
        headers["X-Title"] = "Procta"
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        r = client.post(f"{LLM_BASE_URL}/chat/completions",
                        json=payload, headers=headers)
        if r.status_code >= 400:
            # Don't echo the model's full error body to clients — some
            # providers' errors include the prompt back, which could
            # leak teacher input via logs. Keep the body for server-
            # side debugging only.
            log.warning("llm %s %s: %s", LLM_BASE_URL, r.status_code,
                        r.text[:500])
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


# ── Scorecard insights ──────────────────────────────────────────────

_INSIGHT_SYSTEM = """You are a supportive teacher writing a brief personalised note \
to a student about their exam result. You speak directly to the student in second person ("you"). \
Your tone is warm but honest — you celebrate what went well, name what went wrong specifically, \
and end with one concrete next step.

Rules:
- Total length: 2 to 4 short sentences. No lists. No headings.
- Be specific. Reference actual question topics from the input. Do not say "you did well overall" or \
"keep practising" — those phrases are banned.
- If the student passed, lead with the strength. If they failed, lead with empathy then the next step.
- Never reveal the correct answer to questions they got wrong — they may retake the exam.
- No emojis. No exclamation marks. No "great job!"-style filler."""


def scorecard_insight(summary: dict, per_question: list[dict]) -> str:
    """Generate a 2-4 sentence personalised note for a student's scorecard.

    ``summary`` is the dict from ``_build_scorecard_pdf`` — has score,
    total, percentage, passed, exam_title, plus the original exam dict.
    ``per_question`` is the list of {question, student_answer,
    correct_answer, is_correct} rows from the same builder.

    Returns a single string. On any failure (LLM down, bad JSON,
    empty input), returns ``""`` — the caller treats empty as
    "no insight, render the PDF without it" rather than crashing.
    Insights are nice-to-have; they must never block a scorecard.
    """
    if not is_configured():
        return ""
    if not per_question:
        return ""

    # Build a compact representation — we don't need full question text
    # in the prompt, just enough for the model to identify topic
    # patterns. Truncate at 25 questions to keep tokens bounded; if a
    # student took a 100-question exam, the model only sees the first
    # 25 but that's enough to spot the strength/weakness pattern.
    correct = sum(1 for q in per_question if q.get("is_correct"))
    wrong = [q.get("question", "")[:120] for q in per_question[:25]
             if not q.get("is_correct")][:8]
    right = [q.get("question", "")[:120] for q in per_question[:25]
             if q.get("is_correct")][:8]

    score = summary.get("score", 0)
    total = summary.get("total", len(per_question))
    pct = summary.get("percentage", 0)
    passed = bool(summary.get("passed"))

    user = f"""Student score: {score}/{total} ({pct}%). {'PASSED.' if passed else 'DID NOT PASS.'}
Exam: {summary.get("exam_title") or "Exam"}

Questions they got RIGHT (sample, truncated):
{chr(10).join(f"- {q}" for q in right) or "(none)"}

Questions they got WRONG (sample, truncated):
{chr(10).join(f"- {q}" for q in wrong) or "(none)"}

Write the personalised note. Return JSON: {{"note": "..."}}."""

    try:
        parsed = _chat_json(_INSIGHT_SYSTEM, user, max_tokens=300, temperature=0.6)
        note = str(parsed.get("note") or "").strip()
        # Hard cap so a runaway model can't blow out the PDF layout.
        # 600 chars is roughly 4 sentences.
        return note[:600]
    except Exception as e:
        log.warning("scorecard_insight failed: %s", e)
        return ""


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


# ── Live risk triage ─────────────────────────────────────────────────

_TRIAGE_SYSTEM = """You are an exam invigilator's assistant. Given a session's recent \
violation log and metadata, write ONE sentence (max 22 words) summarising what's \
notable. Be concrete and concise — name specific behaviours and timing. Lead with \
the most concerning pattern. If nothing is concerning, say "No concerning patterns." \
verbatim. Never speculate beyond the evidence; never recommend an action.

Banned phrases: "appears to", "may indicate", "suggests that", "could be", \
"it seems", "based on the data". They sound mealy-mouthed and waste words.

Output JSON: {"summary": "..."}"""


def live_risk_triage(session_meta: dict, violations: list[dict]) -> str:
    """One-line TL;DR of a live exam session for the teacher's
    dashboard. The teacher needs to scan 50 students fast — this
    sentence is what they read instead of clicking each row open
    and parsing the raw violation log.

    ``session_meta``: {roll_number, full_name, exam_title,
                       elapsed_minutes, current_question}
    ``violations``: list of {timestamp, type, severity, details}
                    — typically the most recent ~30 events.

    Returns a string. On any failure (LLM down, bad JSON, empty
    input) returns ``""``. Caller treats empty as "skip the badge,
    don't show anything" so a Groq outage doesn't alarm-bell every
    row on the live tab.
    """
    if not is_configured():
        return ""

    # Compact violation digest — we don't send the raw event log
    # because it's noisy (heartbeats, frame analyses, etc.). Filter
    # to events with severity >= medium AND non-housekeeping types.
    HOUSEKEEPING = {
        "heartbeat", "exam_started", "exam_submitted", "answer_selected",
        "calibration_started", "calibration_complete", "id_verification",
        "id_verification_captured", "session_ended", "face_enrolled",
        "enrollment_started", "enrollment_complete",
    }
    notable = [v for v in (violations or [])
               if v.get("violation_type") not in HOUSEKEEPING][:30]

    if not notable:
        # No real signal to summarise — return the canonical "clean"
        # string without burning a Groq call.
        return "No concerning patterns."

    # Format each event as "[mm:ss into exam] type — details"
    # so the LLM can pick up timing patterns ("Q3-Q5 looking down").
    lines = []
    for v in notable:
        t = v.get("violation_type", "?")
        sev = (v.get("severity") or "low").upper()
        det = (v.get("details") or "")[:80]
        # Compute relative time if we have both a session start and
        # a violation timestamp; otherwise just include the raw ts.
        ts = v.get("created_at") or v.get("timestamp") or ""
        lines.append(f"[{sev}] {t} — {det}".strip())
    digest = "\n".join(lines)

    elapsed = session_meta.get("elapsed_minutes")
    elapsed_str = f"{int(elapsed)}m elapsed" if elapsed is not None else "elapsed unknown"
    user = f"""Session context:
- Student: {session_meta.get("full_name") or "?"} (roll {session_meta.get("roll_number") or "?"})
- Exam: {session_meta.get("exam_title") or "Exam"}
- Status: {elapsed_str}, on Q{session_meta.get("current_question") or "?"}
- Violation count: {len(notable)}

Recent events (most recent first):
{digest}

Now write the one-sentence triage summary."""

    try:
        parsed = _chat_json(_TRIAGE_SYSTEM, user, max_tokens=120, temperature=0.3)
        summary = str(parsed.get("summary") or "").strip()
        # Cap length — runaway models can blow past the 22-word limit
        # by ignoring the system prompt. 220 chars ≈ 35-40 words.
        return summary[:220]
    except Exception as e:
        log.warning("live_risk_triage failed: %s", e)
        return ""
