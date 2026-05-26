"""Password complexity rules and HIBP top-1000 breach check."""
import hashlib
import logging
import os
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 10
REQUIRE_UPPER = True
REQUIRE_LOWER = True
REQUIRE_DIGIT = True
REQUIRE_SYMBOL = True

# Path to bundled top-1000 breached passwords list
_BREACHED_FILE = Path(__file__).parent.parent.parent / "data" / "breached_top1000.txt"
# Path to bundled disposable-email-domain blocklist
_DISPOSABLE_FILE = Path(__file__).parent.parent.parent / "data" / "disposable_email_domains.txt"
_HIBP_CHECK_ENABLED = os.environ.get("HIBP_PASSWORD_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}
_HIBP_TIMEOUT_SECONDS = float(os.environ.get("HIBP_PASSWORD_TIMEOUT_SECONDS", "1.5"))


def _load_text_set(path: Path, label: str) -> set[str]:
    """Load a newline-delimited file into a lowercased set.

    Shared loader for both the breached-passwords list and the
    disposable-email-domain list. Both files have the same format:
    one entry per line, # comments, blank lines ignored.
    """
    if not path.exists():
        logger.warning("[passwords] %s not found at %s", label, path)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return set()
    try:
        with open(path) as f:
            return {
                line.strip().lower()
                for line in f
                if line.strip() and not line.startswith("#")
            }
    except Exception as e:
        logger.warning("[passwords] failed to load %s: %s", label, e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return set()


_BREACHED: set[str] | None = None
_DISPOSABLE: set[str] | None = None


def _get_breached() -> set[str]:
    global _BREACHED
    if _BREACHED is None:
        _BREACHED = _load_text_set(_BREACHED_FILE, "breached_top1000.txt")
    return _BREACHED


def _get_disposable() -> set[str]:
    global _DISPOSABLE
    if _DISPOSABLE is None:
        _DISPOSABLE = _load_text_set(_DISPOSABLE_FILE, "disposable_email_domains.txt")
    return _DISPOSABLE


def is_disposable_email(email: str) -> bool:
    """True if the email domain matches a known disposable provider.

    Domain is normalised (lowercase, trimmed) before lookup. Returns
    False on malformed input — let the upstream email regex catch
    bad addresses, not us.
    """
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain in _get_disposable()


def _appears_in_hibp(password: str) -> bool:
    """Check Have I Been Pwned's k-anonymity range API when enabled.

    The password itself never leaves the process: we send only the first
    five SHA-1 characters and compare suffixes locally. Network errors
    fail open so signup does not hard-depend on a third-party service.
    """
    if not _HIBP_CHECK_ENABLED:
        return False
    # SHA-1 is REQUIRED by the HIBP k-anonymity API (not for crypto security).
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    # Dynamic URL is a hex prefix (0-9 A-F only); the host is constant and
    # the path is validated by character class. Not user-controlled.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    req = urllib.request.Request(
        f"https://api.pwnedpasswords.com/range/{prefix}",
        headers={"User-Agent": "ProctaPasswordAudit/1.0"},
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=_HIBP_TIMEOUT_SECONDS) as resp:
            body = resp.read(512_000).decode("utf-8", "replace")
    except Exception as exc:
        # Log line contains the string literal "passwords" only as a module-tag
        # prefix; no credential is interpolated. The %s formatter receives the
        # exception class name, never the password value.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning("[passwords] HIBP range check unavailable: %s", exc.__class__.__name__)
        return False
    for line in body.splitlines():
        candidate, _, count = line.partition(":")
        if candidate.upper() == suffix:
            try:
                return int(count.strip() or "0") > 0
            except ValueError:
                return True
    return False


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
    if _appears_in_hibp(password):
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
    if is_disposable_email(email):
        raise PasswordError(
            "Please use a permanent email address. Disposable-email "
            "domains are not allowed."
        )
    validate_password(password)
