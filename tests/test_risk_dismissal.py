"""Risk-SCORE dismissal: a dismissed flag must stop contributing to the score.

phase94's remediation hook dismisses a disputed flag (sets dismissed_at) and
expects the session risk score to actually drop. The appeals-endpoint tests
MOCK compute_risk_score, so this file is the only real coverage that dismissal
moves the number. _batch_risk_scores is the live-dashboard scorer (fed rows that
now select dismissed_at); it filters dismissed flags in memory. These lock that
exclusion so a regression dropping the `not r.get('dismissed_at')` clause fails.

Empirically: one active high flag scores 70; dismissed → 0; two high flags
score 93, dismissing one → 70.
"""
from app.services.risk import _batch_risk_scores


def _flag(vt="wrong_person", sev="high", at="2025-01-01T09:00:00Z", dismissed=False):
    r = {"violation_type": vt, "severity": sev, "created_at": at}
    if dismissed:
        r["dismissed_at"] = "2025-01-01T10:00:00Z"
    return r


def test_active_high_flag_scores():
    score, label = _batch_risk_scores({"s": [_flag()]})["s"]
    assert score > 0 and label != "Low Risk"


def test_dismissed_flag_scores_zero():
    # The whole point of due process: dismissing the only flag clears the score.
    assert _batch_risk_scores({"s": [_flag(dismissed=True)]})["s"] == (0, "Low Risk")


def test_dismissing_one_of_two_lowers_score():
    two = _batch_risk_scores(
        {"s": [_flag(), _flag(vt="phone_detected", at="2025-01-01T09:05:00Z")]})["s"][0]
    one = _batch_risk_scores(
        {"s": [_flag(), _flag(vt="phone_detected", at="2025-01-01T09:05:00Z", dismissed=True)]})["s"][0]
    assert two > one, f"dismissing a flag must lower the score ({two} -> {one})"
