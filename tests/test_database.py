"""Tests for app/database.py — the database access layer.

Covers _required_env and async_table.  Conftest replaces
``sys.modules["app.database"]`` with a MagicMock, so we temporarily
restore the real module inside each test.
"""
from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _real_database():
    """Restore the real app.database module for the duration of the test."""
    saved = sys.modules.pop("app.database", None)
    import app as app_mod
    if hasattr(app_mod, "database"):
        delattr(app_mod, "database")
    importlib.invalidate_caches()
    from app import database as mod
    yield mod
    if saved is not None:
        sys.modules["app.database"] = saved
        app_mod.database = saved


def test_required_env_returns_value_when_set():
    from app.database import _required_env
    with patch.dict(os.environ, {"MY_TEST_VAR": "hello"}, clear=False):
        assert _required_env("MY_TEST_VAR") == "hello"


def test_required_env_exits_when_not_set():
    from app.database import _required_env
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit) as exc:
            _required_env("MISSING_VAR")
        assert exc.value.code == 1


class TestAsyncTable:
    def test_returns_postgres_table(self):
        fake = MagicMock()
        with patch("app.postgres_table.postgres_table", return_value=fake):
            from app.database import async_table
            result = async_table("my_table")
            assert result is fake

    def test_passes_name_through(self):
        with patch("app.postgres_table.postgres_table") as mock_pt:
            from app.database import async_table
            async_table("my_table")
            mock_pt.assert_called_once_with("my_table")
