import pytest


def test_corroboration_flag_true_when_paste_attempt_present():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 2, "keystroke_rhythm_variance": 5.0}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    assert _is_corroborated(sub_a, sub_b) is True


def test_corroboration_flag_true_when_variance_anomalously_low():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 0, "keystroke_rhythm_variance": 0.01}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    assert _is_corroborated(sub_a, sub_b) is True


def test_corroboration_flag_false_when_no_signal():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 4.5}
    assert _is_corroborated(sub_a, sub_b) is False


def test_corroboration_flag_handles_missing_telemetry():
    from app.jobs.plagiarism_jobs import _is_corroborated
    assert _is_corroborated({}, {}) is False


def test_group_submissions_by_question_and_language():
    from app.jobs.plagiarism_jobs import _group_submissions
    subs = [
        {"id": "1", "question_id": "q1", "language": "python"},
        {"id": "2", "question_id": "q1", "language": "python"},
        {"id": "3", "question_id": "q1", "language": "java"},
        {"id": "4", "question_id": "q2", "language": "python"},
    ]
    groups = _group_submissions(subs)
    assert groups[("q1", "python")] == subs[0:2]
    assert groups[("q1", "java")] == [subs[2]]
    assert groups[("q2", "python")] == [subs[3]]
