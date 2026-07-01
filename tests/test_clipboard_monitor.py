"""Clipboard capture tests."""
from unittest.mock import patch

from ai_desktop.capture import clipboard_monitor


def test_read_selection_restores_empty_clipboard_when_capture_succeeds():
    reads = ["", "selected text"]

    with patch.object(clipboard_monitor, "_read_clipboard", side_effect=lambda: reads.pop(0)), \
            patch.object(clipboard_monitor, "_write_clipboard") as write_clipboard, \
            patch.object(clipboard_monitor, "_try_cmd_c_via_pynput", return_value=True), \
            patch.object(clipboard_monitor.time, "sleep"):
        assert clipboard_monitor.read_selection() == "selected text"

    write_clipboard.assert_called_once_with("")


def test_read_selection_restores_empty_clipboard_when_copy_fails():
    with patch.object(clipboard_monitor, "_read_clipboard", return_value=""), \
            patch.object(clipboard_monitor, "_write_clipboard") as write_clipboard, \
            patch.object(clipboard_monitor, "_try_cmd_c_via_pynput", return_value=False), \
            patch.object(clipboard_monitor, "_try_cmd_c_via_osascript", return_value=False):
        assert clipboard_monitor.read_selection() is None

    write_clipboard.assert_called_once_with("")
