"""Per-student exam time extension (accommodations)."""

from ..database import async_table as _atable


async def get_time_extension(teacher_id: str, exam_id: str, roll_number: str) -> int:
    """Extra minutes for (teacher, exam, roll). 0 if none. Fails OPEN to 0
    (a lookup error must never block a student from starting/submitting)."""
    try:
        rows = (await _atable("exam_time_extensions")
                .select("extra_minutes")
                .eq("teacher_id", teacher_id)
                .eq("exam_id", exam_id)
                .eq("roll_number", roll_number)
                .limit(1)
                .execute()).data or []
        return int(rows[0]["extra_minutes"]) if rows else 0
    except Exception:
        return 0
