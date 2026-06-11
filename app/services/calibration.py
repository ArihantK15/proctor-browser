"""Calibration quality analysis.

Extracted from app/dependencies.py.
"""

import json
import logging
import re
from typing import Optional

from ..database import async_table as _atable
from ..constants import (
    _CAL_TIGHT_GAZE, _CAL_LOOSE_GAZE,
    _CAL_TIGHT_HEAD, _CAL_LOOSE_HEAD,
)

logger = logging.getLogger(__name__)

try:
    from .. import cache as _cache
except Exception:
    _cache = None


def parse_calibration_details(details: str) -> Optional[dict]:
    if not details:
        return None
    s = str(details).strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
            if isinstance(d, dict) and "gaze_yaw_range" in d:
                return {
                    "gaze_yaw_range":   float(d.get("gaze_yaw_range") or 0),
                    "gaze_pitch_range": float(d.get("gaze_pitch_range") or 0),
                    "head_yaw_range":   float(d.get("head_yaw_range") or 0),
                    "head_pitch_range": float(d.get("head_pitch_range") or 0),
                    "gaze_yaw":         float(d.get("gaze_yaw") or 0),
                    "gaze_pitch":       float(d.get("gaze_pitch") or 0),
                    "head_yaw":         float(d.get("head_yaw") or 0),
                    "head_pitch":       float(d.get("head_pitch") or 0),
                }
        except Exception:
            logger.debug("calibration: details parse failed", exc_info=True)

    # Proctor format: "gaze yaw:+0.12rad pitch:-0.05rad | head yaw:+3.0° pitch:+2.0°"
    m_proctor = re.search(
        r"gaze yaw:([+-][\d.]+)rad pitch:([+-][\d.]+)rad \| head yaw:([+-][\d.]+)° pitch:([+-][\d.]+)°",
        s,
    )
    if m_proctor:
        gy, gp, hy, hp = (
            float(m_proctor.group(1)),
            float(m_proctor.group(2)),
            float(m_proctor.group(3)),
            float(m_proctor.group(4)),
        )
        return {
            "gaze_yaw_range":   abs(gy),
            "gaze_pitch_range": abs(gp),
            "head_yaw_range":   abs(hy),
            "head_pitch_range": abs(hp),
            "gaze_yaw":         gy,
            "gaze_pitch":       gp,
            "head_yaw":         hy,
            "head_pitch":       hp,
        }

    # Legacy details format (pre-2025 proctor versions)
    m_g = re.search(r"range\s+gaze:\s*±\(([\d.\-]+)\s*,\s*([\d.\-]+)\)", s)
    m_h = re.search(r"head:\s*±\(([\d.\-]+)°?\s*,\s*([\d.\-]+)°?\)", s)
    m_b = re.search(r"bias\s+gaze:\(([\d.\-]+)\s*,\s*([\d.\-]+)\)", s)
    if not (m_g and m_h):
        return None
    out = {
        "gaze_yaw_range": float(m_g.group(1)),
        "gaze_pitch_range": float(m_g.group(2)),
        "head_yaw_range": float(m_h.group(1)),
        "head_pitch_range": float(m_h.group(2)),
    }
    if m_b:
        out["gaze_yaw"] = float(m_b.group(1))
        out["gaze_pitch"] = float(m_b.group(2))
    out.setdefault("gaze_yaw", 0.0)
    out.setdefault("gaze_pitch", 0.0)
    out.setdefault("head_yaw", 0.0)
    out.setdefault("head_pitch", 0.0)
    return out


def classify_calibration(parsed: Optional[dict]) -> dict:
    if not parsed:
        return {"tier": "missing", "reason": "No calibration recorded.", "ranges": None}
    g_yaw, g_pitch = parsed["gaze_yaw_range"], parsed["gaze_pitch_range"]
    h_yaw, h_pitch = parsed["head_yaw_range"], parsed["head_pitch_range"]
    if min(g_yaw, g_pitch) < _CAL_TIGHT_GAZE or min(h_yaw, h_pitch) < _CAL_TIGHT_HEAD:
        return {
            "tier": "tight",
            "reason": f"Narrow range — student barely moved (gaze yaw ±{g_yaw:.2f} rad, head yaw ±{h_yaw:.0f}°).",
            "ranges": parsed,
        }
    if max(g_yaw, g_pitch) > _CAL_LOOSE_GAZE or max(h_yaw, h_pitch) > _CAL_LOOSE_HEAD:
        return {
            "tier": "loose",
            "reason": f"Wide range — student moved more than expected (gaze yaw ±{g_yaw:.2f} rad, head yaw ±{h_yaw:.0f}°).",
            "ranges": parsed,
        }
    return {"tier": "normal", "reason": "Calibration within typical envelope.", "ranges": parsed}


async def get_calibration_quality(session_id: str, teacher_id: Optional[str] = None) -> dict:
    cache_key = f"cal_quality:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
    q = (_atable("violations").select("details").eq("session_key", session_id)
         .eq("violation_type", "calibration_complete").order("id", desc=True).limit(1))
    if teacher_id:
        q = q.eq("teacher_id", str(teacher_id))
    rows = (await q.execute()).data or []
    parsed = parse_calibration_details(rows[0].get("details")) if rows else None
    out = classify_calibration(parsed)
    if _cache:
        try:
            _cache.set(cache_key, out, ttl=300)
        except Exception:
            logger.debug("calibration: cache set failed", exc_info=True)
    return out
