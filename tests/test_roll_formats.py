"""Roll-format classification tests.

Audit #7 (bulk student import wizard) — the dry-run preview hinges on
``classify_roll`` and ``detect_dominant_format`` getting the buckets
right. Spot-check the patterns + the "dominant valid format wins over
a noisy minority" tie-break.
"""
from app.services.roll_formats import (
    classify_roll,
    detect_dominant_format,
    format_label,
)


class TestClassifyRoll:
    def test_cbse_board_8_digits(self):
        assert classify_roll("12345678") == "cbse_board"

    def test_jee_advanced_iit_format(self):
        # 2-digit year + 1 letter (city code) + 8 digits
        assert classify_roll("23A12345678") == "jee_advanced"
        assert classify_roll("24Z00000001") == "jee_advanced"

    def test_nta_app_id_9_10_11_digits(self):
        # JEE Main / NEET / UPSC numeric application IDs
        assert classify_roll("202612345678") == "nta_app_id" or \
               classify_roll("20261234567") == "nta_app_id"
        assert classify_roll("123456789") == "nta_app_id"        # 9
        assert classify_roll("1234567890") == "nta_app_id"       # 10
        assert classify_roll("12345678901") == "nta_app_id"      # 11

    def test_generic_alnum_fallback(self):
        # Coaching-institute custom IDs
        assert classify_roll("STU-001") == "generic_alnum"
        assert classify_roll("ALLEN2024-789") == "generic_alnum"

    def test_invalid_empty_or_garbage(self):
        assert classify_roll("") == "invalid"
        assert classify_roll(None) == "invalid"  # type: ignore[arg-type]
        # Chars outside [A-Z0-9_-]
        assert classify_roll("abc@123") == "invalid"
        assert classify_roll("STUDENT 1") == "invalid"  # space rejected
        # Too short for generic_alnum (< 4 chars)
        assert classify_roll("A1") == "invalid"

    def test_case_insensitive(self):
        # Bulk-register normalises to upper; detector should match either.
        assert classify_roll("23a12345678") == "jee_advanced"
        assert classify_roll("stu-001") == "generic_alnum"


class TestDetectDominantFormat:
    def test_pure_cbse_class(self):
        rolls = [f"{i:08d}" for i in range(50)]
        dominant, counts = detect_dominant_format(rolls)
        assert dominant == "cbse_board"
        assert counts["cbse_board"] == 50

    def test_majority_cbse_with_typos(self):
        # 97 CBSE rolls + 3 typos. Dominant should still be CBSE.
        rolls = [f"{i:08d}" for i in range(97)] + ["bad", "", "x"]
        dominant, counts = detect_dominant_format(rolls)
        assert dominant == "cbse_board"
        assert counts["cbse_board"] == 97
        assert counts["invalid"] == 3

    def test_all_invalid_returns_invalid_dominant(self):
        dominant, counts = detect_dominant_format(["", "", "bad@", " "])
        assert dominant == "invalid"
        assert counts["invalid"] == 4

    def test_mixed_jee_main_and_cbse_picks_majority(self):
        # 60 JEE Main vs 40 CBSE — JEE wins.
        rolls = [f"2026{i:07d}" for i in range(60)] + [f"{i:08d}" for i in range(40)]
        dominant, counts = detect_dominant_format(rolls)
        assert dominant == "nta_app_id"
        assert counts["nta_app_id"] == 60
        assert counts["cbse_board"] == 40

    def test_empty_input(self):
        dominant, counts = detect_dominant_format([])
        assert dominant == "invalid"
        assert counts == {}


def test_format_label_returns_human_string():
    assert "CBSE" in format_label("cbse_board")
    assert "NTA" in format_label("nta_app_id")
    assert format_label("nonexistent_key") == "nonexistent_key"
