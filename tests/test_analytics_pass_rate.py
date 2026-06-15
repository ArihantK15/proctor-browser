"""Analytics pass-rate must honour each exam's configured pass mark.

Regression: the exam-analytics endpoint hardcoded `percentage >= 40`, so for an
exam with pass_mark=60 it over-reported the pass rate (a 45% counted as a pass
in analytics but FAIL on the scorecard). _count_passing keys the mark by
(teacher_id, exam_id) and defaults to 40, matching the scorecard / student
history / results list.
"""

from app.routers.admin_exams import _count_passing


def _sess(pct, exam_id="exam-1", teacher_id="teacher-1"):
    return {"percentage": pct, "exam_id": exam_id, "teacher_id": teacher_id}


def test_uses_configured_pass_mark_not_hardcoded_40():
    pass_marks = {("teacher-1", "exam-1"): 60}
    sessions = [_sess(50), _sess(60), _sess(70), _sess(45)]
    # Only 60 and 70 clear a 60 bar — NOT the 45/50 that a hardcoded 40 passed.
    assert _count_passing(sessions, pass_marks) == 2


def test_defaults_to_40_when_no_config():
    sessions = [_sess(39), _sess(40), _sess(41)]
    assert _count_passing(sessions, {}) == 2  # 40 and 41 (>= 40)


def test_per_exam_marks_keyed_by_teacher_and_exam():
    pass_marks = {
        ("teacher-1", "exam-1"): 60,   # strict
        ("teacher-2", "exam-1"): 40,   # same exam_id, different teacher → lenient
    }
    sessions = [
        _sess(50, "exam-1", "teacher-1"),  # 50 < 60 → fail
        _sess(50, "exam-1", "teacher-2"),  # 50 >= 40 → pass
        _sess(50, "exam-2", "teacher-1"),  # no config → default 40 → pass
    ]
    assert _count_passing(sessions, pass_marks) == 2


def test_missing_percentage_treated_as_zero():
    sessions = [{"exam_id": "exam-1", "teacher_id": "teacher-1"}]  # no percentage
    assert _count_passing(sessions, {}) == 0
