"""
选中文本读取模块

策略：
  1. pynput CGEvent 模拟 ⌘C（主方案，低延迟）
  2. osascript + System Events keystroke（回退方案，macOS 权限隔离场景）
  不依赖 Accessibility API（只对原生 Cocoa 应用有效）。
"""
import logging
import subprocess
import time
from collections.abc import Callable
from typing import Optional

from pynput.keyboard import Controller, Key
from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal

from ai_desktop.capture import text_normalizer

logger = logging.getLogger(__name__)

_keyboard = Controller()
_COPY_SCRIPT = (
    'tell application "System Events" '
    'to tell (first process whose frontmost is true) '
    'to keystroke "c" using command down'
)


def _read_clipboard() -> str:
    """通过 pbpaste 读取剪贴板"""
    try:
        return subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return ""


def _write_clipboard(text: str) -> bool:
    """通过 pbcopy 写入剪贴板，成功返回 True"""
    try:
        subprocess.run(
            ["pbcopy"],
            input=text,
            text=True,
            timeout=2,
        )
        return True
    except Exception:
        logger.warning("Failed to restore clipboard content via pbcopy", exc_info=True)
        return False


def _try_cmd_c_via_pynput() -> bool:
    """尝试通过 pynput Controller 模拟 ⌘C。成功返回 True。"""
    try:
        with _keyboard.pressed(Key.cmd):
            _keyboard.press("c")
            _keyboard.release("c")
        return True
    except Exception:
        logger.warning("pynput Cmd+C failed", exc_info=True)
        return False


def _try_cmd_c_via_osascript() -> bool:
    """通过 osascript (System Events) 向前台应用发送 ⌘C。成功返回 True。"""
    try:
        subprocess.run(
            ["osascript", "-e", _COPY_SCRIPT],
            capture_output=True,
            timeout=3,
        )
        return True
    except Exception:
        logger.warning("osascript Cmd+C failed", exc_info=True)
        return False


def read_selection() -> Optional[str]:
    """
    读取当前选中文本。

    流程：
    1. 保存当前剪贴板
    2. 模拟 ⌘C（pynput，失败则回退 osascript）
    3. 等待剪贴板更新
    4. 读取新剪贴板
    5. 恢复原剪贴板
    """
    # 1. 保存原剪贴板
    saved = _read_clipboard()

    # 2. 模拟 ⌘C（修饰键已由调用方的 QTimer 延迟确保释放）
    if not _try_cmd_c_via_pynput():
        # pynput 失败，尝试 osascript 回退
        if _try_cmd_c_via_osascript():
            time.sleep(0.15)
        else:
            _write_clipboard(saved)
            return None

    # 3. 等待剪贴板更新
    time.sleep(0.08)

    # 4. 读取新内容
    selected = _read_clipboard()

    # 5. 如果剪贴板未变化，尝试 osascript 回退
    if saved and selected.strip() == saved.strip():
        logger.info("pynput Cmd+C had no effect, trying osascript fallback...")
        if _try_cmd_c_via_osascript():
            time.sleep(0.15)
            selected = _read_clipboard()

    # 6. 恢复原剪贴板
    _write_clipboard(saved)

    # 7. 判断是否有效
    if not selected:
        logger.info("Clipboard empty after Cmd+C — no text captured")
        return None

    if saved and selected.strip() == saved.strip():
        logger.info("Clipboard unchanged after Cmd+C — no selection detected")
        return None

    logger.info("Captured %d chars via clipboard", len(selected))
    return text_normalizer.normalize(selected)


class SelectionCaptureTask(QObject):
    """Capture selected text as a non-blocking QProcess/QTimer state machine.

    The quick pynput key event stays on the object's Qt thread. Clipboard and
    AppleScript subprocesses are asynchronous. This still restores plain text
    only; callers must not present it as full multi-format clipboard backup.
    """

    completed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None, *,
                 copy_action: Callable[[], bool] | None = None) -> None:
        super().__init__(parent)
        self._copy_action = copy_action or _try_cmd_c_via_pynput
        self._process: QProcess | None = None
        self._command_callback = None
        self._command_timer = QTimer(self)
        self._command_timer.setSingleShot(True)
        self._command_timer.timeout.connect(self._on_command_timeout)
        self._phase_timer = QTimer(self)
        self._phase_timer.setSingleShot(True)
        self._phase_timer.timeout.connect(self._run_phase)
        self._phase_callback = None
        self._started = False
        self._cancelled = False
        self._terminal = False
        self._saved = ""
        self._saved_known = False
        self._clipboard_modified = False
        self._used_fallback = False
        self._restoring = False
        self._pending_text = ""

    def start(self) -> None:
        if self._started or self._terminal:
            return
        self._started = True
        self._start_command(
            "/usr/bin/pbpaste", [], timeout_ms=2000,
            callback=self._on_original_clipboard,
        )

    def cancel(self) -> None:
        if self._terminal or self._cancelled:
            return
        self._cancelled = True
        self._phase_timer.stop()
        self._phase_callback = None
        process = self._process
        if process is not None and not self._restoring:
            process.kill()
            self._finish_command(process, False)
        elif process is None:
            self._restore_and_complete()

    def _on_original_clipboard(self, success: bool, stdout: str, stderr: str) -> None:
        if self._cancelled:
            self._complete("")
            return
        if not success:
            logger.warning("Cannot read clipboard before capture: %s", stderr)
            self._complete("")
            return
        self._saved = stdout
        self._saved_known = True
        try:
            copy_started = self._copy_action()
        except Exception:
            logger.warning("pynput Cmd+C failed", exc_info=True)
            copy_started = False
        if copy_started:
            self._clipboard_modified = True
            self._wait_for_clipboard(80, self._read_selection)
        else:
            self._run_osascript_copy()

    def _run_osascript_copy(self) -> None:
        if self._cancelled:
            self._restore_and_complete()
            return
        self._used_fallback = True
        # Restore as a precaution if cancellation happens while AppleScript runs.
        self._clipboard_modified = True
        self._start_command(
            "/usr/bin/osascript", ["-e", _COPY_SCRIPT], timeout_ms=3000,
            callback=self._on_osascript_copy,
        )

    def _on_osascript_copy(self, success: bool, _stdout: str, stderr: str) -> None:
        if self._cancelled:
            self._restore_and_complete()
        elif success:
            self._wait_for_clipboard(150, self._read_selection)
        else:
            logger.warning("osascript Cmd+C failed: %s", stderr)
            self._restore_and_complete()

    def _read_selection(self) -> None:
        if self._cancelled:
            self._restore_and_complete()
            return
        self._start_command(
            "/usr/bin/pbpaste", [], timeout_ms=2000,
            callback=self._on_selection_clipboard,
        )

    def _on_selection_clipboard(self, success: bool, stdout: str, stderr: str) -> None:
        if self._cancelled:
            self._restore_and_complete()
            return
        if not success:
            logger.warning("Cannot read captured clipboard: %s", stderr)
            self._restore_and_complete()
            return
        selected = stdout
        if self._saved and selected.strip() == self._saved.strip() and not self._used_fallback:
            logger.info("pynput Cmd+C had no effect, trying osascript fallback...")
            self._run_osascript_copy()
            return
        if not selected or (self._saved and selected.strip() == self._saved.strip()):
            selected = ""
        else:
            selected = text_normalizer.normalize(selected)
        self._restore_and_complete(selected)

    def _wait_for_clipboard(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self._phase_callback = callback
        self._phase_timer.start(delay_ms)

    def _run_phase(self) -> None:
        callback = self._phase_callback
        self._phase_callback = None
        if callback is not None and not self._terminal:
            callback()

    def _restore_and_complete(self, text: str = "") -> None:
        if self._terminal:
            return
        self._pending_text = "" if self._cancelled else text
        if self._clipboard_modified and self._saved_known and not self._restoring:
            self._restoring = True
            self._start_command(
                "/usr/bin/pbcopy", [], input_text=self._saved, timeout_ms=2000,
                callback=self._on_clipboard_restored,
            )
        elif not self._restoring:
            self._complete(self._pending_text)

    def _on_clipboard_restored(self, success: bool, _stdout: str, stderr: str) -> None:
        if not success:
            logger.warning("Failed to restore clipboard content via pbcopy: %s", stderr)
        self._restoring = False
        self._complete(self._pending_text)

    def _start_command(self, program: str, arguments: list[str], *, timeout_ms: int,
                       callback, input_text: str | None = None) -> None:
        if self._terminal or self._process is not None:
            return
        process = QProcess(self)
        self._process = process
        self._command_callback = callback
        process.finished.connect(
            lambda exit_code, exit_status, process=process: self._finish_command(
                process,
                exit_status == QProcess.NormalExit and exit_code == 0,
            )
        )
        process.errorOccurred.connect(
            lambda _error, process=process: self._finish_command(process, False)
        )
        if input_text is not None:
            payload = input_text.encode("utf-8")

            def write_input() -> None:
                process.write(payload)
                process.closeWriteChannel()

            process.started.connect(write_input)
        self._command_timer.start(timeout_ms)
        process.start(program, arguments)

    def _on_command_timeout(self) -> None:
        process = self._process
        if process is None:
            return
        process.kill()
        self._finish_command(process, False)

    def _finish_command(self, process: QProcess, success: bool) -> None:
        if process is not self._process:
            return
        self._command_timer.stop()
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        callback = self._command_callback
        self._command_callback = None
        self._process = None
        process.deleteLater()
        if callback is not None:
            callback(success, stdout, stderr)

    def _complete(self, text: str) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._phase_timer.stop()
        self._command_timer.stop()
        self.completed.emit(text)
