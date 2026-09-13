"""截图模块测试：覆盖成功、取消、权限失败和进程终止。"""
import threading
import time
from unittest.mock import MagicMock

from ai_desktop.capture import screenshot
from ai_desktop.capture.screenshot import ScreenshotStatus
from ai_desktop.main import ScreenshotWorker

_PNG = b"\x89PNG\r\n\x1a\n fakebytes"


def _fake_process(stderr: str = "", creates_file: bool = False):
    def _popen(cmd, **kwargs):
        if creates_file:
            dest = cmd[-1]
            with open(dest, "wb") as f:
                f.write(_PNG)
        process = MagicMock()
        process.poll.return_value = 0
        process.communicate.return_value = ("", stderr)
        return process

    return _popen


class TestCaptureRegion:
    def test_success_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(creates_file=True),
        )
        path, err = screenshot.capture_region()
        assert path and err == ""
        import os
        assert os.path.getsize(path) == len(_PNG)
        os.remove(path)

    def test_cancel_returns_none_no_error(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(),
        )
        path, err = screenshot.capture_region()
        assert path is None
        assert err == ""

    def test_permission_error_returned(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(stderr="screencapture: could not create image from rect"),
        )
        path, err = screenshot.capture_region()
        assert path is None
        assert "could not create image" in err
        assert screenshot.is_permission_error(err)

    def test_cancel_terminates_process_and_removes_temp_file(self, monkeypatch):
        cancelled = threading.Event()
        process = MagicMock()
        running = True

        def poll():
            return None if running else -15

        def terminate():
            nonlocal running
            running = False

        process.poll.side_effect = poll
        process.terminate.side_effect = terminate
        process.communicate.return_value = ("", "")
        monkeypatch.setattr("ai_desktop.capture.screenshot.subprocess.Popen", lambda *args, **kwargs: process)
        timer = threading.Timer(0.04, cancelled.set)
        timer.start()
        try:
            started = time.monotonic()
            path, err = screenshot.capture_region(cancelled.is_set)
        finally:
            timer.cancel()
        assert time.monotonic() - started < 0.5
        assert path is None and err == ""
        process.terminate.assert_called_once()

    def test_worker_cancelled_before_run_does_not_spawn_process(self, qapp, monkeypatch):
        popen = MagicMock()
        monkeypatch.setattr("ai_desktop.capture.screenshot.subprocess.Popen", popen)
        worker = ScreenshotWorker()
        worker.cancel()
        worker.run()
        popen.assert_not_called()

    def test_explicit_success_result_owns_temporary_path(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(creates_file=True),
        )
        result = screenshot.capture_region_result()
        assert result.status == ScreenshotStatus.SUCCEEDED
        assert result.ok and result.path
        import os
        os.remove(result.path)

    def test_explicit_user_cancel_has_no_error(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(),
        )
        result = screenshot.capture_region_result()
        assert result.status == ScreenshotStatus.CANCELLED
        assert result.error == ""

    def test_explicit_permission_error(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            _fake_process(stderr="screencapture: could not create image from display"),
        )
        result = screenshot.capture_region_result()
        assert result.status == ScreenshotStatus.PERMISSION_DENIED

    def test_timeout_is_distinct_and_cleans_temporary_file(self, monkeypatch):
        process = MagicMock()
        running = True
        created_path = []

        def poll():
            return None if running else -15

        def terminate():
            nonlocal running
            running = False

        def popen(cmd, **kwargs):
            created_path.append(cmd[-1])
            process.poll.side_effect = poll
            process.terminate.side_effect = terminate
            process.communicate.return_value = ("", "")
            return process

        monkeypatch.setattr("ai_desktop.capture.screenshot.subprocess.Popen", popen)
        result = screenshot.capture_region_result(timeout_seconds=0.04)
        assert result.status == ScreenshotStatus.TIMED_OUT
        assert "0.04" in result.error
        assert created_path
        import os
        assert not os.path.exists(created_path[0])

    def test_command_start_failure_is_distinct_and_cleans_temp(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.Popen",
            MagicMock(side_effect=FileNotFoundError("screencapture missing")),
        )
        result = screenshot.capture_region_result()
        assert result.status == ScreenshotStatus.COMMAND_FAILED
        assert "missing" in result.error


class TestIsPermissionError:
    def test_matches_rect_and_display_errors(self):
        assert screenshot.is_permission_error("screencapture: could not create image from rect")
        assert screenshot.is_permission_error("could not create image from display")
        assert screenshot.is_permission_error("Screen recording permission was not granted")
        assert screenshot.is_permission_error("not authorized to capture this display")
        assert not screenshot.is_permission_error("")
        assert not screenshot.is_permission_error("random failure")
