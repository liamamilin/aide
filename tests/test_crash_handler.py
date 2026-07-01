"""Crash handler tests — verify exception capture and log writing."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from ai_desktop.utils.crash_handler import _on_crash


class TestCrashHandler:
    def test_on_crash_writes_log(self):
        crash_dir = Path(tempfile.mkdtemp())
        with patch("ai_desktop.utils.crash_handler._CRASH_LOG_DIR", crash_dir):
            try:
                raise ValueError("test error")
            except ValueError:
                _on_crash(*sys.exc_info())

        log_file = crash_dir / "crash.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "ValueError" in content
        assert "test error" in content

    def test_on_crash_swallows_exceptions(self):
        with patch("ai_desktop.utils.crash_handler._CRASH_LOG_DIR", Path("/nonexistent")):
            try:
                raise RuntimeError("test")
            except RuntimeError:
                _on_crash(*sys.exc_info())
