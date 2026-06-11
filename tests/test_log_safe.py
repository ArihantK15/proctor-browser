"""Unit tests for app.log_safe — log-injection sanitisation and PII masking.

These guard two invariants relied on at every log site:
  * safe() neutralises control characters (the log-forging vector).
  * mask_email() never emits a full address, yet stays injection-safe.
"""
from app.log_safe import mask_email, safe


class TestSafe:
    def test_strips_newlines(self):
        # A forged second log line must not survive intact.
        out = safe("victim\nX-User: admin")
        assert "\n" not in out
        assert "admin" in out  # content preserved, just neutralised

    def test_none_is_empty(self):
        assert safe(None) == ""

    def test_email_survives_readable(self):
        # safe() is injection-only: the address is intentionally NOT masked.
        assert safe("a@b.com") == "a@b.com"

    def test_truncates_long(self):
        out = safe("x" * 500)
        assert len(out) <= 200


class TestMaskEmail:
    def test_masks_local_part(self):
        assert mask_email("alice@example.com") == "a***@example.com"

    def test_never_leaks_full_address(self):
        for addr in ("bob.smith@school.edu", "x@y.io", "STUDENT@Univ.AC.IN"):
            out = mask_email(addr)
            local = addr.split("@", 1)[0]
            # Only the first local char is kept; everything after it is gone.
            assert local[1:] == "" or local[1:] not in out
            # A multi-char local must never appear in full.
            if len(local) > 1:
                assert local not in out
            assert "***@" in out

    def test_domain_preserved_for_correlation(self):
        assert mask_email("alice@example.com").endswith("@example.com")

    def test_none_safe(self):
        assert mask_email(None) == ""

    def test_non_email_falls_through_to_safe(self):
        # No '@' → still neutralise control chars rather than crash.
        out = mask_email("not-an-email\nINJECT")
        assert "\n" not in out

    def test_empty_local_part(self):
        # Degenerate "@domain" must not raise and must not crash on local[:1].
        assert mask_email("@example.com") == "***@example.com"

    def test_mask_is_injection_safe(self):
        # A newline smuggled into the local part must not survive the mask.
        out = mask_email("ev\nil@example.com")
        assert "\n" not in out
