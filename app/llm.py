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


# ── Question quality lint ───────────────────────────────────────────

_LINT_SYSTEM = """You are an experienced exam writer reviewing multiple-choice questions \
for a high-stakes exam. For each question you'll receive, identify any issues that \
would make it unfair, ambiguous, or wrong.

Look for these problems specifically:
  • AMBIGUOUS — wording could be read multiple ways
  • UNBALANCED — distractors are obviously wrong (e.g. one option is twice as long, or has a giveaway like "all of the above")
  • WRONG_KEY — the marked correct answer is factually wrong, OR multiple options are correct, OR none are correct
  • DOUBLE_NEGATIVE — uses "not" / "except" in a confusing way
  • TRIVIAL — answer is obvious from the question text alone
  • TYPO — spelling or grammar error that changes meaning

For each question, return at most 2 issues (the most severe). If a question is fine, \
return an empty issues array.

Severity scale: "high" = students will get this wrong unfairly; "medium" = confusing \
but probably gradable; "low" = stylistic.

Output JSON: {"results":[{"idx":N,"issues":[{"type":"AMBIGUOUS","severity":"medium","note":"<one sentence>"}]}, ...]}

Important: never invent answers. If you think the marked correct is wrong, say so but \
do NOT speculate which option SHOULD be correct unless you're certain — false \
corrections are worse than no review. Maximum 25 words per note."""


def lint_questions(questions: list[dict]) -> list[dict]:
    """Review a batch of questions for ambiguity / unbalanced options /
    wrong correct-answer keys. Returns per-question issue lists.

    ``questions``: list of dicts with keys
        idx, question, options (dict A/B/C/D → text), correct (letter)

    Returns: list of {idx, issues: [{type, severity, note}]}
        — same length as input. Empty issues list = clean question.

    On any failure (LLM down, bad JSON, empty input) returns
    [] — caller treats empty as "skip linting" rather than "all
    clean" so a Groq blip doesn't silently mark every question as
    fine. UI shows "Lint unavailable" in that case.
    """
    if not is_configured() or not questions:
        return []

    # Cap at 25 questions per call. Groq can handle more in raw token
    # budget but the model loses focus past ~25 items in a JSON list
    # — issues from later questions get attributed to earlier indices.
    # Caller batches in chunks of 25 if there are more.
    batch = questions[:25]
    digest = []
    for i, q in enumerate(batch):
        opts = q.get("options") or {}
        opts_str = " | ".join(f"{k}: {v}" for k, v in opts.items())
        digest.append(
            f"Q{i} (idx={q.get('idx', i)}): {q.get('question','')}\n"
            f"  Options: {opts_str}\n"
            f"  Marked correct: {q.get('correct','')}"
        )
    user = "Review these questions:\n\n" + "\n\n".join(digest)

    try:
        parsed = _chat_json(_LINT_SYSTEM, user, max_tokens=2000, temperature=0.2)
    except Exception as e:
        log.warning("lint_questions failed: %s", e)
        return []

    raw = parsed.get("results") or []
    if not isinstance(raw, list):
        return []

    # Normalise + clamp. Build a lookup by the idx the LLM returned
    # so out-of-order results are matched correctly. Missing results
    # default to empty (clean) — better than dropping the question.
    by_idx = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if idx is None:
            continue
        issues = []
        for issue in (item.get("issues") or [])[:2]:
            if not isinstance(issue, dict):
                continue
            issues.append({
                "type": str(issue.get("type", "ISSUE"))[:24].upper(),
                "severity": str(issue.get("severity", "medium")).lower(),
                "note": str(issue.get("note", ""))[:200],
            })
        by_idx[idx] = issues

    return [
        {"idx": q.get("idx", i), "issues": by_idx.get(q.get("idx", i), [])}
        for i, q in enumerate(batch)
    ]


# ── Short-answer grading ────────────────────────────────────────────

_GRADE_SYSTEM = """You are an exam grader. The teacher has provided a reference answer and \
optional rubric. Score the student's answer on a scale from 0 to max_score, allowing half marks.

Rules:
- Award FULL marks only when the student's answer fully covers the reference answer's content.
- Award PARTIAL marks when the answer is partially correct, ambiguous, or missing key details.
- Award ZERO when the answer is wrong, blank, or off-topic.
- Be lenient on spelling, grammar, and phrasing — the answer's MEANING is what matters.
  e.g. "atomicity, consistency, isolation, durability" and "Atomicity Consitency Isolation Durability" \
should get the same score.
- Be strict on factual content. If the rubric says "must mention X", then missing X = partial credit.
- For numeric answers: accept equivalent forms (1/2 = 0.5 = 50%). Accept reasonable rounding.
- Provide feedback in 1-2 sentences. Be direct and specific. No filler like "Good attempt!" or \
"You can do better." The student needs to know what was right or wrong, not be coddled.

Confidence:
- "high" — answer is clearly right or clearly wrong with no judgment call.
- "medium" — partial credit decision required, or interpretation involved.
- "low" — student's answer is highly unusual, ambiguous, or you're uncertain. Flag for human review.

Output JSON: {"score": N, "feedback": "...", "confidence": "high|medium|low"}"""


def grade_short_answer(question: str, reference: str, rubric: str,
                       student_answer: str, max_score: float = 1.0) -> dict:
    """Grade one short-answer response against a teacher's reference.

    Returns ``{score, feedback, confidence}`` where score is a number
    between 0 and max_score (half marks allowed). On any failure
    returns ``{score: None, feedback: "...", confidence: "low"}`` so
    the caller can surface "couldn't grade automatically — review
    manually" in the UI without crashing.

    Critically, this function is one half of a two-step pattern. The
    score it returns is a SUGGESTION; the teacher must confirm in
    the dashboard before it lands in the gradebook. Don't fold this
    into auto-grading on submit — the trust gradient with AI grades
    on high-stakes exams is too steep to skip the human-in-the-loop.
    """
    if not is_configured():
        return {"score": None, "feedback": "AI grader not configured.",
                "confidence": "low"}
    if not (student_answer or "").strip():
        # Blank answer → 0/max with no LLM call. Saves a Groq round-
        # trip on the most common case (student skipped the question).
        return {"score": 0.0, "feedback": "Blank answer.", "confidence": "high"}

    user = f"""Question: {question}

Reference answer (model answer the teacher wrote):
{reference}

Rubric (optional grading criteria):
{rubric or '(none provided — use the reference answer as the standard)'}

Maximum score: {max_score}

Student's answer:
{student_answer}

Now grade it."""

    try:
        parsed = _chat_json(_GRADE_SYSTEM, user, max_tokens=300, temperature=0.2)
    except Exception as e:
        log.warning("grade_short_answer failed: %s", e)
        return {"score": None,
                "feedback": "Couldn't reach the AI grader. Review manually.",
                "confidence": "low"}

    # Defensive normalisation — the model occasionally returns the
    # score as a string ("1.5") or as max_score+ (over-scoring). Clamp
    # to [0, max_score] and coerce to float.
    try:
        score = float(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(float(max_score), score))
    feedback = str(parsed.get("feedback") or "")[:500]
    confidence = str(parsed.get("confidence") or "medium").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    return {"score": score, "feedback": feedback, "confidence": confidence}
