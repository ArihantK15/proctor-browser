"""Domain: lti."""
from ...routers.lti import router as lti_router  # noqa
from ...routers.google_classroom import router as google_classroom_router  # noqa

__all__ = ["lti_router", "google_classroom_router"]
