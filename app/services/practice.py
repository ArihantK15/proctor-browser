from typing import Any, Optional

from ..constants import PRACTICE_PREFIX


def is_practice(identifier: str | None) -> bool:
    return bool(identifier) and str(identifier).startswith(PRACTICE_PREFIX)


PRACTICE_QUESTIONS: list[dict[str, Any]] = [
    {"id": 1, "question_id": 1,
     "question": "This is a practice exam to test your setup. Pick any answer to continue.",
     "question_type": "mcq_single",
     "options": {"A": "I can see this question and the camera light is on.", "B": "I cannot see the camera preview.", "C": "I am unsure.", "D": "Skip"},
     "correct": "A", "image_url": ""},
    {"id": 2, "question_id": 2,
     "question": "Try clicking outside the exam window. The system should warn you. Did the warning appear?",
     "question_type": "mcq_single",
     "options": {"A": "Yes — a warning banner appeared.", "B": "No — nothing happened.", "C": "I did not try this.", "D": "I am not sure."},
     "correct": "A", "image_url": ""},
    {"id": 3, "question_id": 3,
     "question": "When you submit this practice exam, your real answers will not be graded or saved. Ready to submit?",
     "question_type": "mcq_single",
     "options": {"A": "Yes — submit and finish the practice run.", "B": "Not yet, I want to review.", "C": "Skip submission.", "D": "Other."},
     "correct": "A", "image_url": ""},
]


def _practice_validate_response(roll_number: str) -> dict[str, Any]:
    return {"valid": True, "full_name": "Practice Student", "email": "", "phone": "",
            "roll_number": roll_number, "token": "", "practice": True}
