"""Unit tests for scripts/check_migration_safety.py — no DB required.

The linter is fed strings/temp files to verify all detection branches.
"""
import pathlib
import subprocess
import tempfile
from unittest.mock import patch


from scripts.check_migration_safety import (
    _check_file,
    _get_new_migrations,
    _strip_sql_comments,
    _CONTRACT_RE,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_migration(content: str, name: str = "test_migration.sql") -> pathlib.Path:
    """Write content to a temp file and return the path."""
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".sql")[1])
    tmp.write_text(content)
    # give it the .name we expect
    tmp.rename(tmp.with_name(name))
    return tmp.with_name(name)


# ── Comment stripping ───────────────────────────────────────────────────


class TestStripComments:
    def test_removes_single_line_comment(self):
        sql = "SELECT 1; -- this is a comment\nALTER TABLE t DROP COLUMN c;"
        cleaned = _strip_sql_comments(sql)
        assert "DROP COLUMN" in cleaned
        assert "this is a comment" not in cleaned

    def test_removes_block_comment(self):
        sql = "/* dangerous: DROP COLUMN */\nSELECT 1;"
        cleaned = _strip_sql_comments(sql)
        assert "DROP COLUMN" not in cleaned
        assert "SELECT 1;" in cleaned

    def test_comment_only_does_not_false_positive(self):
        """Patterns inside comments should not be flagged after stripping."""
        sql = "-- DROP COLUMN foo;\n-- DROP TABLE bar;\nSELECT 1;"
        cleaned = _strip_sql_comments(sql)
        assert cleaned.strip() == "SELECT 1;"


# ── Detection ───────────────────────────────────────────────────────────


class TestDetection:
    def test_add_column_if_not_exists_passes(self):
        content = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;"
        path = _make_migration(content)
        assert _check_file(path) == []

    def test_create_table_passes(self):
        content = "CREATE TABLE t (id INT);"
        path = _make_migration(content)
        assert _check_file(path) == []

    def test_create_index_passes(self):
        content = "CREATE INDEX CONCURRENTLY idx ON t (c);"
        path = _make_migration(content)
        assert _check_file(path) == []

    def test_not_valid_constraint_passes(self):
        content = "ALTER TABLE t ADD CONSTRAINT ck CHECK (c > 0) NOT VALID;"
        path = _make_migration(content)
        assert _check_file(path) == []

    def test_drop_column_fails(self):
        content = "ALTER TABLE t DROP COLUMN c;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "DROP COLUMN" in errors[0]

    def test_drop_table_fails(self):
        content = "DROP TABLE t;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "DROP TABLE" in errors[0]

    def test_alter_column_type_fails(self):
        content = "ALTER TABLE t ALTER COLUMN c TYPE TEXT;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "ALTER COLUMN" in errors[0]

    def test_rename_fails(self):
        content = "ALTER TABLE t RENAME COLUMN c TO d;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "RENAME" in errors[0]

    def test_set_not_null_fails(self):
        content = "ALTER TABLE t ALTER COLUMN c SET NOT NULL;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "SET NOT NULL" in errors[0]

    def test_drop_constraint_fails(self):
        content = "ALTER TABLE t DROP CONSTRAINT ck;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "DROP CONSTRAINT" in errors[0]

    def test_multiple_offenses(self):
        content = (
            "ALTER TABLE t DROP COLUMN c;\n"
            "DROP TABLE old;\n"
            "ALTER TABLE x ALTER COLUMN y TYPE INT;"
        )
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 3

    def test_drop_column_if_exists_fails(self):
        """DROP COLUMN IF EXISTS is still a contract step."""
        content = "ALTER TABLE t DROP COLUMN IF EXISTS c;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "DROP COLUMN" in errors[0]


# ── Contract marker ─────────────────────────────────────────────────────


class TestContractMarker:
    def test_marked_without_down_file_fails(self):
        content = "-- migration:contract drop legacy column\nALTER TABLE t DROP COLUMN c;"
        path = _make_migration(content)
        errors = _check_file(path)
        assert len(errors) == 1
        assert "contract" in errors[0]
        assert "down" in errors[0]

    def test_marked_with_down_file_passes(self):
        content = "-- migration:contract drop legacy column\nALTER TABLE t DROP COLUMN c;"
        path = _make_migration(content)
        # Create the down file at the correct path (MIGRATIONS / "down" / path.name)
        from scripts.check_migration_safety import MIGRATIONS
        down_dir = MIGRATIONS / "down"
        down_dir.mkdir(exist_ok=True)
        down_path = down_dir / path.name
        down_path.write_text("ALTER TABLE t ADD COLUMN c TEXT;")

        try:
            errors = _check_file(path)
            assert errors == []
        finally:
            down_path.unlink()
            if down_dir.exists() and not list(down_dir.iterdir()):
                down_dir.rmdir()

    def test_marker_on_second_line(self):
        """Marker must be in first 5 lines — verify tolerance."""
        from scripts.check_migration_safety import MIGRATIONS
        content = (
            "-- some intro comment\n"
            "-- migration:contract drop column\n"
            "ALTER TABLE t DROP COLUMN c;\n"
        )
        path = _make_migration(content)
        down_dir = MIGRATIONS / "down"
        down_dir.mkdir(exist_ok=True)
        down_path = down_dir / path.name
        down_path.write_text("ALTER TABLE t ADD COLUMN c TEXT;")
        try:
            errors = _check_file(path)
            assert errors == []
        finally:
            down_path.unlink()

    def test_contract_regex(self):
        m = _CONTRACT_RE.match("-- migration:contract drop legacy column")
        assert m is not None
        assert m.group(1).strip() == "drop legacy column"
        m = _CONTRACT_RE.match("-- migration:contract  ")
        assert m is not None
        assert m.group(1).strip() == ""
        m = _CONTRACT_RE.match("SELECT 1;")
        assert m is None


# ── Git-diff fallback ───────────────────────────────────────────────────


class TestGitDiffFallback:
    def test_git_failure_skips_rather_than_scanning_all(self):
        """git unavailable → return [] (skip), NOT scan-all. Scanning the whole
        history would false-fail on legacy migrations written before this guard."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            assert _get_new_migrations() == []

    def test_empty_diff_is_a_clean_pass_not_fallback(self):
        """returncode 0 with empty output = 0 new migrations (pass) — the bug
        that previously triggered scan-all and flagged legacy migrations."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            assert _get_new_migrations() == []

    def test_successful_diff_returns_added_files(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="migrations/phase999_x.sql\n", stderr="")
            paths = _get_new_migrations()
            assert [p.name for p in paths] == ["phase999_x.sql"]


# ── Integration-style (no subprocess) ───────────────────────────────────


class TestCliExitCodes:
    def test_clean_migration_exits_zero(self):
        content = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;"
        path = _make_migration(content, name="clean_mig.sql")
        assert _check_file(path) == []

    def test_destructive_migration_exits_one(self):
        content = "DROP TABLE t;"
        path = _make_migration(content, name="bad_mig.sql")
        errors = _check_file(path)
        assert len(errors) == 1

    def test_marked_without_down_exits_one(self):
        content = "-- migration:contract reason\nDROP TABLE t;"
        path = _make_migration(content, name="marked_no_down.sql")
        errors = _check_file(path)
        assert len(errors) == 1
