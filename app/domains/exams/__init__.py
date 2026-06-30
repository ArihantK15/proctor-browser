"""Domain: exams."""
from ...routers.admin_exams import router as admin_exams_router  # noqa
from ...routers.question_bank import router as question_bank_router  # noqa

__all__ = ["admin_exams_router", "question_bank_router"]
