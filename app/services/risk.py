"""Risk scoring, violation classification, and narrative summary.

Extracted from app/dependencies.py to reduce the god module.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

from ..logger import get_logger
from ..utils import fmt_ist, now_ist
from ..constants import (
    _SATURATION_K, _BASELINE_DURATION_MINS,
    _DEFAULT_WEIGHT_HIGH, _DEFAULT_WEIGHT_MED, _CRITICAL_TYPES,
)

logger = logging.getLogger(__name__)

try:
    from ..event_bus import async_publish as _bus_async_publish
    _HAS_REDIS = True
except Exception:
    _HAS_REDIS = False
    async def _bus_async_publish(*_a, **_kw): pass

try:
    from .. import cache as _cache
except Exception:
    _cache = None

from ..database import async_table as _atable


# ─── CONSTANTS ────────────────────────────────────────────────────

VIOLATION_WEIGHTS: dict[str, float] = {
    "wrong_person": 30, "multiple_faces": 20, "face_missing": 15,
    "cheat_object_detected": 25, "window_focus_lost": 18, "tab_hidden": 15,
    "shortcut_blocked": 12, "gaze_away": 8, "head_turned": 8, "eyes_closed": 5,
    "voice_detected": 10, "time_exceeded": 15, "vm_detected": 20,
    "remote_desktop_detected": 22, "screen_share_detected": 12, "multiple_monitors": 8,
    "calibration_abort": 35,
    "phone_consulting": 32, "collaboration": 30, "answer_memo": 28,
    "note_reading": 25, "sustained_offtask": 15, "nervous_evasion": 12,
}

_SEVERITY_MULTIPLIER = {"high": 1.0, "medium": 0.4, "low": 0.1}
RISK_LABELS = [(15, "Low Risk"), (40, "Moderate Risk"), (70, "High Risk"), (100, "Critical Risk")]

_NON_VIOLATION_TYPES = {
    "exam_submitted", "enrollment_started", "enrollment_complete",
    "exam_started", "submit_failed", "answer_selected", "session_ended",
    "face_enrolled", "heartbeat", "id_verification", "id_verification_captured",
    "calibration_started", "calibration_complete", "calibration_timeout",
}

BLOCKING_TYPES = {"vm_detected", "remote_desktop_detected", "vpn_detected", "proxy_detected", "debugger_detected"}

_BEHAVIORAL_PATTERNS: dict[str, str] = {
    "phone_consulting": "consulting a phone",
    "collaboration": "communicating with another person",
    "answer_memo": "writing down answers to look up later",
    "note_reading": "reading from prepared notes",
    "sustained_offtask": "sustained off-task behavior",
    "nervous_evasion": "nervous gaze evasion",
}

_CRITICAL_VIOLATIONS: dict[str, str] = {
    "wrong_person": "a different person appeared on camera",
    "calibration_abort": "the calibration was aborted (possible person switch)",
    "cheat_object_detected": "a cheat object was detected",
    "vm_detected": "a virtual machine was detected",
    "remote_desktop_detected": "remote desktop access was detected",
}


# ─── HELPERS ───────────────────────────────────────────────────────

def _is_violation(vtype: str) -> bool:
    return vtype not in _NON_VIOLATION_TYPES


def _risk_label(score: int) -> str:
    for threshold, label in RISK_LABELS:
        if score <= threshold:
            return label
    return "Critical Risk"


def _normalise_answer_set(ans: str) -> set[str]:
    if ans is None:
        return set()
    return {s.strip().upper() for s in str(ans).split(",") if s.strip()}


def _answers_match(student_ans: str, correct_ans: str) -> bool:
    return _normalise_answer_set(student_ans) == _normalise_answer_set(correct_ans)


# ─── ALERTS ────────────────────────────────────────────────────────

async def publish_critical_alert(
    teacher_id: str,
    session_id: str,
    violation_type: str,
    severity: str,
    details: str = "",
    roll_number: str = "",
    full_name: str = "",
):
    if not _HAS_REDIS:
        return
    if violation_type not in _CRITICAL_TYPES and severity not in ("critical", "high"):
        return
    weight = VIOLATION_WEIGHTS.get(violation_type, 0)
    alert = {
        "session_id": session_id,
        "violation_type": violation_type,
        "severity": severity,
        "details": details,
        "roll_number": roll_number,
        "full_name": full_name,
        "risk_weight": weight,
        "timestamp": fmt_ist(now_ist().isoformat()),
    }
    await _bus_async_publish(f"alerts:{teacher_id}", alert)


# ─── RISK SCORE ────────────────────────────────────────────────────

async def compute_risk_score(session_id: str, teacher_id: str | None = None) -> dict:
    cache_key = f"risk_score:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached:
            return cached
    query = _atable("violations").select("violation_type,severity,created_at").eq("session_key", session_id)
    if teacher_id:
        query = query.eq("teacher_id", str(teacher_id))
    viol_result = await query.order("created_at").execute()
    rows = viol_result.data or []
    scored = [r for r in rows if _is_violation(r["violation_type"]) and r["severity"] in ("high", "medium")]
    if not scored:
        return {"risk_score": 0, "label": "Low Risk", "duration_minutes": 0, "breakdown": {}}

    def _parse_ts(ts_str):
        try:
            return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    timestamps = [_parse_ts(r["created_at"]) for r in rows if r.get("created_at")]
    timestamps = [t for t in timestamps if t > 0]
    duration_mins = (max(timestamps) - min(timestamps)) / 60.0 if len(timestamps) >= 2 else 1.0

    counts: dict[tuple[str, str], int] = {}
    for r in scored:
        key = (r["violation_type"], r["severity"])
        counts[key] = counts.get(key, 0) + 1

    breakdown: dict[str, dict] = {}
    raw_sum = 0.0
    log_sat = math.log(1 + _SATURATION_K)
    for (vtype, sev), n in counts.items():
        weight = VIOLATION_WEIGHTS.get(vtype)
        if weight is None:
            weight = (_DEFAULT_WEIGHT_HIGH if sev == "high" else _DEFAULT_WEIGHT_MED)
        sev_mult = _SEVERITY_MULTIPLIER.get(sev, 0.4)
        contribution = weight * sev_mult * min(1.0, math.log(1 + n) / log_sat)
        raw_sum += contribution
        if vtype not in breakdown:
            breakdown[vtype] = {"count": 0, "contribution": 0.0}
        breakdown[vtype]["count"] += n
        breakdown[vtype]["contribution"] = round(breakdown[vtype]["contribution"] + contribution, 1)

    duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
    normalized = raw_sum * duration_factor
    risk_score = min(100, round(normalized))
    result = {
        "risk_score": risk_score,
        "label": _risk_label(risk_score),
        "duration_minutes": round(duration_mins, 1),
        "breakdown": breakdown,
    }
    if _cache:
        _cache.set(cache_key, result, ttl=30)
    return result


# ─── BATCH RISK (for _build_sessions_payload) ──────────────────────

def _batch_risk_scores(viol_by_session: dict[str, list[dict]]) -> dict[str, tuple[int | None, str | None]]:
    scores: dict[str, tuple[int | None, str | None]] = {}
    for sk, rows in viol_by_session.items():
        scored = [r for r in rows if _is_violation(r["violation_type"]) and r["severity"] in ("high", "medium")]
        if not scored:
            scores[sk] = (0, "Low Risk")
            continue
        try:
            timestamps = []
            for r in rows:
                ts = r.get("created_at")
                if ts:
                    try:
                        timestamps.append(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
                    except Exception:
                        logger.debug("risk: timestamp parse failed", exc_info=True)
            timestamps = [t for t in timestamps if t > 0]
            duration_mins = (max(timestamps) - min(timestamps)) / 60.0 if len(timestamps) >= 2 else 1.0
        except Exception:
            duration_mins = 1.0

        counts: dict[tuple[str, str], int] = {}
        for r in scored:
            key = (r["violation_type"], r["severity"])
            counts[key] = counts.get(key, 0) + 1

        raw_sum = 0.0
        log_sat = math.log(1 + _SATURATION_K)
        for (vtype, sev), n in counts.items():
            weight = VIOLATION_WEIGHTS.get(vtype)
            if weight is None:
                weight = (_DEFAULT_WEIGHT_HIGH if sev == "high" else _DEFAULT_WEIGHT_MED)
            sev_mult = _SEVERITY_MULTIPLIER.get(sev, 0.4)
            raw_sum += weight * sev_mult * min(1.0, math.log(1 + n) / log_sat)

        duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
        risk_score = min(100, round(raw_sum * duration_factor))
        scores[sk] = (risk_score, _risk_label(risk_score))
    return scores


# ─── NARRATIVE SUMMARY ─────────────────────────────────────────────

def generate_session_summary(violations: list[dict], session_info: dict | None = None) -> dict:
    if not violations:
        return {
            "narrative": "No suspicious activity detected during this session.",
            "highlights": [],
            "severity": "clean",
            "pattern_count": 0,
        }

    real_viols = [v for v in violations if _is_violation(v.get("violation_type", ""))]
    if not real_viols:
        return {
            "narrative": "No suspicious activity detected during this session.",
            "highlights": [],
            "severity": "clean",
            "pattern_count": 0,
        }

    behavioral: list[dict] = []
    critical: list[dict] = []
    standard: list[dict] = []
    for v in real_viols:
        vt = v.get("violation_type", "")
        if vt in _BEHAVIORAL_PATTERNS:
            behavioral.append(v)
        elif vt in _CRITICAL_VIOLATIONS:
            critical.append(v)
        else:
            standard.append(v)

    std_counts: dict[str, int] = {}
    for v in standard:
        vt = v.get("violation_type", "unknown")
        std_counts[vt] = std_counts.get(vt, 0) + 1

    highlights: list[str] = []
    for v in behavioral:
        vt = v.get("violation_type", "")
        detail = v.get("details", "")
        desc = _BEHAVIORAL_PATTERNS.get(vt, vt)
        label = f"Behavioral pattern: {desc}"
        if detail:
            if detail.startswith("[Behavioral] "):
                detail = detail[13:]
            label += f" — {detail}"
        highlights.append(label)

    for v in critical:
        vt = v.get("violation_type", "")
        desc = _CRITICAL_VIOLATIONS.get(vt, vt)
        highlights.append(f"Critical: {desc}")

    for vt, count in sorted(std_counts.items(), key=lambda x: -x[1]):
        label = vt.replace("_", " ")
        if count == 1:
            highlights.append(f"{label.capitalize()} (1 occurrence)")
        else:
            highlights.append(f"{label.capitalize()} ({count} occurrences)")

    paragraphs: list[str] = []
    name = (session_info or {}).get("full_name", "The student")
    roll = (session_info or {}).get("roll_number", "")
    intro_roll = f" ({roll})" if roll else ""

    if critical:
        crit_types = {_CRITICAL_VIOLATIONS.get(v["violation_type"], v["violation_type"]) for v in critical}
        paragraphs.append(
            f"{name}{intro_roll} triggered {len(critical)} critical violation(s) during this session. "
            f"The following were detected: {'; '.join(sorted(crit_types))}."
        )

    if behavioral:
        pattern_types = {_BEHAVIORAL_PATTERNS.get(v["violation_type"], v["violation_type"]) for v in behavioral}
        paragraphs.append(
            f"The behavioral analysis engine detected {len(behavioral)} suspicious pattern(s): "
            f"{'; '.join(sorted(pattern_types))}. "
            f"These patterns suggest coordinated cheating behavior that goes beyond isolated lapses."
        )

    if std_counts:
        total_std = sum(std_counts.values())
        types_list = [f"{vt.replace('_', ' ')} ({n})" for vt, n in sorted(std_counts.items(), key=lambda x: -x[1])]
        paragraphs.append(
            f"Additionally, {total_std} standard violation(s) were recorded: "
            f"{', '.join(types_list)}."
        )

    risk = (session_info or {}).get("risk_score")
    if risk is not None:
        label = _risk_label(risk)
        if risk > 40:
            paragraphs.append(
                f"Overall risk score: {risk}/100 ({label}). "
                f"This session warrants careful review before accepting the result."
            )
        elif risk > 15:
            paragraphs.append(
                f"Overall risk score: {risk}/100 ({label}). "
                f"Some irregularities were noted but may be within normal variation."
            )
        else:
            paragraphs.append(
                f"Overall risk score: {risk}/100 ({label}). "
                f"Minor issues detected; likely not significant."
            )

    if critical:
        severity = "critical"
    elif behavioral and len(behavioral) > 2:
        severity = "critical"
    elif behavioral:
        severity = "concerning"
    elif any(c > 5 for c in std_counts.values()):
        severity = "concerning"
    elif std_counts:
        severity = "minor"
    else:
        severity = "clean"

    narrative = "\n\n".join(paragraphs) if paragraphs else "No suspicious activity detected."
    return {
        "narrative": narrative,
        "highlights": highlights[:20],
        "severity": severity,
        "pattern_count": len({v.get("violation_type") for v in behavioral + critical}),
    }
