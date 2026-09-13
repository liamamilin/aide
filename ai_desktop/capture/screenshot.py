"""
截图捕获 —— 调用 macOS 原生 screencapture 进行框选截图

用于「截图快捷键 / 📎 菜单截图」：弹出系统级框选 UI，用户选区后落盘为 PNG，
返回临时文件路径（调用方负责复制到应用数据目录并清理）。

若屏幕录制权限未授予，screencapture 会输出
"could not create image from rect/display" 之类的错误 —— 本模块捕获并返回给调用方，
由上层引导用户去系统设置授权。
"""
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ScreenshotStatus(str, Enum):
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    TIMED_OUT = "timed_out"
    COMMAND_FAILED = "command_failed"


@dataclass(frozen=True)
class ScreenshotResult:
    status: ScreenshotStatus
    path: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ScreenshotStatus.SUCCEEDED


def _stop_process(proc: subprocess.Popen) -> None:
    """Terminate only the screencapture process created by this call."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=0.5)


def capture_region_result(cancelled: Callable[[], bool] | None = None, *,
                          timeout_seconds: float = 30) -> ScreenshotResult:
    """Run interactive capture and return an explicit terminal state."""
    if cancelled and cancelled():
        return ScreenshotResult(ScreenshotStatus.CANCELLED)

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    proc = None
    result = None
    try:
        proc = subprocess.Popen(
            ["screencapture", "-i", "-x", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + timeout_seconds
        while proc.poll() is None:
            if cancelled and cancelled():
                result = ScreenshotResult(ScreenshotStatus.CANCELLED)
                _stop_process(proc)
                break
            if time.monotonic() >= deadline:
                result = ScreenshotResult(
                    ScreenshotStatus.TIMED_OUT,
                    error=f"截图在 {timeout_seconds:g} 秒内未完成，请重试。",
                )
                _stop_process(proc)
                break
            time.sleep(0.02)
        try:
            stdout, stderr = proc.communicate()
        except Exception:
            if result is None:
                raise
            stdout, stderr = "", ""
        output = (stderr or stdout or "").strip()
        if result is None and os.path.exists(path) and os.path.getsize(path) > 0:
            result = ScreenshotResult(ScreenshotStatus.SUCCEEDED, path=path)
        elif result is None and is_permission_error(output):
            result = ScreenshotResult(ScreenshotStatus.PERMISSION_DENIED, error=output)
        elif result is None and output:
            result = ScreenshotResult(ScreenshotStatus.COMMAND_FAILED, error=output)
        elif result is None:
            # macOS screencapture exits without a file or message when Esc is used.
            result = ScreenshotResult(ScreenshotStatus.CANCELLED)
    except Exception as e:
        if proc is not None:
            try:
                _stop_process(proc)
            except Exception:
                logger.warning("Failed to stop screencapture", exc_info=True)
        result = ScreenshotResult(ScreenshotStatus.COMMAND_FAILED, error=str(e))
        logger.warning("screencapture failed: %s", e)
    finally:
        if result is None or not result.ok:
            try:
                os.remove(path)
            except OSError:
                pass

    if result is None:
        return ScreenshotResult(ScreenshotStatus.COMMAND_FAILED, error="截图命令未返回结果。")
    if result.error:
        logger.warning("screencapture: %s", result.error)
    return result


def capture_region(cancelled: Callable[[], bool] | None = None) -> Tuple[Optional[str], str]:
    """Compatibility wrapper returning the original ``(path, error)`` tuple."""
    result = capture_region_result(cancelled)
    if result.ok:
        return result.path, ""
    if result.status == ScreenshotStatus.CANCELLED:
        return None, ""
    return None, result.error


def is_permission_error(err: str) -> bool:
    """判断错误是否属于「屏幕录制权限未授予」"""
    value = err.lower()
    return any(marker in value for marker in (
        "could not create image",
        "screen recording permission",
        "not authorized to capture",
        "not permitted to capture",
    ))
