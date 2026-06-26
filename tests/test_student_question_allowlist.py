"""Security regression: the student exam client (GET /api/v1/questions) must only
ever receive a tight allowlist of question fields. load_questions intentionally
returns authoring/grading-only fields (correct, reference_answer, max_score, rubric)
so the admin authoring screen can round-trip them — the exam delivery path strips
everything except `_STUDENT_Q_KEYS`. If someone widens that allowlist to include a
secret field, students would see the answer key mid-exam. This test fails loudly first.
"""
from app.routers.exam import _STUDENT_Q_KEYS

# Fields load_questions now returns that are authoring/grading-only.
_SECRET_FIELDS = {"correct", "reference_answer", "max_score", "rubric", "teacher_id"}


def test_allowlist_excludes_every_secret_field():
    leaked = _SECRET_FIELDS.intersection(_STUDENT_Q_KEYS)
    assert not leaked, f"student question allowlist leaks secret field(s): {leaked}"


def test_allowlist_is_exactly_the_student_safe_fields():
    # Locks the contract — a deliberate widening must update this test on purpose.
    assert set(_STUDENT_Q_KEYS) == {"id", "question", "options", "question_type", "image_url"}


def test_allowlist_applied_to_a_loaded_question_drops_secrets():
    # Mirrors the comprehension in get_questions against a load_questions-shaped row.
    loaded = {
        "id": "1", "question": "2+2?", "options": {"A": "4", "B": "5"},
        "correct": "A", "question_type": "mcq_single", "image_url": "",
        "reference_answer": "the answer is 4", "max_score": 5, "rubric": "be strict",
    }
    safe = {k: loaded[k] for k in _STUDENT_Q_KEYS if k in loaded}
    assert not _SECRET_FIELDS.intersection(safe), f"leaked: {_SECRET_FIELDS.intersection(safe)}"
    assert set(safe) == {"id", "question", "options", "question_type", "image_url"}
