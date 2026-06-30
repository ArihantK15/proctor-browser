"""Domain: compliance — privacy, consent, appeals."""
from ...routers.privacy import router as privacy_router  # noqa
from ...routers.appeals import router as appeals_router  # noqa

__all__ = ["privacy_router", "appeals_router"]
