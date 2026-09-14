"""Clipboard capture tests."""
import sys
import time
from unittest.mock import patch

from PyQt5.QtCore import QTimer

from ai_desktop.capture import clipboard_monitor


def test_decode_text_output_preserves_utf8_and_utf16_cjk():
    text = "中文选区：解释这个错误"
    assert clipboard_monitor._decode_text_output(text.encode("utf-8")) == text
    assert clipboard_monitor._decode_text_output(text.encode("utf-16")) == text
    assert clipboard_monitor._decode_text_output(text.encode("utf-16-le")) == text


def test_native_pasteboard_reads_declared_unicode_type_without_locale_guessing():
    class Pasteboard:
        def stringForType_(self, pasteboard_type):
            if pasteboard_type == "public.utf8-plain-text":
                return "中文选区：解释这个错误"
            return None

        def dataForType_(self, _pasteboard_type):
            return None

    native = object.__new__(clipboard_monitor.NativePasteboard)
    native._pasteboard = Pasteboard()
    assert native.read_plain_text() == "中文选区：解释这个错误"


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


class FakeSelectionCapture(clipboard_monitor.SelectionCaptureTask):
    def __init__(self, responses, *, copy_action=lambda: True, counts=(2, 2),
                 pasteboard=None):
        self.pasteboard = pasteboard or FakePasteboard(counts)
        super().__init__(copy_action=copy_action, pasteboard=self.pasteboard)
        self.responses = list(responses)
        self.calls = []

    def _start_command(self, program, arguments, *, timeout_ms, callback, input_text=None):
        self.calls.append((program, arguments, input_text, timeout_ms))
        success, stdout, stderr = self.responses.pop(0)
        QTimer.singleShot(0, lambda: callback(success, stdout, stderr))


class FakePasteboard:
    def __init__(self, counts=(2, 2), *, snapshot_error=None):
        self.counts = list(counts)
        self.snapshot_error = snapshot_error
        self.saved = clipboard_monitor.PasteboardSnapshot(
            1,
            ((("public.utf8-plain-text", b"original"),
              ("public.html", b"<b>original</b>"),
              ("public.png", b"png-bytes")),),
        )
        self.restores = []

    def snapshot(self):
        if self.snapshot_error:
            raise self.snapshot_error
        return self.saved

    def change_count(self):
        if len(self.counts) > 1:
            return self.counts.pop(0)
        return self.counts[0]

    def restore(self, snapshot, expected_change_count):
        self.restores.append((snapshot, expected_change_count))
        return True


def test_async_capture_reads_and_restores_clipboard(qtbot):
    task = FakeSelectionCapture([
        (True, "  selected text  ", ""),
    ])
    with qtbot.waitSignal(task.completed, timeout=1000) as signal:
        task.start()
    assert signal.args == ["selected text"]
    assert [call[0] for call in task.calls] == ["/usr/bin/pbpaste"]
    assert task.calls[0][1] == ["-Prefer", "txt"]
    assert task.pasteboard.restores == [(task.pasteboard.saved, 2)]


def test_async_capture_uses_osascript_when_clipboard_is_unchanged(qtbot):
    task = FakeSelectionCapture([
        (True, "", ""),
        (True, "fallback selection", ""),
    ], counts=(1, 2, 2))
    with qtbot.waitSignal(task.completed, timeout=1500) as signal:
        task.start()
    assert signal.args == ["fallback selection"]
    assert [call[0] for call in task.calls] == [
        "/usr/bin/osascript",
        "/usr/bin/pbpaste",
    ]


def test_cancel_during_capture_restores_plain_text_once(qtbot):
    class HoldingCapture(FakeSelectionCapture):
        def _start_command(self, program, arguments, *, timeout_ms, callback, input_text=None):
            self.calls.append((program, arguments, input_text, timeout_ms))
            if len(self.calls) == 1:
                self.held_callback = callback
                return
            success, stdout, stderr = self.responses.pop(0)
            QTimer.singleShot(0, lambda: callback(success, stdout, stderr))

    task = HoldingCapture([
        (True, "", ""),
    ])
    task.start()
    qtbot.waitUntil(lambda: len(task.calls) == 1, timeout=1000)
    with qtbot.waitSignal(task.completed, timeout=1000) as signal:
        task.cancel()
    assert signal.args == [""]
    assert [call[0] for call in task.calls] == ["/usr/bin/pbpaste"]
    assert task.pasteboard.restores == [(task.pasteboard.saved, 2)]


def test_async_capture_does_not_overwrite_new_user_copy(qtbot):
    task = FakeSelectionCapture(
        [(True, "selected text", "")],
        counts=(2, 3),
    )
    with qtbot.waitSignal(task.completed, timeout=1000) as signal:
        task.start()
    assert signal.args == [""]
    assert task.pasteboard.restores == []


def test_async_capture_stops_before_copy_when_format_cannot_be_saved(qtbot):
    pasteboard = FakePasteboard(
        snapshot_error=clipboard_monitor.UnsupportedClipboardFormatError("promised data"),
    )
    copy_action = patch.object(clipboard_monitor, "_try_cmd_c_via_pynput")
    with copy_action as copy:
        task = clipboard_monitor.SelectionCaptureTask(pasteboard=pasteboard)
        with qtbot.waitSignal(task.completed, timeout=1000) as signal:
            task.start()
    assert signal.args == [""]
    copy.assert_not_called()


def test_qprocess_command_does_not_block_qt_event_loop(qtbot):
    task = clipboard_monitor.SelectionCaptureTask()
    pulse = []
    command_done = []
    task._start_command(
        sys.executable,
        ["-c", "import time; time.sleep(0.15); print('done')"],
        timeout_ms=1000,
        callback=lambda success, stdout, _stderr: command_done.append((success, stdout)),
    )
    started = time.perf_counter()
    QTimer.singleShot(20, lambda: pulse.append(time.perf_counter()))
    qtbot.waitUntil(lambda: bool(pulse), timeout=500)
    assert pulse[0] - started < 0.2
    assert command_done == []
    qtbot.waitUntil(lambda: bool(command_done), timeout=1000)
    assert command_done[0][0]
    assert command_done[0][1].strip() == "done"
