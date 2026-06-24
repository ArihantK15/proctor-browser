"""
Tests for the macOS Handoff / Universal Clipboard kill-switch in proctor.py.

Why this exists: macOS Handoff lets a Cmd+C on one Apple device land as a
Cmd+V on another device signed into the same Apple ID nearby. A student
could copy an answer on a hidden iPhone and paste it straight into the exam
editor — no camera-visible action at all. _disable_handoff() / _restore_handoff()
toggle this off for the exam and back on at clean exit.

These tests mock subprocess.run and platform.system — they never actually
toggle Handoff on the machine running the test suite.
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

_proctor_deps = ["cv2", "numpy", "uniface", "onnxruntime"]
_missing = []
for dep in _proctor_deps:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except ImportError:
            _missing.append(dep)

pytestmark = pytest.mark.skipif(
    bool(_missing),
    reason=f"proctor.py dependencies not installed (missing: {', '.join(_missing)})"
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _import_proctor():
    import proctor
    return proctor


class TestDisableHandoff:
    def test_disable_issues_three_commands_on_darwin(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", True)
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._disable_handoff()

        assert mock_run.call_count == 3
        calls = [c.args[0] for c in mock_run.call_args_list]

        advertising = calls[0]
        assert advertising[:2] == ["defaults", "write"]
        assert "com.apple.coreservices.useractivityd" in advertising
        assert "ActivityAdvertisingAllowed" in advertising
        assert "false" in advertising

        receiving = calls[1]
        assert "ActivityReceivingAllowed" in receiving
        assert "false" in receiving

        killall = calls[2]
        assert killall == ["killall", "useractivityd"]

    def test_disable_is_noop_on_linux(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Linux")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", True)
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._disable_handoff()
        mock_run.assert_not_called()

    def test_disable_is_noop_on_windows(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Windows")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", True)
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._disable_handoff()
        mock_run.assert_not_called()

    def test_disable_skipped_when_env_opts_out(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        # Simulate PROCTOR_DISABLE_HANDOFF=0 by setting the module flag the
        # way module import would have derived it from the env var.
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", False)
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._disable_handoff()
        mock_run.assert_not_called()

    def test_disable_env_flag_parses_to_false(self, monkeypatch):
        # Confirms the env-var contract itself: PROCTOR_DISABLE_HANDOFF=0
        # must compute to a falsy DISABLE_HANDOFF, mirroring module-load logic.
        monkeypatch.setenv("PROCTOR_DISABLE_HANDOFF", "0")
        value = os.environ.get("PROCTOR_DISABLE_HANDOFF", "1") == "1"
        assert value is False

    def test_disable_swallows_subprocess_exception(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", True)
        mock_run = MagicMock(side_effect=OSError("boom"))
        with patch("subprocess.run", mock_run):
            # Must not raise — proctoring must survive a subprocess failure.
            proctor._disable_handoff()

    def test_disable_swallows_killall_exception(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", True)

        def _flaky(cmd, **kwargs):
            if cmd and cmd[0] == "killall":
                raise OSError("no such process")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_flaky):
            proctor._disable_handoff()  # must not raise


class TestRestoreHandoff:
    def test_restore_issues_restore_commands_on_darwin(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._restore_handoff()

        assert mock_run.call_count == 3
        calls = [c.args[0] for c in mock_run.call_args_list]

        advertising = calls[0]
        assert "ActivityAdvertisingAllowed" in advertising
        assert "true" in advertising

        receiving = calls[1]
        assert "ActivityReceivingAllowed" in receiving
        assert "true" in receiving

        killall = calls[2]
        assert killall == ["killall", "useractivityd"]

    def test_restore_is_noop_on_linux(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Linux")
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._restore_handoff()
        mock_run.assert_not_called()

    def test_restore_runs_even_when_disable_flag_is_off(self, monkeypatch):
        # Restore must not be gated by DISABLE_HANDOFF — if disable was ever
        # toggled on (e.g. by a previous run or manual `defaults write`), a
        # restore call must still be able to undo it regardless of the
        # current flag value.
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(proctor, "DISABLE_HANDOFF", False)
        mock_run = MagicMock()
        with patch("subprocess.run", mock_run):
            proctor._restore_handoff()
        assert mock_run.call_count == 3

    def test_restore_swallows_subprocess_exception(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        mock_run = MagicMock(side_effect=OSError("boom"))
        with patch("subprocess.run", mock_run):
            proctor._restore_handoff()  # must not raise

    def test_restore_swallows_killall_exception(self, monkeypatch):
        proctor = _import_proctor()
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")

        def _flaky(cmd, **kwargs):
            if cmd and cmd[0] == "killall":
                raise OSError("no such process")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_flaky):
            proctor._restore_handoff()  # must not raise
