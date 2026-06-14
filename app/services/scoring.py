"""Answer scoring, shuffle helpers, and student answer translation.

Extracted from app/dependencies.py.
"""

from ..log_safe import safe
import asyncio
import hashlib
import logging
import random

from ..repositories.questions import load_questions, load_exam_config

logger = logging.getLogger(__name__)


# ─── SHUFFLE HELPERS ──────────────────────────────────────────────

def shuffle_seed(session_id: str, teacher_id: str) -> int:
    basis = f"{teacher_id or ''}::{session_id or ''}"
    return int(hashlib.sha256(basis.encode()).hexdigest(), 16) % (2**32)


def build_shuffle_view(questions: list[dict], session_id: str, teacher_id: str, *, shuffle_q: bool, shuffle_o: bool) -> tuple[list[dict], dict[str, dict[str, str]]]:
    rng = random.Random(shuffle_seed(session_id, teacher_id))
    q_iter = list(questions)
    if shuffle_q:
        rng.shuffle(q_iter)
    student_qs: list[dict] = []
    label_maps: dict[str, dict[str, str]] = {}
    for q in q_iter:
        qid = str(q.get("id"))
        opts = q.get("options", {}) or {}
        orig_keys = list(opts.keys())
        qtype = str(q.get("question_type") or "mcq_single").lower()
        tf_keys = set(orig_keys) == {"True", "False"}
        can_shuffle_opts = shuffle_o and len(orig_keys) > 1 and qtype != "true_false" and not tf_keys
        if can_shuffle_opts:
            perm = list(orig_keys)
            rng.shuffle(perm)
            new_opts = {orig_keys[i]: opts[perm[i]] for i in range(len(orig_keys))}
            label_maps[qid] = {orig_keys[i]: perm[i] for i in range(len(orig_keys))}
            q = {**q, "options": new_opts}
        else:
            label_maps[qid] = {k: k for k in orig_keys}
        student_qs.append(q)
    return student_qs, label_maps


def get_shuffle_flags(config: dict) -> tuple[bool, bool]:
    sq = config.get("shuffle_questions")
    so = config.get("shuffle_options")
    if sq is None:
        sq = True
    if so is None:
        so = True
    return bool(sq), bool(so)


# ─── ANSWER/SCORING HELPERS ────────────────────────────────────────

def normalise_answer_set(ans: str) -> set[str]:
    if ans is None:
        return set()
    return {s.strip().upper() for s in str(ans).split(",") if s.strip()}


def _numeric_in_range(student_ans: str, correct_ans: str) -> bool:
    """Grade a numeric answer against a `range:MIN:MAX` correct value.

    Inclusive band, decimals supported. MIN == MAX is an exact value. Bounds
    entered in the wrong order are tolerated. Any non-numeric input → wrong
    (never raises)."""
    try:
        parts = str(correct_ans).split(":")
        lo = float(parts[1])
        hi = float(parts[2])
    except (ValueError, IndexError, TypeError):
        return False
    if lo > hi:
        lo, hi = hi, lo
    try:
        x = float(str(student_ans).strip())
    except (ValueError, TypeError):
        return False
    return lo <= x <= hi


def answers_match(student_ans: str, correct_ans: str) -> bool:
    # Numeric-range questions encode their tolerance band in `correct` as
    # "range:MIN:MAX" (students never see `correct`). Everything else keeps
    # the legacy case-insensitive string-set equality.
    if str(correct_ans or "").strip().lower().startswith("range:"):
        return _numeric_in_range(student_ans, correct_ans)
    return normalise_answer_set(student_ans) == normalise_answer_set(correct_ans)


async def translate_student_answer(session_id: str, teacher_id: str, question_id: str, student_label: str, exam_id: str = None) -> str:
    try:
        if not student_label:
            return student_label
        config = await load_exam_config(teacher_id, exam_id=exam_id)
        shuffle_q, shuffle_o = get_shuffle_flags(config)
        if not shuffle_o:
            return student_label
        questions = await load_questions(teacher_id, exam_id=exam_id)
        if not questions:
            return student_label
        _, label_maps = build_shuffle_view(questions, session_id, teacher_id, shuffle_q=shuffle_q, shuffle_o=shuffle_o)
        qmap = label_maps.get(str(question_id))
        if not qmap:
            return student_label
        return qmap.get(str(student_label), student_label)
    except Exception as e:
        logger.debug("[Shuffle] translate failed q=%s s=%s: %s", safe(question_id), safe(student_label), safe(e))
        return student_label


async def canonicalise_student_answer(session_id: str, teacher_id: str, question_id: str, raw: str, exam_id: str = None) -> str:
    if not str(raw or ""):
        return ""
    try:
        qs = await load_questions(teacher_id, exam_id=exam_id) or []
        qmeta = next((q for q in qs if str(q.get("id")) == str(question_id)), None)
        if qmeta and str(qmeta.get("question_type") or "").lower() == "short_answer":
            return str(raw)
    except Exception:
        logger.debug("scoring: question-type lookup failed", exc_info=True)
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    translated = [await translate_student_answer(session_id, str(teacher_id or ""), str(question_id), p, exam_id=exam_id) for p in parts]
    return ",".join(sorted(translated))


async def recalculate_score(session_id: str, payload_answers: dict, teacher_id: str = None, exam_id: str = None) -> tuple[int, int]:
    from ..database import async_table as _atable
    last_err = None
    for attempt in range(2):
        try:
            questions = await load_questions(teacher_id, exam_id=exam_id)
            auto_qs = [q for q in questions if str(q.get("question_type") or "mcq_single").lower() != "short_answer"]
            total = len(auto_qs)
            saved = await _atable("answers").select("question_id,answer").eq("session_key", session_id).execute()
            ans_map = {str(r["question_id"]): str(r["answer"]) for r in (saved.data or [])}
            for qid, ans in (payload_answers or {}).items():
                ans_map[str(qid)] = await canonicalise_student_answer(session_id, str(teacher_id or ""), str(qid), str(ans), exam_id=exam_id)
            score = sum(1 for q in auto_qs if answers_match(ans_map.get(str(q["id"]), ""), str(q["correct"])))
            return score, total
        except Exception as e:
            last_err = e
            logger.warning("[Score] Recalculation attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(0.3)
    raise RuntimeError(f"Score recalculation failed after 2 attempts: {last_err}")
