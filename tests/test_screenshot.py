"""截图模块测试 —— mock subprocess.run，覆盖成功/取消/权限失败"""
from unittest.mock import MagicMock

from ai_desktop.capture import screenshot

_PNG = b"\x89PNG\r\n\x1a\n fakebytes"


def _fake_run_result(stderr: str = "", creates_file: bool = False):
    """构造 mock subprocess.run：按需把文件写出来"""
    def _run(cmd, **kwargs):
        if creates_file:
            dest = cmd[-1]
            with open(dest, "wb") as f:
                f.write(_PNG)
        return MagicMock(stderr=stderr, stdout="")

    return _run


class TestCaptureRegion:
    def test_success_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.run",
            _fake_run_result(creates_file=True),
        )
        path, err = screenshot.capture_region()
        assert path and err == ""
        import os
        assert os.path.getsize(path) == len(_PNG)
        os.remove(path)

    def test_cancel_returns_none_no_error(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.run",
            _fake_run_result(),
        )
        path, err = screenshot.capture_region()
        assert path is None
        assert err == ""

    def test_permission_error_returned(self, monkeypatch):
        monkeypatch.setattr(
            "ai_desktop.capture.screenshot.subprocess.run",
            _fake_run_result(stderr="screencapture: could not create image from rect"),
        )
        path, err = screenshot.capture_region()
        assert path is None
        assert "could not create image" in err
        assert screenshot.is_permission_error(err)


class TestIsPermissionError:
    def test_matches_rect_and_display_errors(self):
        assert screenshot.is_permission_error("screencapture: could not create image from rect")
        assert screenshot.is_permission_error("could not create image from display")
        assert not screenshot.is_permission_error("")
        assert not screenshot.is_permission_error("random failure")
