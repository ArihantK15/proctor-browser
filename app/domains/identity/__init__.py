"""Identity domain — authentication, account management."""
from ...routers.auth import router as auth_router  # noqa

__all__ = ["auth_router"]
