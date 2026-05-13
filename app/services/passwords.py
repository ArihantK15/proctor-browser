"""Password complexity rules and HIBP top-1000 breach check."""
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 10
REQUIRE_UPPER = True
REQUIRE_LOWER = True
REQUIRE_DIGIT = True
REQUIRE_SYMBOL = True

# Path to bundled top-1000 breached passwords list
_BREACHED_FILE = Path(__file__).parent.parent.parent / "data" / "breached_top1000.txt"


def _load_breached_set() -> set[str]:
    """Load the top-1000 breached passwords into a set."""
    if not _BREACHED_FILE.exists():
        logger.warning("[passwords] breached_top1000.txt not found at %s", _BREACHED_FILE)
        return set()
    try:
        with open(_BREACHED_FILE) as f:
            return {line.strip().lower() for line in f if line.strip()}
    except Exception as e:
        logger.warning("[passwords] failed to load breached list: %s", e)
        return set()


_BREACHED: set[str] | None = None


def _get_breached() -> set[str]:
    global _BREACHED
    if _BREACHED is None:
        _BREACHED = _load_breached_set()
    return _BREACHED


class PasswordError(ValueError):
    pass


def validate_password(password: str) -> None:
    """Check password against complexity rules and breached list.
    Raises PasswordError with a user-facing message on failure."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if REQUIRE_UPPER and not any(c.isupper() for c in password):
        raise PasswordError("Password must contain at least one uppercase letter.")
    if REQUIRE_LOWER and not any(c.islower() for c in password):
        raise PasswordError("Password must contain at least one lowercase letter.")
    if REQUIRE_DIGIT and not any(c.isdigit() for c in password):
        raise PasswordError("Password must contain at least one digit.")
    if REQUIRE_SYMBOL and not any(not c.isalnum() for c in password):
        raise PasswordError("Password must contain at least one symbol (!@#$ etc.).")

    # Check breached list (case-insensitive)
    if password.lower() in _get_breached():
        raise PasswordError(
            "This password appears in a known data breach. "
            "Please choose a different password."
        )


def validate_signup(email: str, password: str, full_name: str) -> None:
    """Run signup-time validations."""
    if not email or "@" not in email:
        raise PasswordError("A valid email address is required.")
    if not full_name or not full_name.strip():
        raise PasswordError("Full name is required.")
    validate_password(password)
