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
from dataclasses import dataclass
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
_PBPASTE_TEXT_ARGS = ["-Prefer", "txt"]
_PLAIN_TEXT_TYPES = (
    "public.utf8-plain-text",
    "public.utf16-external-plain-text",
    "public.text",
    "NSStringPboardType",
)


class UnsupportedClipboardFormatError(RuntimeError):
    """The current clipboard cannot be snapshotted without losing a format."""


@dataclass(frozen=True)
class PasteboardSnapshot:
    change_count: int
    items: tuple[tuple[tuple[str, bytes], ...], ...]


class NativePasteboard:
    """Lossless snapshot/conditional restore for enumerable macOS pasteboard items."""

    def __init__(self) -> None:
        from AppKit import NSPasteboard

        self._pasteboard = NSPasteboard.generalPasteboard()

    def change_count(self) -> int:
        return int(self._pasteboard.changeCount())

    def read_plain_text(self) -> str:
        """Read the pasteboard's declared Unicode text without locale guessing."""
        for pasteboard_type in _PLAIN_TEXT_TYPES:
            try:
                value = self._pasteboard.stringForType_(pasteboard_type)
            except Exception:
                value = None
            if value is not None:
                return text_normalizer.normalize(str(value))
            try:
                data = self._pasteboard.dataForType_(pasteboard_type)
            except Exception:
                data = None
            if data is not None:
                decoded = _decode_text_output(bytes(data))
                if decoded:
                    return text_normalizer.normalize(decoded)
        return ""

    def snapshot(self) -> PasteboardSnapshot:
        count = self.change_count()
        captured: list[tuple[tuple[str, bytes], ...]] = []
        for item in self._pasteboard.pasteboardItems() or []:
            values: list[tuple[str, bytes]] = []
            for pasteboard_type in item.types() or []:
                data = item.dataForType_(pasteboard_type)
                if data is None:
                    raise UnsupportedClipboardFormatError(
                        f"剪贴板类型 {pasteboard_type} 无法完整读取。"
                    )
                values.append((str(pasteboard_type), bytes(data)))
            captured.append(tuple(values))
        return PasteboardSnapshot(count, tuple(captured))

    def restore(self, snapshot: PasteboardSnapshot, expected_change_count: int) -> bool:
        # A new user copy owns the clipboard and must never be overwritten.
        if self.change_count() != expected_change_count:
            return False
        from AppKit import NSPasteboardItem
        from Foundation import NSData

        items = []
        for values in snapshot.items:
            item = NSPasteboardItem.alloc().init()
            for pasteboard_type, payload in values:
                data = NSData.dataWithBytes_length_(payload, len(payload))
                if not item.setData_forType_(data, pasteboard_type):
                    raise UnsupportedClipboardFormatError(
                        f"剪贴板类型 {pasteboard_type} 无法完整恢复。"
                    )
            items.append(item)
        self._pasteboard.clearContents()
        if items and not self._pasteboard.writeObjects_(items):
            raise UnsupportedClipboardFormatError("剪贴板内容恢复失败。")
        return True


def _decode_text_output(payload: bytes) -> str:
    """Decode pasteboard text without replacing valid CJK characters."""
    if not payload:
        return ""
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", errors="replace")
    def quality(text: str) -> int:
        controls = sum(
            ord(char) < 32 and char not in "\t\r\n" for char in text
        )
        replacements = text.count("\ufffd")
        return controls * 4 + replacements * 8

    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = payload.decode("utf-8", errors="replace")

    # Some Cocoa clients expose plain text as UTF-16 without a BOM. UTF-16LE
    # can technically decode as UTF-8 while producing control characters, so
    # compare both candidates instead of accepting the first successful decode.
    if quality(decoded):
        candidates = [decoded]
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                candidates.append(payload.decode(encoding))
            except UnicodeDecodeError:
                continue
        decoded = min(candidates, key=quality)
    return decoded


def _read_clipboard() -> str:
    """通过 pbpaste 读取纯文本剪贴板内容。"""
    try:
        result = subprocess.run(
            ["pbpaste", *_PBPASTE_TEXT_ARGS], capture_output=True, timeout=2
        )
        return _decode_text_output(result.stdout)
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
    AppleScript and text reads are asynchronous. The original macOS pasteboard
    is captured with every enumerable item/type and restored only while the
    selection copy still owns the clipboard.
    """

    completed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None, *,
                 copy_action: Callable[[], bool] | None = None,
                 pasteboard=None) -> None:
        super().__init__(parent)
        self._copy_action = copy_action or _try_cmd_c_via_pynput
        self._pasteboard = pasteboard or NativePasteboard()
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
        self._snapshot: PasteboardSnapshot | None = None
        self._fallback_clipboard_text = ""
        self._fallback_change_count: int | None = None
        self._capture_change_count: int | None = None
        self._clipboard_modified = False
        self._used_fallback = False
        self._restoring = False
        self._pending_text = ""

    def start(self) -> None:
        if self._started or self._terminal:
            return
        self._started = True
        try:
            self._snapshot = self._pasteboard.snapshot()
        except Exception as exc:
            # Some clients (notably WeChat) publish private promised formats that
            # cannot be read back. Keep the readable plain text as a best-effort
            # fallback instead of dropping the entire selection capture.
            logger.warning(
                "Cannot preserve all clipboard formats; continuing with plain-text fallback: %s",
                exc,
            )
            self._snapshot = None
            try:
                self._fallback_change_count = self._pasteboard.change_count()
                reader = getattr(self._pasteboard, "read_plain_text", None)
                if callable(reader):
                    self._fallback_clipboard_text = reader() or ""
            except Exception:
                logger.warning("Cannot read plain-text clipboard fallback", exc_info=True)
        self._begin_copy()

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
            if (self._clipboard_modified and self._snapshot is not None
                    and self._capture_change_count is None):
                # The injected copy event may not have reached the pasteboard yet.
                self._phase_callback = self._capture_cancelled_change
                self._phase_timer.start(180)
            else:
                self._restore_and_complete()

    def _capture_cancelled_change(self) -> None:
        if self._snapshot is not None:
            current = self._pasteboard.change_count()
            if current != self._snapshot.change_count:
                self._capture_change_count = current
        self._restore_and_complete()

    def _begin_copy(self) -> None:
        if self._cancelled:
            self._complete("")
            return
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
        snapshot = self._snapshot
        current_count = self._pasteboard.change_count()
        baseline_count = (
            snapshot.change_count if snapshot is not None else self._fallback_change_count
        )
        if baseline_count is not None and current_count == baseline_count:
            if self._used_fallback:
                self._restore_and_complete()
            else:
                logger.info("pynput Cmd+C had no effect, trying osascript fallback...")
                self._run_osascript_copy()
            return
        self._capture_change_count = current_count
        if isinstance(self._pasteboard, NativePasteboard):
            selected = self._pasteboard.read_plain_text()
            if selected:
                self._restore_and_complete(selected)
                return
        self._start_command(
            "/usr/bin/pbpaste", _PBPASTE_TEXT_ARGS, timeout_ms=2000,
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
        if self._pasteboard.change_count() != self._capture_change_count:
            logger.info("Clipboard changed during capture; preserving the newer user content")
            self._complete("")
            return
        selected = stdout
        if not selected:
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
        if (self._clipboard_modified and self._snapshot is not None
                and self._capture_change_count is not None and not self._restoring):
            self._restoring = True
            try:
                restored = self._pasteboard.restore(
                    self._snapshot,
                    self._capture_change_count,
                )
                if not restored:
                    logger.info("Clipboard changed after selection; skipping restore")
            except Exception:
                logger.warning("Failed to restore clipboard formats", exc_info=True)
            self._restoring = False
            self._complete(self._pending_text)
        elif (
            self._clipboard_modified
            and self._snapshot is None
            and self._fallback_clipboard_text
            and self._capture_change_count is not None
            and not self._restoring
        ):
            # A private clipboard format could not be serialized. Restore the
            # readable text only, and only if the selection copy still owns the
            # pasteboard; never overwrite a newer user copy.
            self._restoring = True
            try:
                if self._pasteboard.change_count() == self._capture_change_count:
                    _write_clipboard(self._fallback_clipboard_text)
                else:
                    logger.info("Clipboard changed after partial capture; skipping text restore")
            finally:
                self._restoring = False
            self._complete(self._pending_text)
        elif not self._restoring:
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
        stdout = _decode_text_output(bytes(process.readAllStandardOutput()))
        stderr = _decode_text_output(bytes(process.readAllStandardError()))
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
