"""
AI 桌面助手 —— 主入口

单模式：悬浮按钮 + 全局快捷键 → Agent 多轮对话
  选中文字 → ⌘⌃L → 自动打开对话窗口并粘贴选中文字
"""
import functools
import html
import json
import logging
import os
import signal
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

from ai_desktop import __version__, config
from ai_desktop.agent_manager import AgentManager
from ai_desktop.capture.clipboard_monitor import SelectionCaptureTask
from ai_desktop.capture.hotkey_recovery import HotkeyRecovery
from ai_desktop.capture.screenshot import ScreenshotResult, ScreenshotStatus
from ai_desktop.config import Agent
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, ResultStatus, StreamEvent
from ai_desktop.llm.run_types import RunEventKind
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.llm.service_checks import (
    AsyncServiceChecks,
    ImageCapability,
    ModelCapabilityResult,
    ServiceCheckResult,
    ServiceState,
    model_cache_key,
    model_capability_cache_key,
    model_versions_cache_key,
    normalize_service_url,
)
from ai_desktop.llm.task_checks import TaskChecks
from ai_desktop.llm.thinking import ThinkMode, cache_thinking, load_cached_thinking
from ai_desktop.services.action_service import Action, ActionService
from ai_desktop.services.model_profiles import ModelProfile, ModelProfileManager
from ai_desktop.services.ocr_service import AsyncOCRService, OCRResult, OCRStatus
from ai_desktop.services.speech_service import SpeechService
from ai_desktop.services.task_admission import TaskAuthorization, TaskModelSettings
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.ui import styles
from ai_desktop.ui.agent_editor import AgentDef, AgentEditor
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.float_button import FloatButton, pin_to_all_spaces
from ai_desktop.ui.fluent import InfoBar, initialize, show_notice
from ai_desktop.ui.fluent import MessageBox as QMessageBox
from ai_desktop.ui.history_dialog import HistoryDialog
from ai_desktop.ui.menubar_icon import MenuBarIcon
from ai_desktop.ui.result_bubble import ResultBubble
from ai_desktop.ui.settings_dialog import SettingsDialog
from ai_desktop.ui.task_dialog import TaskDialog
from ai_desktop.utils import logging as log_util
from ai_desktop.utils.permissions import PermissionStatus
from ai_desktop.utils.storage import (
    Message,
    create_conversation,
    delete_conversation,
    delete_message,
    get_active_generation,
    get_conversation,
    get_generation,
    get_setting,
    init_db,
    list_conversations,
    list_generations,
    list_input_history,
    save_generation,
    save_message,
    save_setting,
    set_active_generation,
)

logger = logging.getLogger(__name__)


def _safe_slot(fn):
    """Decorator: catch all exceptions in Qt slots to prevent qFatal abort"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("Unhandled exception in %s", fn.__name__)
    return wrapper


# ═══════════════════════════════════════════════════════
# LLM Worker
# ═══════════════════════════════════════════════════════

class ScreenshotWorker(QThread):
    """截图 Worker：后台运行 screencapture

    - completed(result): 返回可区分成功、取消、权限、超时及命令错误的结果
    """
    completed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    def run(self) -> None:
        from ai_desktop.capture.screenshot import capture_region_result
        if self._cancelled.is_set():
            return
        try:
            result = capture_region_result(self._cancelled.is_set)
        except Exception:
            logger.exception("Screenshot capture error")
            result = ScreenshotResult(ScreenshotStatus.COMMAND_FAILED, error="截图任务异常。")
        if self._cancelled.is_set():
            if result.path:
                Path(result.path).unlink(missing_ok=True)
            return
        self.completed.emit(result)


# ═══════════════════════════════════════════════════════
# ChatController —— 悬浮按钮 + 快捷键 → 多轮对话
# ═══════════════════════════════════════════════════════

class ChatController(QObject):
    """快捷键触发 → 读选中文字 → 打开对话窗口粘贴 → 用户按 Enter 发送"""

    # pynput 回调在后台线程，通过信号桥接到主线程
    _hotkey_triggered = pyqtSignal(str)
    _screenshot_hotkey_triggered = pyqtSignal()
    shutdown_started = pyqtSignal()
    exit_ready = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        init_db()  # 确保表结构存在，必须在任何 DB 查询之前

        # 加载持久化配置
        self._settings = SettingsManager()
        self._settings.load()
        self._profile_mgr = ModelProfileManager()
        self._action_service = ActionService()

        # 加载 Agent 列表
        self._agent_mgr = AgentManager()
        self._all_agents = self._agent_mgr.all_agents
        self._active_agent = self._agent_mgr.active_agent
        self._custom_agents = self._agent_mgr.custom_agents

        # 读取上次使用的模型
        saved_model = get_setting("last_model")

        self._model: str = saved_model or config.OLLAMA_MODEL
        self._auto_hide: bool = get_setting("auto_hide") == "true"  # 默认不收起
        saved_action_mode = get_setting("action_conversation_mode")
        self._action_mode = (
            saved_action_mode
            if saved_action_mode in {"new", "current"}
            else "new"
        )

        self._convo_id: int = 0
        self._messages: list[Message] = []
        self._restore_last: bool = True  # 首次打开自动恢复上次对话
        self._worker: Optional[RunWorker] = None
        self._task_authorization: TaskAuthorization | None = None
        self._task_epoch = 0
        self._pending_task = None
        self._model_prepare_timer = QTimer(self)
        self._model_prepare_timer.setSingleShot(True)
        self._model_prepare_timer.timeout.connect(lambda: self._resume_pending_model(timed_out=True))
        self._task_checks = TaskChecks(self)
        self._task_checks.completed.connect(self._on_task_checked)
        self._regenerating_user_id: int = 0
        self._stale_workers: list[RunWorker] = []
        self._response_text = ""
        self._dialog: Optional[ChatDialog] = None
        self._chat_geometry = get_setting("chat_window_geometry")

        self._window_state_timer = QTimer(self)
        self._window_state_timer.setSingleShot(True)
        self._window_state_timer.setInterval(500)
        self._window_state_timer.timeout.connect(self._save_window_state)
        self._screen_recovery_timer = QTimer(self)
        self._screen_recovery_timer.setSingleShot(True)
        self._screen_recovery_timer.setInterval(100)
        self._screen_recovery_timer.timeout.connect(self._ensure_windows_visible)

        # 悬浮按钮
        self.float_btn = FloatButton(
            pet_enabled=config.DESKTOP_PET_ENABLED,
            reduce_motion=config.DESKTOP_PET_REDUCE_MOTION,
            pet_size=config.DESKTOP_PET_SIZE,
        )
        self._result_bubble = ResultBubble()
        self.float_btn.restore_placement(get_setting("float_button_placement"))
        self.float_btn.clicked.connect(self._toggle_dialog)
        self.float_btn.exit_requested.connect(self._on_exit)
        self.float_btn.hide_requested.connect(self._hide_float_entry)
        self.float_btn.about_requested.connect(self._show_about)
        self.float_btn.settings_requested.connect(self._on_settings_requested)
        self.float_btn.auto_hide_toggled.connect(self._on_auto_hide_toggled)
        self.float_btn.pet_mode_toggled.connect(self._on_pet_mode_toggled)
        self.float_btn.quick_action_requested.connect(self._on_pet_action_requested)
        self.float_btn.read_selection_requested.connect(self._on_read_selection_requested)
        self.float_btn.stop_speech_requested.connect(self._on_stop_speech_requested)
        self.float_btn.screenshot_requested.connect(self._on_screenshot_hotkey)
        self.float_btn.placement_changed.connect(self._schedule_window_state_save)
        self.float_btn.placement_changed.connect(self._reposition_result_bubble)
        self.float_btn.screen_follow_changed.connect(self._on_screen_follow_changed)
        self.float_btn.set_follow_cursor_screen(
            get_setting("float_follow_cursor_screen") != "false"
        )
        self.float_btn.set_auto_hide_state(self._auto_hide)
        self._result_bubble.activated.connect(self._show_dialog)
        self._refresh_pet_actions()
        self._connect_screen_signals()

        # 菜单栏图标
        self._tray = MenuBarIcon(self._all_agents, self._active_agent)
        self._tray.dialog_toggle.connect(self._toggle_dialog)
        self._tray.float_entry_toggle.connect(self._on_float_entry_toggle)
        self._tray.agent_selected.connect(self._on_tray_agent)
        self._tray.settings_clicked.connect(self._on_settings_requested)
        self._tray.about_clicked.connect(self._show_about)
        self._tray.exit_clicked.connect(self._on_exit)

        # 全局快捷键：选中文字 → ⌘⌃L → 提问
        self.hotkey = self._create_hotkey_backend()
        self.hotkey.register(config.HOTKEY, self._on_global_hotkey)
        self._hotkey_triggered.connect(self._on_hotkey_triggered)
        self._selection_delay_timer = QTimer(self)
        self._selection_delay_timer.setSingleShot(True)
        self._selection_delay_timer.timeout.connect(self._start_selection_capture)
        self._selection_capture: SelectionCaptureTask | None = None
        self._pending_pet_action_id: str | None = None
        self._pending_read_selection = False

        # 全局快捷键：⌘⌃S → 截图并附加到对话
        self.hotkey_img = self._create_hotkey_backend()
        self.hotkey_img.register(config.SCREENSHOT_HOTKEY, self._on_global_screenshot_hotkey)
        self._screenshot_hotkey_triggered.connect(self._on_screenshot_hotkey)
        self._screenshot_worker: Optional[ScreenshotWorker] = None
        self._ocr = AsyncOCRService(self)
        self._ocr.completed.connect(self._on_ocr_completed)
        self._ocr_image_path: str | None = None
        self._speech = SpeechService(self)
        self._speech.completed.connect(self._on_speech_completed)
        self._speech.progress.connect(self._on_speech_progress)
        self._speech_feedback_active = False
        self._shutdown_workers: list[QThread] = []
        self._stopping = False
        self._stopped = False
        self._hotkey_recovery: HotkeyRecovery | None = None
        self._service_checks = AsyncServiceChecks(self)
        self._service_checks.service_checked.connect(self._on_service_checked)
        self._service_checks.model_capability_checked.connect(
            self._on_model_capability_checked
        )
        self._service_checks.update_checked.connect(self._on_update_checked)
        self._service_check_sequence = 0
        self._service_check_url = normalize_service_url(config.OLLAMA_BASE_URL)
        self._service_state = ServiceState.CHECKING
        self._model_versions = self._load_cached_model_versions(
            config.OLLAMA_BASE_URL
        )
        self._image_capability = self._load_cached_image_capability(
            config.OLLAMA_BASE_URL,
            self._model,
            self._model_versions.get(self._model, ""),
        )
        self._capability_check: tuple[int, str, str, str] | None = None
        self._startup_service_check: tuple[int, str] | None = None
        self._notices: list[InfoBar] = []

    @_safe_slot
    def refresh_theme(self, _palette=None) -> None:
        """Refresh every live themed surface after a system palette change."""
        refreshed = styles.refresh_all()
        self._tray.refresh_theme()
        if self._dialog is not None:
            self._dialog.refresh_theme()
        self._result_bubble.refresh_theme()
        logger.info("Theme refreshed (%d widget styles updated)", refreshed)

    @staticmethod
    def _create_hotkey_backend():
        """返回全局快捷键后端（始终使用 NSEvent，避免 pynput 后台线程触发主队列断言崩溃）"""
        from ai_desktop.capture.nsevent_monitor import NSEventMonitor
        logger.info("Using NSEventMonitor hotkey backend")
        return NSEventMonitor()

    def start(self) -> None:
        if self._stopping or self._stopped:
            return
        init_db()
        if os.environ.get("AIDE_SMOKE_TEST") != "1":
            if self._hotkey_recovery is None:
                self._hotkey_recovery = HotkeyRecovery(
                    {'selection': self.hotkey, 'screenshot': self.hotkey_img}, self)
                self._hotkey_recovery.environment_restored.connect(self._schedule_screen_recovery)
            self._hotkey_recovery.start()
        self.float_btn.show()
        pin_to_all_spaces(self.float_btn)
        self._tray.set_float_entry_visible(True, self.float_btn.pet_enabled)
        self._tray.show()
        logger.info("ChatController 已就绪（快捷键 %s / 截图 %s）", config.HOTKEY, config.SCREENSHOT_HOTKEY)

    def stop(self) -> None:
        """Begin non-blocking shutdown and emit exit_ready after all tasks finish."""
        if self._stopped:
            return
        if self._stopping:
            self._finish_stop_if_ready()
            return
        self._stopping = True
        if self._hotkey_recovery is not None:
            self._hotkey_recovery.stop()
        self.shutdown_started.emit()
        self._window_state_timer.stop()
        self._screen_recovery_timer.stop()
        self._save_window_state()
        self._tray.hide()
        self.hotkey.stop()
        self.hotkey_img.stop()
        self.float_btn.hide()
        self._result_bubble.hide()
        self._startup_service_check = None
        self._service_checks.cancel_all()
        for notice in list(self._notices):
            notice.close()
        self._selection_delay_timer.stop()
        self._pending_pet_action_id = None
        for worker in self._speech.shutdown():
            self._track_shutdown_worker(worker)
        if self._selection_capture is not None:
            self._selection_capture.cancel()
        self._stop_worker(show_cancelled=False)
        if self._screenshot_worker is not None:
            worker = self._screenshot_worker
            if worker.isFinished():
                self._screenshot_worker = None
                worker.deleteLater()
            else:
                worker.cancel()
        self._ocr_image_path = None
        for worker in self._ocr.take_shutdown_workers():
            self._track_shutdown_worker(worker)
        if self._dialog:
            self._dialog.hide()
            for worker in self._dialog.take_shutdown_workers():
                self._track_shutdown_worker(worker)
        self._finish_stop_if_ready()

    def _track_shutdown_worker(self, worker: QThread) -> None:
        if worker not in self._shutdown_workers:
            self._shutdown_workers.append(worker)
            worker.finished.connect(self._on_shutdown_worker_finished)
        # Close the race where the thread finishes just before or during connect().
        if worker.isFinished() and worker in self._shutdown_workers:
            self._shutdown_workers.remove(worker)

    def _on_shutdown_worker_finished(self) -> None:
        worker = self.sender()
        if worker in self._shutdown_workers:
            self._shutdown_workers.remove(worker)
        self._finish_stop_if_ready()

    def _finish_stop_if_ready(self) -> None:
        if (not self._stopping or self._worker is not None or self._stale_workers
                or self._selection_capture is not None):
            return
        if self._screenshot_worker is not None or self._shutdown_workers:
            return
        if self._dialog:
            self._dialog.deleteLater()
            self._dialog = None
        self._result_bubble.deleteLater()
        self._stopped = True
        logger.info("ChatController 已退出")
        self.exit_ready.emit()

    # ── 快捷键 ─────────────────────────────────────────

    def _on_global_hotkey(self) -> None:
        """热键回调：发射信号到主线程（pynput 后台线程 / NSEvent 主线程均适用）"""
        self._hotkey_triggered.emit("")

    def _on_global_screenshot_hotkey(self) -> None:
        """Bridge a native/global hotkey callback to the controller's Qt thread."""
        logger.debug("Screenshot global hotkey callback fired")
        self._screenshot_hotkey_triggered.emit()

    def _on_screenshot_hotkey(self) -> None:
        """截图热键回调：后台启动框选截图，完成后附加到对话窗口"""
        if self._stopping or self._stopped:
            logger.debug("Screenshot hotkey ignored (stopping/stopped)")
            return
        if self._screenshot_worker is not None:
            if self._screenshot_worker.isFinished():
                logger.warning(
                    "Screenshot worker finished but not cleaned up, resetting"
                )
                worker = self._screenshot_worker
                self._screenshot_worker = None
                worker.deleteLater()
            else:
                logger.debug("Screenshot hotkey ignored (worker busy)")
                return
        self.float_btn.set_listening(True)
        self._screenshot_worker = ScreenshotWorker(self)
        self._screenshot_worker.completed.connect(self._on_screenshot_result)
        self._screenshot_worker.finished.connect(self._on_screenshot_finished)
        self._screenshot_worker.start()

    @_safe_slot
    def _on_screenshot_result(self, result: ScreenshotResult) -> None:
        if self._stopping:
            if result.path:
                Path(result.path).unlink(missing_ok=True)
            return
        if result.status == ScreenshotStatus.CANCELLED:
            logger.info("Screenshot cancelled by user")
            return
        if result.ok:
            try:
                if not self._dialog or not self._dialog.isVisible():
                    self._show_dialog()
                else:
                    self._dialog.activateWindow()
                    self._dialog.raise_()
                if self._dialog:
                    self._dialog.attach_image_paths([result.path])
            finally:
                Path(result.path).unlink(missing_ok=True)
            return
        logger.warning("Screenshot failed (%s): %s", result.status.value, result.error)
        if result.status == ScreenshotStatus.TIMED_OUT:
            self._show_notice(
                QMessageBox.Warning, "截图超时",
                result.error or "截图长时间未完成，请重试。",
            )
            return
        if result.status == ScreenshotStatus.COMMAND_FAILED:
            detail = html.escape(result.error or "未知命令错误")
            self._show_notice(
                QMessageBox.Warning, "截图失败",
                f"系统截图命令执行失败。<br><br><tt>{detail}</tt>",
            )
            return

        # Only an explicit permission result offers the system settings action.
        box = QMessageBox(
            QMessageBox.Warning,
            "需要屏幕录制权限",
            "无法进行截图：屏幕录制权限未授予。\n\n"
            "请前往 系统设置 → 隐私与安全性 → 屏幕录制，\n"
            "勾选允许「AI 桌面助手」（开发模式为「终端」），授权后重新截图。",
            QMessageBox.Ok,
        )
        settings_btn = box.addButton("打开系统设置", QMessageBox.ActionRole)
        box.exec_()
        if box.clickedButton() is settings_btn:
            self._open_screen_recording_prefs()

    def _on_screenshot_finished(self) -> None:
        worker = self.sender()
        if worker is self._screenshot_worker:
            self._screenshot_worker = None
        self.float_btn.set_listening(False)
        worker.deleteLater()
        self._finish_stop_if_ready()

    def _on_ocr_requested(self, image_path: str) -> None:
        if self._stopping or self._stopped or self._dialog is None:
            return
        if image_path not in self._dialog.get_pending_images():
            return
        self._ocr_image_path = image_path
        self._dialog.show_ocr_loading(image_path)
        self._ocr.start(image_path)

    @_safe_slot
    def _on_ocr_completed(self, result: OCRResult) -> None:
        if self._stopping or self._dialog is None:
            return
        if result.image_path != self._ocr_image_path:
            return
        if result.image_path not in self._dialog.get_pending_images():
            return
        self._ocr_image_path = None
        if result.status == OCRStatus.SUCCEEDED:
            low_confidence = any(
                block.confidence < 0.5 for block in result.blocks
            )
            self._dialog.show_ocr_result(
                result.image_path,
                result.text,
                block_count=len(result.blocks),
                elapsed_ms=result.elapsed_ms,
                languages=result.languages,
                low_confidence=low_confidence,
            )
        elif result.status == OCRStatus.EMPTY:
            self._dialog.show_ocr_empty(result.image_path, result.elapsed_ms)
        elif result.status == OCRStatus.FAILED:
            self._dialog.show_ocr_error(result.image_path, result.error)

    def _on_ocr_cancel_requested(self) -> None:
        self._ocr_image_path = None
        self._ocr.cancel()

    def _on_pending_images_changed(self, image_paths: list[str]) -> None:
        image_path = self._ocr_image_path
        if image_path is None or image_path in image_paths:
            return
        self._ocr_image_path = None
        self._ocr.cancel()
        if self._dialog:
            self._dialog.close_ocr_preview(image_path)

    def _on_dialog_closed(self) -> None:
        self._cancel_pending_task()
        self._on_ocr_cancel_requested()

    @staticmethod
    def _open_screen_recording_prefs() -> None:
        import subprocess
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]
        )

    def _on_hotkey_triggered(self, _text: str) -> None:
        """Coalesce triggers; the capture task waits for actual modifier release."""
        if (not self._stopping and not self._stopped and self._selection_capture is None
                and not self._selection_delay_timer.isActive()):
            self._selection_delay_timer.start(100)

    @_safe_slot
    def _start_selection_capture(self) -> None:
        if self._stopping or self._stopped:
            return
        if self._selection_capture is not None:
            return
        self.float_btn.set_listening(True)
        task = SelectionCaptureTask(self)
        self._selection_capture = task
        task.completed.connect(lambda text, task=task: self._on_selection_captured(task, text))
        task.start()

    @_safe_slot
    def _on_pet_action_requested(self, action_id: str) -> None:
        if self._stopping or self._stopped:
            return
        action = self._action_service.get(action_id)
        if (
            not config.QUICK_ACTIONS_ENABLED
            or action is None
            or not action.enabled
            or "text" not in action.input_types
        ):
            self._refresh_pet_actions()
            self._show_dialog()
            self._show_notice(
                QMessageBox.Warning,
                "快捷动作不可用",
                "该动作已隐藏、被禁用或不支持文字选区。",
            )
            return
        if self._worker is not None or self._selection_capture is not None:
            self._show_dialog()
            if self._dialog:
                self._dialog.flash_busy()
            return
        self._pending_pet_action_id = action_id
        self._start_selection_capture()

    @_safe_slot
    def _on_read_selection_requested(self, text: str = "") -> None:
        """朗读选区入口，统一接收应用内和其他应用的选中文本。"""
        if self._stopping or self._stopped:
            return
        if text.strip():
            logger.info("Read-selection requested, text length=%d", len(text))
            self.float_btn.set_speaking(True)
            self._speech.speak(text)
            return
        if self._worker is not None or self._selection_capture is not None:
            self._show_dialog()
            if self._dialog:
                self._dialog.flash_busy()
            return
        self._pending_read_selection = True
        self._start_selection_capture()

    @_safe_slot
    def _on_speech_progress(self, message: str) -> None:
        logger.info("Speech: %s", message)
        self.float_btn.set_speaking(True)
        title = "正在朗读" if message == "正在朗读…" else "准备朗读"
        self._show_speech_feedback("progress", title, message, timeout_ms=60000)

    @_safe_slot
    def _on_stop_speech_requested(self) -> None:
        self._speech.stop()
        self.float_btn.set_speaking(False)
        if self._speech_feedback_active:
            self._result_bubble.dismiss()
            self._speech_feedback_active = False

    @_safe_slot
    def _on_speech_completed(self, success: bool, error: str) -> None:
        self.float_btn.set_listening(False)
        self.float_btn.set_speaking(False)
        if self._stopping or self._stopped:
            return
        if success:
            if self._speech_feedback_active:
                self._result_bubble.dismiss()
                self._speech_feedback_active = False
            self.float_btn.show_result(True)
            return
        if error == "朗读已停止。":
            if self._speech_feedback_active:
                self._result_bubble.dismiss()
                self._speech_feedback_active = False
            return
        if not self._show_speech_feedback(
            "error", "无法朗读", error, timeout_ms=7000
        ):
            self._tray.showMessage(
                "朗读选区",
                error,
                QSystemTrayIcon.Warning,
                5000,
            )

    def _show_speech_feedback(
        self,
        kind: str,
        title: str,
        summary: str,
        *,
        timeout_ms: int,
    ) -> bool:
        if not self.float_btn.isVisible() or not self.float_btn.pet_enabled:
            return False
        self._speech_feedback_active = True
        self._result_bubble.show_result(
            kind,
            title,
            summary,
            self.float_btn.frameGeometry(),
            timeout_ms=timeout_ms,
            activate_on_click=False,
        )
        pin_to_all_spaces(self._result_bubble)
        return True

    @_safe_slot
    def _on_selection_captured(self, task: SelectionCaptureTask, text: str) -> None:
        if task is not self._selection_capture:
            task.deleteLater()
            return
        self._selection_capture = None
        action_id = self._pending_pet_action_id
        self._pending_pet_action_id = None
        read_selection = self._pending_read_selection
        self._pending_read_selection = False
        self.float_btn.set_listening(False)
        failure_reason = getattr(task, 'failure_reason', '')
        task.deleteLater()
        if self._stopping or self._stopped:
            self._finish_stop_if_ready()
            return
        if not text and failure_reason in {'modifiers_held', 'modifier_state_unavailable'}:
            message = ('请松开 Command、Control、Option 和 Shift 后，再触发选区操作。'
                       if failure_reason == 'modifiers_held' else '暂时无法读取键盘状态，请重试选区操作。')
            self.float_btn.show_result(False)
            if not self._show_speech_feedback('error', '选区读取', message, timeout_ms=7000):
                self._tray.showMessage('选区读取', message, QSystemTrayIcon.Warning, 5000)
            self._finish_stop_if_ready()
            return
        if read_selection:
            logger.info("Read-selection captured text length=%d", len(text))
            if text.strip():
                self._on_read_selection_requested(text)
            else:
                message = "没有读取到选中文字，请重新框选后再试。"
                self.float_btn.show_result(False)
                if not self._show_speech_feedback(
                    "error", "朗读选区", message, timeout_ms=7000
                ):
                    self._tray.showMessage(
                        "朗读选区", message, QSystemTrayIcon.Warning, 5000
                    )
            self._finish_stop_if_ready()
            return
        if action_id:
            logger.info("Pet action %s captured text length=%d", action_id, len(text))
            if text.strip():
                self._on_action_requested(action_id, text, "new")
            else:
                self._show_dialog()
                if self._dialog:
                    self._dialog.show_actions("", action_id)
            self._finish_stop_if_ready()
            return
        logger.info("Hotkey triggered, text length=%d", len(text))
        if not self._dialog or not self._dialog.isVisible():
            self._show_dialog()
        if text and self._dialog:
            self._dialog.show()
            self._dialog.activateWindow()
            self._dialog.raise_()
            self._dialog.set_input_text(text)
            self._dialog.show_actions(text)
        elif not text:
            logger.info("No text captured — dialog shown without paste")
        self._finish_stop_if_ready()

    # ── 对话框开关 ─────────────────────────────────────

    def _toggle_dialog(self) -> None:
        if self._stopping or self._stopped:
            return
        if self._dialog and self._dialog.isVisible():
            self._dialog.hide()
            return
        self._show_dialog()

    def _show_dialog(self) -> None:
        if self._stopping or self._stopped:
            return
        self._result_bubble.hide()
        if self._dialog is None:
            cached_models = self._load_cached_models(config.OLLAMA_BASE_URL)
            self._dialog = ChatDialog(
                self._all_agents,
                self._active_agent,
                cached_models,
                self._model,
                auto_hide=self._auto_hide,
                actions=self._action_service.visible_actions,
                actions_enabled=config.QUICK_ACTIONS_ENABLED,
            )
            self._sync_action_context()
            self._dialog.restore_geometry(self._chat_geometry)
            self._dialog.geometry_changed.connect(self._schedule_window_state_save)
            self._dialog.message_sent.connect(self._on_user_message)
            self._dialog.screenshot_requested.connect(self._on_screenshot_hotkey)
            self._dialog.read_selection_requested.connect(self._on_read_selection_requested)
            self._dialog.ocr_requested.connect(self._on_ocr_requested)
            self._dialog.ocr_cancel_requested.connect(self._on_ocr_cancel_requested)
            self._dialog.pending_images_changed.connect(
                self._on_pending_images_changed
            )
            self._dialog.closed.connect(self._on_dialog_closed)
            self._dialog.new_convo_requested.connect(self._new_conversation)
            self._dialog.history_requested.connect(self._on_history_requested)
            self._dialog.export_requested.connect(self._on_export_requested)
            self._dialog.run_history_requested.connect(self._on_run_history_requested)
            self._dialog.task_settings_requested.connect(self._on_task_settings_requested)
            self._dialog.manage_agents_requested.connect(self._on_manage_agents)
            self._dialog.stop_requested.connect(self._on_stop_requested)
            self._dialog.command_decided.connect(self._on_command_decided)
            self._dialog.tool_stop_requested.connect(self._on_tool_stop_requested)
            self._dialog.agent_changed.connect(self._on_agent_changed)
            self._dialog.model_changed.connect(self._on_model_changed)
            self._dialog.service_check_requested.connect(self._refresh_model_list)
            self._dialog.action_requested.connect(self._on_action_requested)
            self._dialog.regenerate_requested.connect(self._on_regenerate_requested)
            self._dialog.generation_selected.connect(self._on_generation_selected)
            self._dialog.set_cached_models(cached_models, self._model)
            self._dialog.set_image_capability(
                self._image_capability,
                cached=self._image_capability != ImageCapability.UNKNOWN,
            )
            self._update_model_profile_summary()
            # 灌入输入历史（上下键浏览用）
            self._dialog.set_input_history(list_input_history())
            # 首次打开自动恢复上次对话
            if self._restore_last:
                self._restore_last = False
                convs = list_conversations(limit=1)
                if convs:
                    prev_agent = self._active_agent
                    self._on_conversation_selected(convs[0].id)
                    # 不覆盖用户手动选择的 Agent
                    if self._active_agent != prev_agent:
                        self._active_agent = self._agent_mgr.switch(prev_agent)
                        self._dialog.set_active_agent(self._active_agent)
                        self._tray.set_active_agent(self._active_agent)
                        self._update_model_profile_summary()
        # 如果悬浮球被隐藏了，重新显示
        if self.float_btn.isHidden():
            self.float_btn.show()
            pin_to_all_spaces(self.float_btn)
        self._dialog.show_near(
            self.float_btn.mapToGlobal(self.float_btn.rect().topLeft())
        )

    def _schedule_window_state_save(self) -> None:
        if not self._stopping and not self._stopped:
            self._window_state_timer.start()

    def _connect_screen_signals(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._schedule_screen_recovery)
        for screen in app.screens():
            screen.availableGeometryChanged.connect(self._schedule_screen_recovery)

    def _on_screen_added(self, screen) -> None:
        screen.availableGeometryChanged.connect(self._schedule_screen_recovery)
        self._schedule_screen_recovery()

    def _schedule_screen_recovery(self, *_) -> None:
        if not self._stopping and not self._stopped:
            self._screen_recovery_timer.start()

    def _ensure_windows_visible(self) -> None:
        self.float_btn.ensure_visible()
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.ensure_visible()
        self._reposition_result_bubble()

    def _reposition_result_bubble(self) -> None:
        if self._result_bubble.isVisible():
            self._result_bubble.position_near(self.float_btn.frameGeometry())

    def _hide_float_entry(self) -> None:
        self._result_bubble.hide()
        self.float_btn.hide()
        self._tray.set_float_entry_visible(False, self.float_btn.pet_enabled)

    @_safe_slot
    def _on_float_entry_toggle(self) -> None:
        """通过菜单栏图标恢复或隐藏悬浮入口。"""
        if self.float_btn.isVisible():
            self._hide_float_entry()
            return
        self.float_btn.show()
        pin_to_all_spaces(self.float_btn)
        self._tray.set_float_entry_visible(True, self.float_btn.pet_enabled)
        self._schedule_screen_recovery()

    def _save_window_state(self) -> None:
        float_state = self.float_btn.placement_state()
        if isinstance(float_state, str):
            save_setting("float_button_placement", float_state)
        if self._dialog is not None:
            chat_state = self._dialog.geometry_state()
            if isinstance(chat_state, str):
                self._chat_geometry = chat_state
                save_setting("chat_window_geometry", chat_state)

    @_safe_slot
    def _on_auto_hide_toggled(self, checked: bool) -> None:
        self._auto_hide = checked
        save_setting("auto_hide", "true" if checked else "false")
        self.float_btn.set_auto_hide_state(checked)
        if self._dialog:
            self._dialog.set_auto_hide(checked)
        logger.info("Auto-hide %s", "enabled" if checked else "disabled")

    @_safe_slot
    def _on_screen_follow_changed(self, enabled: bool) -> None:
        save_setting("float_follow_cursor_screen", "true" if enabled else "false")

    @_safe_slot
    def _on_pet_mode_toggled(self, checked: bool) -> None:
        config.DESKTOP_PET_ENABLED = checked
        save_setting("desktop_pet_enabled", "true" if checked else "false")
        self.float_btn.set_pet_enabled(checked)
        self._tray.set_float_entry_visible(self.float_btn.isVisible(), checked)
        self._refresh_pet_actions()
        if not checked:
            self._result_bubble.hide()
        logger.info("Desktop pet mode %s", "enabled" if checked else "disabled")

    # ── 退出 / 关于 ───────────────────────────────────

    def _on_exit(self) -> None:
        self.stop()

    @_safe_slot
    def _on_settings_requested(self) -> None:
        """打开设置面板"""
        current = self._settings.current()
        dlg = SettingsDialog(current, parent=self._dialog, model=self._model,
                             model_version=self._model_versions.get(self._model, ""))
        dlg.settings_applied.connect(self._on_settings_applied)
        dlg.exec_()

    @_safe_slot
    def _on_settings_applied(self, data: dict) -> None:
        """应用设置变更"""
        changed = self._settings.apply(data)
        if changed:
            self._cancel_pending_model()
        if set(changed) & {'timeout', 'num_ctx', 'num_predict', 'temperature', 'top_p', 'top_k',
                           'repeat_penalty', 'max_rounds', 'think'}:
            # Preserve the tool grant, but never finish discovery against stale parameters.
            self._cancel_pending_task()
        if set(changed) & {'base_url', 'execution_workspace', 'bash_policy', 'execution_path', 'task_tools_enabled',
                           'task_max_model_rounds', 'task_max_tool_calls', 'task_max_search_calls',
                           'search_provider', 'search_max_results', 'search_timeout', 'search_parallel_mode'}:
            self._clear_task_authorization()
        if "hotkey" in changed:
            try:
                self.hotkey.reregister(config.HOTKEY, self._on_global_hotkey)
                logger.info("Hotkey changed to %s", config.HOTKEY)
            except Exception as e:
                logger.warning("Failed to change hotkey: %s", e)
        if "quick_actions" in changed and self._dialog:
            self._dialog.set_actions_enabled(config.QUICK_ACTIONS_ENABLED)
        if "quick_actions" in changed:
            self._refresh_pet_actions()
        if "desktop_pet" in changed:
            self.float_btn.set_pet_enabled(config.DESKTOP_PET_ENABLED)
            self._tray.set_float_entry_visible(
                self.float_btn.isVisible(), self.float_btn.pet_enabled
            )
            self._refresh_pet_actions()
            if not config.DESKTOP_PET_ENABLED:
                self._result_bubble.hide()
        if "pet_reduce_motion" in changed:
            self.float_btn.set_reduce_motion(config.DESKTOP_PET_REDUCE_MOTION)
        if "pet_size" in changed:
            self.float_btn.set_pet_size(config.DESKTOP_PET_SIZE)
        if "pet_source" in changed or "pet_name" in changed:
            self.float_btn.reload_pet()
        if "base_url" in changed:
            self._startup_service_check = None
            self._service_checks.cancel_service()
            self._service_checks.cancel_model_capability()
            self._capability_check = None
            self._model_versions = self._load_cached_model_versions(
                config.OLLAMA_BASE_URL
            )
            self._sync_cached_image_capability()
            if self._dialog:
                cached_models = self._load_cached_models(config.OLLAMA_BASE_URL)
                self._dialog.set_cached_models(cached_models, self._model)
            self._refresh_model_list()
        if changed:
            self._update_model_profile_summary()
            logger.info("Settings applied")

    def _show_about(self) -> None:
        QMessageBox.about(
            None,
            "关于 AI 桌面助手",
            f"<b>AI 桌面助手</b> v{__version__}<br><br>"
            "macOS 常驻 AI 助手<br>"
            "选中文字 → ⌘⌃L → 一键提问<br><br>"
            "基于 Ollama 本地 LLM，数据不上传。",
        )

    # ── Agent 切换 ─────────────────────────────────────

    @_safe_slot
    def _on_agent_changed(self, agent: Agent) -> None:
        self._clear_task_authorization(cancel_running=True)
        self._active_agent = self._agent_mgr.switch(agent)
        self._sync_action_context()
        self._tray.set_active_agent(self._active_agent)
        self._update_model_profile_summary()
        logger.info("Agent switched: %s", self._active_agent.name)

    def _sync_active_agent_from_dialog(self) -> Agent:
        """Use the visible Agent as the authority at a user request boundary."""
        if self._dialog is None:
            return self._active_agent
        visible_agent = self._dialog.active_agent
        if visible_agent.id == self._active_agent.id:
            return self._active_agent
        logger.warning(
            "Agent state mismatch (controller=%s, dialog=%s); using visible selection",
            self._active_agent.id,
            visible_agent.id,
        )
        self._clear_task_authorization(cancel_running=True)
        self._active_agent = self._agent_mgr.switch(visible_agent)
        self._tray.set_active_agent(self._active_agent)
        self._update_model_profile_summary()
        return self._active_agent

    @_safe_slot
    def _on_tray_agent(self, agent: Agent) -> None:
        """菜单栏切换 Agent"""
        self._clear_task_authorization(cancel_running=True)
        self._active_agent = self._agent_mgr.switch(agent)
        if self._dialog:
            self._dialog.set_active_agent(self._active_agent)
            self._sync_action_context()
        self._tray.set_active_agent(self._active_agent)
        self._update_model_profile_summary()
        logger.info("Agent switched via tray: %s", self._active_agent.name)

    def _on_model_changed(self, model: str) -> None:
        if model != self._model:
            if self._worker is not None and self._worker.context.tools:
                self._stop_worker()
            self._cancel_pending_task()
            self._task_epoch += 1
        self._cancel_pending_model()
        self._model = model
        save_setting("last_model", model)
        self._service_checks.cancel_model_capability()
        self._capability_check = None
        self._sync_cached_image_capability()
        if self._service_state == ServiceState.ONLINE:
            self._refresh_model_capability()
        self._update_model_profile_summary()
        self._sync_action_context()
        logger.info("Model switched: %s", model)

    @_safe_slot
    def _refresh_model_list(self) -> int:
        """Start or join the current address's non-blocking model check."""
        if self._stopping or self._stopped:
            return 0
        base_url = normalize_service_url(config.OLLAMA_BASE_URL)
        self._service_state = ServiceState.CHECKING
        self._service_checks.cancel_model_capability()
        self._capability_check = None
        self._sync_cached_image_capability()
        if self._dialog:
            self._dialog.set_service_status(ServiceState.CHECKING)
        sequence = self._service_checks.check_service(base_url)
        self._service_check_sequence = sequence
        self._service_check_url = base_url
        return sequence

    @staticmethod
    def _load_cached_models(base_url: str) -> list[str]:
        raw = get_setting(model_cache_key(base_url))
        if not raw:
            return []
        try:
            models = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(models, list):
            return []
        return list(dict.fromkeys(model for model in models if isinstance(model, str) and model))

    @staticmethod
    def _load_cached_model_versions(base_url: str) -> dict[str, str]:
        raw = get_setting(model_versions_cache_key(base_url))
        if not raw:
            return {}
        try:
            versions = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(versions, dict):
            return {}
        return {
            model: version
            for model, version in versions.items()
            if isinstance(model, str) and isinstance(version, str) and model
        }

    @staticmethod
    def _load_cached_image_capability(
        base_url: str, model: str, version: str,
    ) -> ImageCapability:
        raw = get_setting(model_capability_cache_key(base_url, model, version))
        try:
            return ImageCapability(raw)
        except ValueError:
            return ImageCapability.UNKNOWN

    def _sync_cached_image_capability(self) -> None:
        version = self._model_versions.get(self._model, "")
        self._image_capability = self._load_cached_image_capability(
            config.OLLAMA_BASE_URL,
            self._model,
            version,
        )
        if self._dialog:
            self._dialog.set_image_capability(
                self._image_capability,
                cached=self._image_capability != ImageCapability.UNKNOWN,
            )

    def _resolve_model_config(
        self,
        action_profile_id: str | None = None,
        agent: Agent | None = None,
    ):
        models = self._load_cached_models(config.OLLAMA_BASE_URL)
        available_models = (
            models
            if models or self._service_state in (ServiceState.ONLINE, ServiceState.EMPTY)
            else None
        )
        return self._profile_mgr.resolve(
            global_model=self._model,
            agent_profile_id=(agent or self._active_agent).profile_id,
            action_profile_id=action_profile_id,
            available_models=available_models,
            thinking_capability=lambda model: load_cached_thinking(
                config.OLLAMA_BASE_URL, model, self._model_versions.get(model, "")),
        )

    def _update_model_profile_summary(self) -> None:
        if not self._dialog:
            return
        resolved = self._resolve_model_config()
        self._dialog.set_model_profile_summary(
            resolved.profile_name,
            resolved.summary,
            resolved.warnings,
        )

    def _image_capability_for_model(self, model: str) -> ImageCapability:
        if model == self._model:
            return self._image_capability
        return self._load_cached_image_capability(
            config.OLLAMA_BASE_URL,
            model,
            self._model_versions.get(model, ""),
        )

    def _refresh_model_capability(self) -> int:
        if self._stopping or self._stopped or not self._model:
            return 0
        base_url = normalize_service_url(config.OLLAMA_BASE_URL)
        version = self._model_versions.get(self._model, "")
        if self._dialog:
            self._dialog.set_image_capability(
                self._image_capability,
                checking=True,
            )
        sequence = self._service_checks.check_model_capability(
            base_url,
            self._model,
            version,
        )
        self._capability_check = (sequence, base_url, self._model, version)
        return sequence

    @_safe_slot
    def _on_model_capability_checked(self, result: ModelCapabilityResult) -> None:
        expected = (
            result.sequence,
            result.base_url,
            result.model,
            result.version,
        )
        current = (
            result.sequence,
            normalize_service_url(config.OLLAMA_BASE_URL),
            self._model,
            self._model_versions.get(self._model, ""),
        )
        if self._stopping or self._capability_check != expected or expected != current:
            return
        self._capability_check = None
        self._image_capability = result.capability
        cache_thinking(result.base_url, result.model, result.version, result.thinking)
        self._update_model_profile_summary()
        if not result.error:
            save_setting(
                model_capability_cache_key(
                    result.base_url,
                    result.model,
                    result.version,
                ),
                result.capability.value,
            )
        if self._dialog:
            self._dialog.set_image_capability(result.capability)
        if result.error:
            logger.warning(
                "Could not determine image capability for %s: %s",
                result.model,
                result.error,
            )
        self._resume_pending_model()

    @_safe_slot
    def _on_service_checked(self, result: ServiceCheckResult) -> None:
        current_url = normalize_service_url(config.OLLAMA_BASE_URL)
        if (self._stopping or result.sequence != self._service_check_sequence
                or result.base_url != self._service_check_url or result.base_url != current_url):
            return
        self._service_state = result.state
        if self._dialog:
            self._dialog.set_service_status(result.state)
        if result.state == ServiceState.ONLINE:
            models = list(result.models)
            save_setting(model_cache_key(result.base_url), json.dumps(models, ensure_ascii=False))
            self._model_versions = dict(result.model_versions)
            save_setting(
                model_versions_cache_key(result.base_url),
                json.dumps(self._model_versions, ensure_ascii=False),
            )
            if self._dialog:
                self._dialog.refresh_models(models)
                selected_model = self._dialog.active_model
                if selected_model != self._model:
                    self._model = selected_model
                    save_setting("last_model", selected_model)
            elif self._model not in models:
                self._model = models[0]
                save_setting("last_model", self._model)
            self._sync_cached_image_capability()
            self._refresh_model_capability()
            self._update_model_profile_summary()
            logger.info("Ollama connected at %s (%d models)", result.base_url, len(models))
        elif result.state == ServiceState.EMPTY:
            self._service_checks.cancel_model_capability()
            self._capability_check = None
            self._image_capability = ImageCapability.UNKNOWN
            if self._dialog:
                self._dialog.set_image_capability(self._image_capability)
            logger.warning("Ollama connected at %s but has no models", result.base_url)
        elif result.state == ServiceState.INVALID:
            logger.warning("Invalid Ollama response from %s: %s", result.base_url, result.error)
        else:
            logger.warning("Cannot connect to Ollama at %s: %s", result.base_url, result.error)

        if self._startup_service_check == (result.sequence, result.base_url):
            self._startup_service_check = None
            self._show_startup_service_notice(result)
        self._resume_pending_model()

    def start_background_checks(self) -> None:
        """Show first-run guidance and schedule startup network checks."""
        if self._stopping or self._stopped:
            return
        if not get_setting("startup_welcome_shown"):
            self._show_notice(
                QMessageBox.Information,
                "欢迎使用 AI 桌面助手",
                "<b>AI 桌面助手</b><br><br>"
                "三种打开方式：<br>"
                "1. 选中文字 → 按 <b>⌘⌃L</b> → 自动填入对话框<br>"
                "2. 点击屏幕右侧 <b>悬浮按钮</b><br>"
                "3. 点击菜单栏 <b>图标</b><br><br>"
                "需要 <b>Ollama</b> 本地模型服务，数据不上传。<br>"
                "首次使用请确保 Ollama 已启动。",
            )
            save_setting("startup_welcome_shown", "1")
        sequence = self._refresh_model_list()
        self._startup_service_check = (sequence, self._service_check_url)
        self._service_checks.check_for_update()

    def _show_startup_service_notice(self, result: ServiceCheckResult) -> None:
        if result.state == ServiceState.OFFLINE:
            self._show_notice(
                QMessageBox.Warning,
                "Ollama 未运行",
                "未检测到 Ollama 服务。<br><br>"
                "请打开终端执行：<br><tt>ollama serve</tt><br><br>"
                "安装地址：<a href='https://ollama.com'>https://ollama.com</a>",
            )
        elif result.state == ServiceState.EMPTY:
            self._show_notice(
                QMessageBox.Warning,
                "无可用模型",
                "Ollama 已启动，但未安装任何模型。<br><br>"
                "请打开终端执行：<br><tt>ollama pull qwen3:14b</tt><br><br>"
                "更多模型：<a href='https://ollama.com/library'>https://ollama.com/library</a>",
            )
        elif result.state == ServiceState.INVALID:
            self._show_notice(
                QMessageBox.Warning,
                "Ollama 响应异常",
                "已连接到服务，但模型列表格式无法识别。请检查服务地址和版本。",
            )

    def _show_notice(self, icon, title: str, text: str) -> None:
        """Open a retained, non-modal notice so startup work can continue."""
        if self._stopping:
            return
        parent = self._dialog if self._dialog else None
        notice = show_notice(parent, icon, title, text)
        notice.setAttribute(Qt.WA_DeleteOnClose)
        self._notices.append(notice)

        def release_notice(*_) -> None:
            if notice in self._notices:
                self._notices.remove(notice)

        notice.closedSignal.connect(release_notice)
        notice.show()

    def _on_update_checked(self, update) -> None:
        if self._stopping or update is None:
            return
        self._tray.showMessage(
            "AI 桌面助手 — 有更新",
            f"新版本 {update.version} 可用\n{update.url}",
            QSystemTrayIcon.Information,
            5000,
        )

    # ── 新建对话 ───────────────────────────────────────

    @_safe_slot
    def _new_conversation(self) -> None:
        self._stop_worker(show_cancelled=False)
        self._clear_task_authorization()
        self._sync_active_agent_from_dialog()
        self._regenerating_user_id = 0
        self._convo_id = 0
        self._messages = []
        if self._dialog:
            self._dialog.clear_messages()
        self._sync_action_context()
        logger.info("New conversation started (agent=%s)", self._active_agent.name)

    def _sync_action_context(self) -> None:
        if self._dialog:
            self._dialog.set_action_context(self._convo_id != 0, self._action_mode)
            self._dialog.set_task_authorization(self._task_authorization, model=self._model)

    # ── 工作线程管理 ───────────────────────────────────

    def _stop_worker(self, *, show_cancelled: bool = True) -> None:
        """Invalidate callbacks and restore the UI before asynchronous cancellation."""
        self._cancel_pending_task()
        worker = self._worker
        if worker is None:
            return
        if self._regenerating_user_id:
            current = worker.current_request
            self._record_attempt_outcome(
                ChatResult(current.request_id, ResultStatus.CANCELLED,
                           run_id=current.run_id, step_id=current.step_id,
                           conversation_id=current.conversation_id)
            )
            self._regenerating_user_id = 0
        self._worker = None
        if getattr(worker, '_audit_recorder', None) is not None:
            worker._audit_recorder.cancelling()
        worker.cancel()
        self._retire_worker(worker)
        self.float_btn.set_responding(False)
        if self._dialog:
            self._dialog.finish_tool_run('已停止')
            self._dialog.set_thinking(False)
            if show_cancelled:
                self._dialog.finalize_assistant_stream(self._response_text, False, cancelled=True)

    def _on_worker_finished(self, worker: RunWorker) -> None:
        """Keep the QThread alive until finished, then release it on the UI thread."""
        worker._finished_observed = True
        if worker is self._worker:
            return  # Result handling will retire it after updating the UI.
        if worker in self._stale_workers:
            self._stale_workers.remove(worker)
        worker.deleteLater()
        self._finish_stop_if_ready()

    def _retire_worker(self, worker: RunWorker) -> None:
        # isFinished() can become true before its queued finished callback is
        # delivered. Deleting here would leave that callback with a stale Qt
        # sender pointer (a native SIP crash on macOS).
        if getattr(worker, '_finished_observed', False):
            worker.deleteLater()
        elif worker not in self._stale_workers:
            self._stale_workers.append(worker)

    # ── 对话历史 ───────────────────────────────────────

    @_safe_slot
    def _on_history_requested(self) -> None:
        dialog = HistoryDialog(parent=self._dialog, agents=self._all_agents)
        dialog.conversation_selected.connect(self._on_conversation_selected)
        dialog.conversation_deleted.connect(self._on_conversation_deleted)
        if self._dialog:
            p = self._dialog.geometry().center()
            dialog.move(p.x() - dialog.width() // 2, p.y() - dialog.height() // 2)
        dialog.exec_()

    def _on_conversation_selected(self, convo_id: int) -> None:
        try:
            conv = get_conversation(convo_id)
            if conv is None:
                return
            # 停止当前 worker
            self._stop_worker(show_cancelled=False)
            self._clear_task_authorization()
            self._regenerating_user_id = 0
            # 恢复对话状态
            self._convo_id = conv.id
            self._messages = conv.messages
            # 切换 Agent
            for ag in self._all_agents:
                if ag.id == conv.agent_id:
                    self._active_agent = self._agent_mgr.switch(ag)
                    if self._dialog:
                        self._dialog.set_active_agent(self._active_agent)
                    self._tray.set_active_agent(self._active_agent)
                    self._update_model_profile_summary()
                    break
            # 渲染消息
            if self._dialog:
                self._render_messages()
            self._sync_action_context()
            self._refresh_regenerate_state()
            logger.info("Loaded conversation %d (%d messages)", convo_id, len(conv.messages))
        except Exception:
            logger.exception("Failed to load conversation %d", convo_id)

    def _on_conversation_deleted(self, convo_id: int) -> None:
        if convo_id != self._convo_id:
            return
        self._stop_worker(show_cancelled=False)
        self._clear_task_authorization()
        self._regenerating_user_id = 0
        self._convo_id = 0
        self._messages = []
        self._response_text = ""
        if self._dialog:
            self._dialog.clear_messages()
            self._dialog.set_thinking(False)
        self._sync_action_context()
        logger.info("Current conversation %d deleted", convo_id)

    @_safe_slot
    def _on_export_requested(self) -> None:
        """将当前对话格式化为 Markdown 并复制到剪贴板"""
        if not self._messages:
            return
        md = "# AI 桌面助手 · 对话记录\n\n"
        md += f"**{self._active_agent.icon} {self._active_agent.name}**\n\n"
        md += "---\n\n"
        for m in self._context_messages(self._messages):
            role = "**用户**" if m.role == "user" else "**助手**"
            md += f"{role}: {m.content}\n\n"
        from ai_desktop.services.audit_store import export_markdown
        md += export_markdown(self._convo_id)
        try:
            QApplication.clipboard().setText(md)
        except Exception:
            pass
        if self._dialog:
            self._dialog.flash_export_btn()
        logger.info("Exported %d messages to clipboard", len(self._messages))

    @_safe_slot
    def _on_run_history_requested(self):
        if self._dialog:
            self._dialog.purge_expired_audit()
        from ai_desktop.ui.run_history_dialog import RunHistoryDialog
        dialog = RunHistoryDialog(self._convo_id, self._dialog)
        dialog.exec_()

    def _clear_task_authorization(self, *, cancel_running=False):
        if cancel_running and self._worker is not None and self._worker.context.tools:
            self._stop_worker()
        self._task_epoch += 1
        self._cancel_pending_task()
        self._task_authorization = None
        if self._dialog:
            self._dialog.set_task_authorization(None)

    def _cancel_pending_task(self):
        pending, self._pending_task = self._pending_task, None
        self._model_prepare_timer.stop()
        self._task_checks.cancel()
        if pending is None:
            return
        from ai_desktop.utils import storage
        if self._dialog:
            self._dialog.set_thinking(False)
            self._dialog.set_task_authorization(self._task_authorization, model=self._model)
            if not pending['regenerate']:
                self._dialog.restore_draft(pending['text'], list(pending['images']))
                retry_action = pending.get('kwargs', {}).get('retry_action_id')
                if retry_action:
                    self._action_mode = pending['kwargs']['retry_action_mode']
                    self._sync_action_context()
                    self._dialog.show_actions(pending['text'], retry_action)
        storage.release_attachment_paths(pending['retained'])

    def _cancel_pending_model(self):
        if self._pending_task and self._pending_task.get('kind') == 'model':
            self._cancel_pending_task()

    def _should_prepare_model(self, resolved):
        checking = (self._capability_check is not None or
                    self._service_state == ServiceState.CHECKING and self._service_check_sequence > 0)
        capability = load_cached_thinking(config.OLLAMA_BASE_URL, resolved.model,
                                          self._model_versions.get(resolved.model, ''))
        return (checking and resolved.model == self._model and not capability.known
                and resolved.think_setting.mode != ThinkMode.MODEL_DEFAULT)

    def _begin_model_prepare(self, text, images, resolved, kwargs=None, *, regenerate=0):
        from ai_desktop.utils import storage
        self._pending_task = {'kind': 'model', 'text': text, 'images': tuple(images),
                              'retained': storage.retain_attachment_paths(images), 'regenerate': regenerate,
                              'resolved': resolved, 'kwargs': kwargs or {}, 'conversation_id': self._convo_id,
                              'model': self._model, 'agent_id': self._active_agent.id,
                              'base_url': config.OLLAMA_BASE_URL, 'epoch': self._task_epoch}
        if self._dialog:
            self._dialog.set_thinking(True)
        self._model_prepare_timer.start(6000)

    def _resume_pending_model(self, *, timed_out=False):
        pending = self._pending_task
        if pending is None or pending.get('kind') != 'model':
            return
        if (self._stopping or self._worker is not None or self._convo_id != pending['conversation_id']
                or self._active_agent.id != pending['agent_id'] or self._model != pending['model']
                or config.OLLAMA_BASE_URL != pending['base_url'] or self._task_epoch != pending['epoch']):
            self._cancel_pending_task()
            return
        checking = (self._service_state == ServiceState.CHECKING and self._service_check_sequence > 0
                    or self._capability_check is not None)
        if checking and not timed_out:
            return
        if checking or self._service_state in {ServiceState.EMPTY, ServiceState.INVALID, ServiceState.OFFLINE}:
            self._cancel_pending_task()
            self._show_notice(QMessageBox.Warning, '模型检查未完成',
                              '未发送请求，草稿已保留。请检查模型服务后重试。')
            return
        capability = load_cached_thinking(config.OLLAMA_BASE_URL, pending['resolved'].model,
                                          self._model_versions.get(pending['resolved'].model, ''))
        prepared = pending['resolved'].for_model(pending['resolved'].model, capability)
        self._pending_task = None
        self._model_prepare_timer.stop()
        from ai_desktop.utils import storage
        try:
            if self._dialog:
                self._dialog.set_thinking(False)
            if pending['regenerate']:
                user = next((msg for msg in self._messages if msg.id == pending['regenerate']), None)
                if user:
                    self._start_regeneration(user, prepared_config=prepared)
            else:
                self._on_user_message(pending['text'], list(pending['images']),
                                      prepared_config=prepared, **pending['kwargs'])
        finally:
            storage.release_attachment_paths(pending['retained'])

    @_safe_slot
    def _on_task_settings_requested(self):
        if self._worker is not None or self._pending_task is not None:
            return
        agent = self._sync_active_agent_from_dialog()
        if not config.CHAT_TOOLS_ENABLED:
            self._show_notice(QMessageBox.Warning, '工具已禁用', '请在设置 → 工具执行中允许对话 Agent 启用工具。')
            return
        from ai_desktop.services.audit_store import list_runs
        runs = list_runs(self._convo_id) if self._convo_id else []
        hint = next((run['config']['execution']['workspace'] for run in runs
                     if run['config'].get('execution')), '')
        model_settings = TaskModelSettings.from_config(self._resolve_model_config(agent=agent))
        dialog = TaskDialog(self._task_authorization, workspace_hint=hint, model=self._model,
                            settings=model_settings, agent_id=agent.id, agent_name=agent.name, parent=self._dialog)
        conversation_id = self._convo_id
        epoch = self._task_epoch
        selected_model = self._model
        if dialog.exec_() != dialog.Accepted:
            return
        if (self._convo_id != conversation_id or self._active_agent.id != agent.id or self._stopping
                or self._task_epoch != epoch or self._model != selected_model
                or model_settings != TaskModelSettings.from_config(self._resolve_model_config(agent=agent))):
            return
        self._task_authorization = dialog.authorization
        self._task_epoch += 1
        self._sync_action_context()

    def _begin_task_check(self, text, images, *, regenerate=False):
        from ai_desktop.utils import storage
        pending = {'text': text, 'images': tuple(images), 'regenerate': regenerate,
                   'conversation_id': self._convo_id, 'authorization': self._task_authorization,
                   'agent_id': self._active_agent.id,
                   'base_url': config.OLLAMA_BASE_URL, 'model': self._model,
                   'model_settings': TaskModelSettings.from_config(self._resolve_model_config()),
                   'retained': storage.retain_attachment_paths(images)}
        self._pending_task = pending
        if self._dialog:
            self._dialog.set_thinking(True)
            self._dialog.set_task_authorization(self._task_authorization, model=self._model, checking=True)
        pending['sequence'] = self._task_checks.check(config.OLLAMA_BASE_URL, self._model,
                                                     settings=pending['model_settings'])

    @_safe_slot
    def _on_task_checked(self, sequence, admission, error):
        pending = self._pending_task
        if pending is None or pending.get('kind') == 'model' or pending['sequence'] != sequence:
            return
        if (self._stopping or self._convo_id != pending['conversation_id'] or self._worker is not None
                or self._active_agent.id != pending['agent_id']
                or self._task_authorization is not pending['authorization']
                or config.OLLAMA_BASE_URL != pending['base_url'] or self._model != pending['model']
                or pending['model_settings'] != TaskModelSettings.from_config(self._resolve_model_config())):
            self._cancel_pending_task()
            return
        self._pending_task = None
        from ai_desktop.utils import storage
        try:
            if self._dialog:
                self._dialog.set_thinking(False)
                self._dialog.set_task_authorization(self._task_authorization, model=self._model)
            if error:
                if self._dialog and not pending['regenerate']:
                    self._dialog.restore_draft(pending['text'], list(pending['images']))
                self._show_notice(QMessageBox.Warning, '工具任务未启动', error)
                return
            if pending['regenerate']:
                user = next((message for message in self._messages if message.id == pending['regenerate']), None)
                if user:
                    self._start_regeneration(user, task_admission=admission)
            else:
                self._on_user_message(pending['text'], list(pending['images']), task_admission=admission)
        finally:
            storage.release_attachment_paths(pending['retained'])

    @_safe_slot
    def _on_manage_agents(self) -> None:
        """打开 Agent 管理对话框"""
        builtin = [
            AgentDef(id=ag.id, name=ag.name, icon=ag.icon,
                     system_prompt=ag.system_prompt, builtin=True,
                     profile_id=ag.profile_id)
            for ag in self._agent_mgr.builtin_agents
        ]
        editor = AgentEditor(
            builtin,
            self._custom_agents,
            parent=self._dialog,
            profiles=self._profile_mgr.profiles,
            models=self._load_cached_models(config.OLLAMA_BASE_URL),
            actions=self._action_service.actions,
            global_model=self._model, base_url=config.OLLAMA_BASE_URL, model_versions=self._model_versions,
        )
        editor.agents_saved.connect(self._on_custom_agents_saved)
        editor.profiles_saved.connect(self._on_profiles_saved)
        editor.agent_profile_changed.connect(self._on_agent_profile_changed)
        editor.actions_saved.connect(self._on_actions_saved)
        if self._dialog:
            p = self._dialog.geometry().center()
            editor.move(p.x() - editor.width() // 2, p.y() - editor.height() // 2)
        editor.exec_()

    @_safe_slot
    def _on_custom_agents_saved(self, data: list[dict]) -> None:
        """自定义 Agent 保存后刷新"""
        self._cancel_pending_model()
        self._clear_task_authorization(cancel_running=True)
        self._agent_mgr.save_custom(data)
        self._all_agents = self._agent_mgr.all_agents
        self._custom_agents = self._agent_mgr.custom_agents
        self._active_agent = self._agent_mgr.active_agent
        if self._dialog:
            self._dialog.refresh_agents(self._all_agents, self._active_agent)
        self._tray.refresh_agents(self._all_agents)
        self._tray.set_active_agent(self._active_agent)
        self._sync_action_context()
        self._update_model_profile_summary()
        logger.info("Custom agents saved (%d custom)", len(data))

    @_safe_slot
    def _on_profiles_saved(self, profiles: list[ModelProfile]) -> None:
        self._cancel_pending_model()
        self._cancel_pending_task()
        self._profile_mgr.replace_all(profiles)
        self._action_service.reload()
        self._agent_mgr.refresh_profile_assignments()
        self._all_agents = self._agent_mgr.all_agents
        self._custom_agents = self._agent_mgr.custom_agents
        self._active_agent = self._agent_mgr.active_agent
        if self._dialog:
            self._dialog.refresh_actions(self._action_service.visible_actions)
        self._refresh_pet_actions()
        self._update_model_profile_summary()
        logger.info("Model profiles saved (%d)", len(profiles))

    @_safe_slot
    def _on_actions_saved(self, actions: list[Action]) -> None:
        self._cancel_pending_model()
        self._action_service.replace_all(actions)
        if self._dialog:
            self._dialog.refresh_actions(self._action_service.visible_actions)
        self._refresh_pet_actions()
        logger.info("Quick actions saved (%d)", len(actions))

    def _refresh_pet_actions(self) -> None:
        actions = (
            self._action_service.recent_actions()
            if config.QUICK_ACTIONS_ENABLED and self.float_btn.pet_enabled
            else []
        )
        self.float_btn.set_quick_actions(
            [(action.id, action.name) for action in actions]
        )

    @_safe_slot
    def _on_agent_profile_changed(self, agent_id: str, profile_id: str | None) -> None:
        self._cancel_pending_model()
        self._cancel_pending_task()
        self._agent_mgr.assign_profile(agent_id, profile_id)
        self._all_agents = self._agent_mgr.all_agents
        self._custom_agents = self._agent_mgr.custom_agents
        self._active_agent = self._agent_mgr.active_agent
        if self._dialog:
            self._dialog.refresh_agents(self._all_agents, self._active_agent)
        self._tray.refresh_agents(self._all_agents)
        self._tray.set_active_agent(self._active_agent)
        self._update_model_profile_summary()
        logger.info("Agent %s model profile changed to %s", agent_id, profile_id or "global")

    # ── 发送消息 ───────────────────────────────────────

    def _on_stop_requested(self) -> None:
        """中断当前流式生成"""
        self._stop_worker()
        logger.info("Streaming interrupted by user")

    def _latest_user_turn(self) -> tuple[Message | None, Message | None]:
        """Return the latest user message and its newest assistant answer."""
        user_message = next(
            (message for message in reversed(self._messages) if message.role == "user"),
            None,
        )
        assistant_message = None
        if user_message is not None:
            try:
                index = next(
                    i for i, message in enumerate(self._messages)
                    if message.role == "user" and message.id == user_message.id
                )
            except StopIteration:
                index = None
            if index is not None:
                following = [
                    message
                    for message in self._messages[index + 1:]
                    if message.role == "assistant"
                ]
                assistant_message = following[-1] if following else None
        return user_message, assistant_message

    def _active_answer_message(self, user_message: Message) -> Message | None:
        """Resolve the newest stored answer row for regen pairing and context."""
        try:
            active = get_active_generation(user_message.id)
        except Exception:
            logger.exception("Failed to load active answer version")
            return None
        if active is None or active.assistant_message_id is None:
            _, assistant_message = self._latest_user_turn()
            return assistant_message
        return next(
            (message for message in self._messages if message.id == active.assistant_message_id),
            None,
        )

    def _has_follow_up_after(self, user_message_id: int) -> bool:
        """Newer user turns lock older answers against switching or regen context."""
        seen_target = False
        for message in self._messages:
            if message.role == "user" and message.id == user_message_id:
                seen_target = True
            elif seen_target and message.role == "user":
                return True
        return False

    def _version_snapshots(self, user_message_id: int) -> list[dict]:
        return [
            {"id": version.id, "answer": version.answer, "active": version.active,
             "task": bool(version.config_snapshot.get('allowed_tools'))}
            for version in list_generations(user_message_id)
            if version.status == "succeeded" and version.answer
        ]

    def _refresh_regenerate_state(self) -> None:
        """Sync the latest-turn regen entry without rebuilding message bubbles."""
        if not self._dialog:
            return
        user_message, assistant_message = self._latest_user_turn()
        if (
            user_message is None
            or assistant_message is None
            or self._has_follow_up_after(user_message.id)
        ):
            self._dialog.set_regenerate_state(False, [])
            return
        try:
            versions = self._version_snapshots(user_message.id)
        except Exception:
            logger.exception("Failed to load answer versions")
            self._dialog.set_regenerate_state(False, [])
            return
        if not versions:
            self._dialog.set_regenerate_state(False, [])
            return
        self._dialog.set_regenerate_state(True, versions)

    @_safe_slot
    def _on_regenerate_requested(self) -> None:
        if (self._worker is not None or self._pending_task is not None
                or any(worker.context.tools and worker.isRunning() for worker in self._stale_workers)):
            if self._dialog:
                self._dialog.flash_busy()
            return
        user_message, assistant_message = self._latest_user_turn()
        if user_message is None or assistant_message is None:
            return
        source = get_active_generation(user_message.id)
        if source and source.config_snapshot.get('allowed_tools'):
            if self._task_authorization is None or self._task_authorization.agent_id != self._active_agent.id:
                self._show_notice(QMessageBox.Warning, '请先启用工具', '重新执行任务前，请在输入区检查并启用工具授权。')
                return
            if source.config_snapshot.get('agent_id', self._active_agent.id) != self._active_agent.id:
                self._show_notice(QMessageBox.Warning, '任务属于其他角色', '请切回原任务角色并重新启用工具后执行。')
                return
            if QMessageBox.question(self._dialog, '重新执行任务',
                                    '这将创建新任务，并可能再次运行命令或产生搜索费用。是否继续？') != QMessageBox.Yes:
                return
            self._begin_task_check(user_message.content, user_message.images, regenerate=user_message.id)
            return
        self._start_regeneration(user_message)

    def _start_regeneration(self, user_message: Message, *, task_admission=None, prepared_config=None) -> None:
        """Retry the latest user turn without duplicating the stored question."""
        versions = list_generations(user_message.id)
        source = next((version for version in versions if version.active), versions[-1] if versions else None)
        snapshot = source.config_snapshot if source else {}
        origin = "action" if snapshot.get("origin") == "action" else "chat"
        request_agent = self._active_agent
        system_prompt = request_agent.system_prompt
        action_profile_id = None
        if origin == 'action':
            try:
                plan = self._action_service.build_request_plan(
                    snapshot.get('action_id'), user_message.content, self._all_agents,
                )
            except (LookupError, ValueError) as exc:
                self._show_notice(QMessageBox.Warning, '无法重新执行快捷动作', html.escape(str(exc)))
                return
            request_agent = plan.agent
            system_prompt = plan.system_prompt
            action_profile_id = plan.action_profile_id
            if plan.warnings:
                self._show_notice(QMessageBox.Warning, '快捷动作已回退',
                                  '<br>'.join(html.escape(warning) for warning in plan.warnings))
        resolved = prepared_config or self._resolve_model_config(action_profile_id, request_agent)
        task_kwargs = {}
        if snapshot.get('allowed_tools'):
            try:
                if self._task_authorization is None:
                    raise ValueError('请先在输入区启用工具授权。')
                if snapshot.get('agent_id', self._active_agent.id) != self._active_agent.id:
                    raise ValueError('请切回原任务角色并重新启用工具后执行。')
                task_kwargs = self._task_authorization.worker_kwargs(task_admission, config.OLLAMA_BASE_URL,
                                                                   agent_id=self._active_agent.id, origin=origin,
                                                                   model=self._model,
                                                                   settings=TaskModelSettings.from_config(resolved))
            except ValueError as exc:
                self._show_notice(QMessageBox.Warning, '工具任务未启动', str(exc))
                return
        if task_kwargs:
            resolved = replace(resolved, model=task_kwargs['model'], think=task_kwargs['think'],
                               warnings=task_admission.warnings)
        if not task_kwargs and prepared_config is None and self._should_prepare_model(resolved):
            self._begin_model_prepare(user_message.content, user_message.images, resolved, regenerate=user_message.id)
            return
        images = list(user_message.images or [])
        image_capability = ((ImageCapability.SUPPORTED if task_admission.vision_supported else
                             ImageCapability.UNSUPPORTED)
                            if task_kwargs else self._image_capability_for_model(resolved.model))
        if images and image_capability == ImageCapability.UNSUPPORTED:
            if self._dialog:
                self._dialog.focus_model_selector()
            self._show_notice(
                QMessageBox.Warning,
                "当前模型不支持图片",
                f"模型 {html.escape(task_kwargs['model'] if task_kwargs else resolved.model)} 已声明不支持图片输入。"
                "请选择显示“图片 ✓”的模型后重试。",
            )
            return
        if images and image_capability == ImageCapability.UNKNOWN:
            if self._dialog and not self._dialog.confirm_unknown_image_capability(
                task_kwargs['model'] if task_kwargs else resolved.model
            ):
                return
        recent = list(self._messages)
        cutoff = next(
            (index for index, message in enumerate(recent) if message.id == user_message.id),
            None,
        )
        if cutoff is None:
            return
        # F04.1: only the active answer participates in the retried context.
        context = [message for message in recent[:cutoff] if message.role in {"user", "assistant"}]
        context.append(user_message)
        context = self._context_messages(context)
        max_msgs = config.OLLAMA_MAX_ROUNDS * 2
        if len(context) > max_msgs:
            context = context[-max_msgs:]
        if resolved.warnings:
            self._show_notice(
                QMessageBox.Warning,
                "模型配置已回退",
                "<br>".join(html.escape(warning) for warning in resolved.warnings),
            )
        self._response_text = ""
        try:
            worker_options = {'model': resolved.model, 'think': resolved.think, 'think_setting': resolved.think_setting,
                              'think_source': resolved.think_source, 'options': resolved.options}
            worker_options.update(task_kwargs)
            worker = RunWorker(
                context, system_prompt, parent=self,
                conversation_id=self._convo_id, agent_id=request_agent.id,
                origin=origin, action_id=snapshot.get("action_id") if origin == "action" else None,
                **worker_options,
            )
            worker._task_admission = task_admission if task_kwargs else None
        except Exception:
            logger.exception("Failed to start regeneration")
            if self._dialog:
                self._dialog.flash_busy()
            return
        worker.thinking_event.connect(self._on_thinking_event)
        worker.content_event.connect(self._on_stream_event)
        worker.done.connect(self._on_stream_done)
        worker.finished.connect(lambda worker=worker: self._on_worker_finished(worker), Qt.QueuedConnection)
        self._connect_tool_feedback(worker)
        if self._dialog:
            if not worker.context.tools:
                self._dialog.begin_assistant_stream()
            self._dialog.set_thinking(True)
        self._worker = worker
        self._regenerating_user_id = user_message.id
        worker.start()
        self.float_btn.set_responding(True)

    def _render_messages(self) -> None:
        """Redraw bubbles from in-memory messages without touching the draft."""
        if not self._dialog:
            return
        self._dialog.clear_messages()
        user_message, assistant_message = self._latest_user_turn()
        versions: list[dict] = []
        version_index = -1
        regen_available = False
        if user_message is not None and not self._has_follow_up_after(user_message.id):
            try:
                versions = self._version_snapshots(user_message.id)
            except Exception:
                logger.exception("Failed to load answer versions")
                versions = []
            regen_available = assistant_message is not None
            if versions:
                version_index = next(
                    (index for index, item in enumerate(versions) if item.get("active")),
                    len(versions) - 1,
                )
        from ai_desktop.services.audit_store import cleanup
        cleanup()
        pending_user = None
        for message in self._context_messages(self._messages):
            if message.role == "user":
                pending_user = message.id
                self._dialog.add_user_message(
                    message.content,
                    images=message.images,
                    missing_images=message.missing_images,
                )
            elif message.role == "assistant":
                selected = get_active_generation(pending_user) if pending_user is not None else None
                is_latest = (
                    assistant_message is not None and message.id == assistant_message.id
                )
                self._dialog.add_assistant_message(
                    message.content,
                    versions=versions if is_latest else None,
                    version_index=version_index if is_latest else -1,
                    regen_available=regen_available and is_latest,
                    sources=self._stored_sources(message_id=message.id,
                                                 generation_id=selected.id if selected else None,
                                                 cleanup_first=False),
                )

    @_safe_slot
    def _on_generation_selected(self, generation_id: int) -> None:
        user_message, assistant_message = self._latest_user_turn()
        if user_message is None or assistant_message is None:
            return
        if self._worker is not None:
            if self._dialog:
                self._dialog.flash_busy()
            return
        if self._has_follow_up_after(user_message.id):
            self._show_notice(
                QMessageBox.Warning,
                "无法切换版本",
                "该回答之后已有新的追问，暂不支持切换旧答案。",
            )
            return
        try:
            candidate = get_generation(generation_id)
        except (LookupError, ValueError) as exc:
            self._show_notice(
                QMessageBox.Warning, "无法切换版本", html.escape(str(exc))
            )
            return
        except Exception:
            logger.exception("Failed to switch answer version")
            return
        if candidate.user_message_id != user_message.id:
            self._show_notice(
                QMessageBox.Warning,
                "无法切换版本",
                "该版本不属于当前最新问题。",
            )
            return
        try:
            selected = set_active_generation(generation_id)
        except (LookupError, ValueError) as exc:
            self._show_notice(
                QMessageBox.Warning, "无法切换版本", html.escape(str(exc))
            )
            return
        except Exception:
            logger.exception("Failed to switch answer version")
            return
        selected_message_id = selected.assistant_message_id
        for message in self._messages:
            if message.role == "assistant" and (
                message.id == selected_message_id
                or (
                    selected_message_id is None
                    and message.id == assistant_message.id
                )
            ):
                message.content = selected.answer
        if self._dialog and not self._dialog.show_generation(
            selected.id, selected.answer, sources=self._stored_sources(generation_id=selected.id)
        ):
            self._render_messages()
        else:
            self._refresh_regenerate_state()
        logger.info("Answer version switched to generation %d", selected.id)

    @staticmethod
    def _stored_sources(*, message_id=None, generation_id=None, cleanup_first=True):
        from ai_desktop.services.audit_store import sources_for_message
        return sources_for_message(message_id, generation_id=generation_id, cleanup_first=cleanup_first)

    @_safe_slot
    def _on_action_requested(
        self,
        action_id: str,
        material: str,
        mode: str = "new",
    ) -> None:
        mode = mode if mode in {"new", "current"} else "new"
        if mode == "current" and self._convo_id == 0:
            mode = "new"
        self._action_mode = mode
        save_setting("action_conversation_mode", mode)
        self._sync_action_context()
        if self._worker is not None or self._pending_task is not None:
            if self._dialog:
                self._dialog.set_input_text(material)
                self._dialog.show_actions(material, action_id)
                self._dialog.flash_busy()
            return
        try:
            plan = self._action_service.build_request_plan(
                action_id,
                material,
                self._all_agents,
            )
        except (LookupError, ValueError) as exc:
            if self._dialog:
                self._dialog.set_input_text(material)
                self._dialog.show_actions(material, action_id)
            self._show_notice(QMessageBox.Warning, "无法执行快捷动作", html.escape(str(exc)))
            return
        if plan.warnings:
            self._show_notice(
                QMessageBox.Warning,
                "快捷动作已回退",
                "<br>".join(html.escape(warning) for warning in plan.warnings),
            )
        self._action_service.record_use(plan.action.id)
        self._refresh_pet_actions()
        if mode == "new":
            self._new_conversation()
            self._active_agent = self._agent_mgr.switch(plan.agent)
            if self._dialog:
                self._dialog.set_active_agent(self._active_agent)
            self._tray.set_active_agent(self._active_agent)
            self._update_model_profile_summary()
        self._on_user_message(
            plan.material,
            system_prompt=plan.system_prompt,
            action_profile_id=plan.action_profile_id,
            retry_action_id=plan.action.id,
            retry_action_mode=mode,
            request_agent=plan.agent,
        )

    def _active_answers_by_user(self) -> dict[int, str]:
        """Map each user message to its selected answer for request context."""
        active_answers: dict[int, str] = {}
        pending_user: int | None = None
        pending_assistants: list[Message] = []
        for message in self._messages:
            if message.role == "user":
                if pending_user is not None and pending_assistants:
                    try:
                        active = get_active_generation(pending_user)
                    except Exception:
                        logger.exception("Failed to load active answer version")
                        active = None
                    active_answers[pending_user] = (
                        active.answer
                        if active is not None and active.answer
                        else pending_assistants[-1].content
                    )
                pending_user = message.id
                pending_assistants = []
            elif message.role == "assistant" and pending_user is not None:
                pending_assistants.append(message)
        if pending_user is not None and pending_assistants:
            try:
                active = get_active_generation(pending_user)
            except Exception:
                logger.exception("Failed to load active answer version")
                active = None
            active_answers[pending_user] = (
                active.answer
                if active is not None and active.answer
                else pending_assistants[-1].content
            )
        return active_answers

    def _context_messages(self, messages: list[Message]) -> list[Message]:
        """Substitute superseded answers with the selected version per turn."""
        active_answers = self._active_answers_by_user()
        context: list[Message] = []
        pending_user: int | None = None
        pending_assistants: list[Message] = []
        for message in messages:
            if message.role == "user":
                if pending_user is not None and pending_assistants:
                    answer = active_answers.get(
                        pending_user, pending_assistants[-1].content
                    )
                    context.append(self._assistant_context_message(
                        pending_assistants[-1], answer
                    ))
                pending_user = message.id
                pending_assistants = []
                context.append(message)
            elif message.role == "assistant" and pending_user is not None:
                pending_assistants.append(message)
            else:
                context.append(message)
        if pending_user is not None and pending_assistants:
            answer = active_answers.get(pending_user, pending_assistants[-1].content)
            context.append(self._assistant_context_message(pending_assistants[-1], answer))
        return context

    @staticmethod
    def _assistant_context_message(message: Message, answer: str) -> Message:
        if answer == message.content:
            return message
        return Message(
            role=message.role,
            content=answer,
            id=message.id,
            created_at=message.created_at,
            images=list(message.images),
            missing_images=list(message.missing_images),
        )

    def _on_user_message(
        self,
        text: str,
        images: Optional[list] = None,
        *,
        system_prompt: str | None = None,
        action_profile_id: str | None = None,
        retry_action_id: str | None = None,
        retry_action_mode: str = "new",
        request_agent: Agent | None = None,
        task_admission=None,
        prepared_config=None,
    ) -> None:
        images = images or []
        if self._stopping or self._stopped:
            return
        if self._worker is not None or self._pending_task is not None:
            if self._dialog:
                self._dialog.restore_draft(text, images)
                self._dialog.flash_busy()
            return
        self._result_bubble.hide()
        request_agent = request_agent or self._sync_active_agent_from_dialog()
        if any(worker.context.tools and worker.isRunning() for worker in self._stale_workers):
            if self._dialog:
                self._dialog.restore_draft(text, images)
            self._show_notice(QMessageBox.Warning, '上一任务正在停止', '请等待执行进程退出后再发送。')
            return
        task = self._task_authorization if not retry_action_id else None
        if task is not None and task.agent_id != request_agent.id:
            if self._dialog:
                self._dialog.restore_draft(text, images)
            self._show_notice(QMessageBox.Warning, '工具授权已失效', '请为当前角色重新启用工具后发送。')
            return
        if task is not None and task_admission is None:
            self._begin_task_check(text, images)
            return
        task_kwargs = {}
        resolved = prepared_config or self._resolve_model_config(action_profile_id, request_agent)
        if task_admission is not None:
            try:
                if task is None:
                    raise ValueError('工具授权已关闭，请重新启用后发送。')
                task_kwargs = task.worker_kwargs(task_admission, config.OLLAMA_BASE_URL,
                                                 agent_id=request_agent.id, origin='chat', model=self._model,
                                                 settings=TaskModelSettings.from_config(resolved))
            except ValueError as exc:
                if self._dialog:
                    self._dialog.restore_draft(text, images)
                self._show_notice(QMessageBox.Warning, '工具任务未启动', str(exc))
                return
        if not task_kwargs and prepared_config is None and self._should_prepare_model(resolved):
            self._begin_model_prepare(text, images, resolved, {
                'system_prompt': system_prompt, 'action_profile_id': action_profile_id,
                'retry_action_id': retry_action_id, 'retry_action_mode': retry_action_mode,
                'request_agent': request_agent,
            })
            return
        if task_kwargs:
            resolved = replace(resolved, model=task_kwargs['model'], think=task_kwargs['think'],
                               warnings=task_admission.warnings)
        image_capability = ((ImageCapability.SUPPORTED if task_admission.vision_supported else
                             ImageCapability.UNSUPPORTED)
                            if task_kwargs else self._image_capability_for_model(resolved.model))
        if (
            images
            and image_capability == ImageCapability.UNSUPPORTED
            and resolved.model != self._model and not task_kwargs
        ):
            inherited_capability = self._image_capability_for_model(self._model)
            if inherited_capability != ImageCapability.UNSUPPORTED:
                warning = (
                    f"配置模型 {resolved.model} 不支持图片，"
                    f"本次已继承全局模型 {self._model}。"
                )
                resolved = resolved.for_model(self._model, load_cached_thinking(
                    config.OLLAMA_BASE_URL, self._model, self._model_versions.get(self._model, "")))
                resolved = replace(
                    resolved,
                    warnings=resolved.warnings + (warning,),
                )
                image_capability = inherited_capability
        if resolved.warnings:
            self._show_notice(
                QMessageBox.Warning,
                "模型配置已回退",
                "<br>".join(html.escape(warning) for warning in resolved.warnings),
            )
        if images and image_capability == ImageCapability.UNSUPPORTED:
            if self._dialog:
                self._dialog.restore_draft(text, images)
                self._dialog.focus_model_selector()
            self._show_notice(
                QMessageBox.Warning,
                "当前模型不支持图片",
                f"模型 {html.escape(resolved.model)} 已声明不支持图片输入。"
                "请选择显示“图片 ✓”的模型后重试。",
            )
            return
        if images and image_capability == ImageCapability.UNKNOWN:
            if self._dialog and not self._dialog.confirm_unknown_image_capability(
                resolved.model
            ):
                self._dialog.restore_draft(text, images)
                return

        created_conversation = False
        user_msg = None
        worker = None
        try:
            if self._convo_id == 0:
                conv = create_conversation(request_agent.id)
                self._convo_id = conv.id
                created_conversation = True
                self._sync_action_context()

            user_msg = save_message(self._convo_id, "user", text, images=images)
            self._messages.append(user_msg)

            # 截断过长的历史，只保留最近 N 轮
            recent = list(self._messages)
            max_msgs = config.OLLAMA_MAX_ROUNDS * 2
            if len(recent) > max_msgs:
                recent = recent[-max_msgs:]
            recent = self._context_messages(recent)

            logger.info(
                "Starting request conversation=%d agent=%s context_messages=%d roles=%s",
                self._convo_id,
                request_agent.id,
                len(recent),
                ",".join(message.role for message in recent),
            )

            self._response_text = ""
            worker_options = {'model': resolved.model, 'think': resolved.think, 'think_setting': resolved.think_setting,
                              'think_source': resolved.think_source, 'options': resolved.options}
            worker_options.update(task_kwargs)
            worker = RunWorker(
                recent, system_prompt or request_agent.system_prompt, parent=self,
                conversation_id=self._convo_id, agent_id=request_agent.id,
                origin="action" if retry_action_id else "chat", action_id=retry_action_id,
                **worker_options,
            )
            worker._task_admission = task_admission if task_kwargs else None
            worker.thinking_event.connect(self._on_thinking_event)
            worker.content_event.connect(self._on_stream_event)
            worker.done.connect(self._on_stream_done)
            worker.finished.connect(lambda worker=worker: self._on_worker_finished(worker), Qt.QueuedConnection)
            self._connect_tool_feedback(worker)
            if self._dialog:
                self._dialog.add_user_message(text, images=user_msg.images)
                if not worker.context.tools:
                    self._dialog.begin_assistant_stream()
                self._dialog.set_thinking(True)
            self._worker = worker
            worker.start()
            if self._dialog:
                self._dialog.add_input_history(text)
            self.float_btn.set_responding(True)
        except Exception:
            logger.exception("Failed to send message")
            if self._worker is worker:
                self._worker = None
            if worker is not None:
                if worker.isRunning():
                    worker.cancel()
                    self._retire_worker(worker)
                else:
                    worker.release_attachments()
                    worker.deleteLater()
            if user_msg is not None:
                try:
                    delete_message(user_msg.id, preserve_attachments=True)
                except Exception:
                    logger.exception("Failed to roll back message %d", user_msg.id)
                self._messages = [message for message in self._messages if message.id != user_msg.id]
            if created_conversation:
                try:
                    delete_conversation(self._convo_id)
                except Exception:
                    logger.exception("Failed to roll back conversation %d", self._convo_id)
                self._convo_id = 0
                self._sync_action_context()
            if self._dialog:
                self._dialog.set_thinking(False)
                self._dialog.clear_messages()
                for message in self._messages:
                    if message.role == "user":
                        self._dialog.add_user_message(
                            message.content,
                            images=message.images,
                            missing_images=message.missing_images,
                        )
                    else:
                        self._dialog.add_assistant_message(message.content)
                self._dialog.restore_draft(text, images)
                if retry_action_id:
                    self._action_mode = retry_action_mode
                    self._sync_action_context()
                    self._dialog.show_actions(text, retry_action_id)
            self.float_btn.set_responding(False)

    @_safe_slot
    def _on_thinking_event(self, event: StreamEvent) -> None:
        worker = self._worker
        if worker is not None and worker.context.tools:
            self._task_stream_event(worker, event)
            return
        if worker is None or not worker.accept_event(event):
            return
        if self._dialog:
            self._dialog.append_thinking_chunk(event.text)

    def _connect_tool_feedback(self, worker):
        if self._dialog:
            self._dialog.purge_expired_audit()
        from ai_desktop.services.run_audit import RunAudit
        audit = RunAudit(worker.context, self._last_request_user_id(worker), self,
                         admission=getattr(worker, '_task_admission', None))
        worker._audit_recorder = audit
        audit.failed.connect(self._on_audit_failed)
        worker.run_event.connect(audit.observe)
        worker.done.connect(audit.complete)
        worker.finished.connect(audit.deleteLater)
        worker.run_event.connect(lambda event, worker=worker: self._on_run_event(event, worker), Qt.QueuedConnection)
        worker.confirmation_requested.connect(
            lambda request, worker=worker: self._on_command_confirmation(request, worker), Qt.QueuedConnection)
        if worker.context.tools and self._dialog:
            self._dialog.begin_tool_run(worker.request.run_id)

    def _on_audit_failed(self, run_id):
        if self._worker is not None and self._worker.request.run_id == run_id:
            self._stop_worker()
            self._show_notice(QMessageBox.Warning, '运行记录保存失败', '任务已停止，请检查本地磁盘和数据库状态。')

    @_safe_slot
    def _on_run_event(self, event, source_worker):
        worker = self._worker
        if (worker is None or source_worker is not worker
                or worker.request.conversation_id != self._convo_id or not worker.accept_run_event(event)):
            return
        if self._dialog and event.kind in {RunEventKind.TOOL_STARTED, RunEventKind.TOOL_UPDATED,
                                          RunEventKind.TOOL_FINISHED}:
            workspace = worker.context.execution.workspace if worker.context.execution else ''
            self._dialog.show_tool_event(event, workspace)
        elif self._dialog and worker.context.tools:
            if event.kind == RunEventKind.MODEL_STARTED:
                self._response_text = ''
            elif event.kind == RunEventKind.MODEL_FINISHED:
                try:
                    self._response_text = json.loads(event.payload_json)['output']['content']
                except (ValueError, TypeError, KeyError):
                    pass
            self._dialog.show_task_model_event(event)

    @_safe_slot
    def _on_command_confirmation(self, request, source_worker):
        worker = self._worker
        if (worker is None or source_worker is not worker or worker._cancelled.is_set()
                or not worker.current_request.matches(request) or request.conversation_id != self._convo_id):
            return
        if self._dialog is None or not self._dialog.isVisible():
            self._stop_worker()
            return
        if not self._dialog.show_command_confirmation(request):
            worker.confirmations.respond(request, False)

    @_safe_slot
    def _on_command_decided(self, request, approved):
        worker = self._worker
        accepted = False
        if (worker is not None and not worker._cancelled.is_set() and worker.current_request.matches(request)
                and request.conversation_id == self._convo_id and worker.confirmations is not None
                and self._dialog is not None and self._dialog.isVisible()
                and self._dialog._tool_run_id == request.run_id):
            accepted = worker.confirmations.respond(request, approved)
        if self._dialog:
            self._dialog.acknowledge_command(request, accepted, approved)

    def _on_tool_stop_requested(self, run_id):
        if self._worker is not None and self._worker.request.run_id == run_id:
            self._stop_worker()

    @_safe_slot
    def _on_stream_event(self, event: StreamEvent) -> None:
        worker = self._worker
        if worker is not None and worker.context.tools:
            self._task_stream_event(worker, event)
            return
        if worker is None or not worker.accept_event(event):
            return
        self._response_text += event.text
        if self._dialog:
            self._dialog.append_stream_chunk(event.text)

    def _task_stream_event(self, worker, event):
        if (event.run_id != worker.request.run_id or event.conversation_id != self._convo_id
                or event.seq <= getattr(worker, '_last_task_stream_seq', 0)):
            return
        worker._last_task_stream_seq = event.seq
        if event.kind == EventKind.CONTENT:
            self._response_text += event.text
        if self._dialog:
            self._dialog.append_task_stream(event)

    def _request_config_snapshot(self, worker: RunWorker) -> dict:
        return {
            "run_id": worker.request.run_id,
            "step_id": worker.current_request.step_id,
            "initial_request_id": worker.request.request_id,
            "origin": worker.request.origin,
            "action_id": worker.request.action_id,
            "agent_id": worker.request.agent_id,
            "model": worker.request.model,
            "allowed_tools": [tool.name for tool in worker.context.tools],
            "admission": worker._task_admission.record() if getattr(worker, '_task_admission', None) else None,
            "think": worker.request.think,
            "think_setting": worker.request.think_setting.record(),
            "think_source": worker.request.think_source,
            "execution": worker.context.execution.record() if worker.context.execution else None,
            "search": worker.context.search_settings.record() if worker.context.search_settings else None,
            "keep_alive": worker.request.keep_alive,
            "options": dict(worker.request.options),
        }

    def _last_request_user_id(self, worker: RunWorker) -> int:
        return next(
            (
                message.id
                for message in reversed(worker.request.messages)
                if message.role == "user" and message.id
            ),
            0,
        )

    def _record_attempt_outcome(self, result: ChatResult) -> None:
        """Keep failed/cancelled retries visible without replacing the active answer."""
        worker = self._worker
        if worker is None or not worker.current_request.matches(result):
            return
        status = (
            "cancelled"
            if result.status == ResultStatus.CANCELLED
            else "failed"
        )
        user_message_id = self._regenerating_user_id or self._last_request_user_id(worker)
        if not user_message_id:
            return
        if any(
            version.request_id == worker.current_request.request_id
            for version in list_generations(user_message_id)
        ):
            return
        try:
            save_generation(
                user_message_id,
                worker.current_request.request_id,
                config_snapshot={**self._request_config_snapshot(worker), "run_status": result.status.value,
                                 "done_reason": result.turn.done_reason if result.turn else None},
                status=status,
                answer=result.text if result.status == ResultStatus.LIMITED else "",
                assistant_message_id=None,
                active=False,
            )
        except Exception:
            logger.exception("Failed to record %s generation", status)

    @_safe_slot
    def _on_stream_done(self, result: ChatResult) -> None:
        worker = self._worker
        if worker is None or not worker.current_request.matches(result):
            return
        if worker.request.conversation_id != self._convo_id:
            self._stop_worker()
            return
        text, ok = result.text, result.ok
        self.float_btn.set_responding(False)
        if result.status != ResultStatus.CANCELLED:
            self.float_btn.show_result(ok)
        if self._dialog:
            self._dialog.set_thinking(False)

        if ok and self._convo_id > 0 and text:
            try:
                regenerating = self._regenerating_user_id or 0
                if regenerating:
                    assistant_msg = save_message(worker.request.conversation_id, "assistant", text)
                    self._messages.append(assistant_msg)
                    save_generation(
                        regenerating,
                        worker.current_request.request_id,
                        config_snapshot=self._request_config_snapshot(worker),
                        status="succeeded",
                        answer=text,
                        assistant_message_id=assistant_msg.id,
                        active=True,
                    )
                else:
                    assistant_msg = save_message(worker.request.conversation_id, "assistant", text)
                    self._messages.append(assistant_msg)
                    user_message_id = self._last_request_user_id(worker)
                    if user_message_id:
                        save_generation(
                            user_message_id,
                            worker.current_request.request_id,
                            config_snapshot=self._request_config_snapshot(worker),
                            status="succeeded",
                            answer=text,
                            assistant_message_id=assistant_msg.id,
                            active=True,
                        )
            except Exception:
                logger.exception("Failed to save assistant message")
        elif not ok:
            self._record_attempt_outcome(result)

        if self._dialog:
            self._dialog.finalize_assistant_stream(
                text, ok, error=result.error, cancelled=result.status == ResultStatus.CANCELLED,
            )
            self._dialog.finish_tool_run('已停止' if result.status == ResultStatus.CANCELLED else '任务已结束')

        self._regenerating_user_id = 0
        self._refresh_regenerate_state()

        bubble_shown = False
        if result.status != ResultStatus.CANCELLED:
            bubble_shown = self._show_result_bubble(result)

        # 完整窗口仍可见或宠物气泡不可用时，沿用系统通知。
        if (
            ok
            and text
            and self._dialog
            and not self._dialog.isActiveWindow()
            and not bubble_shown
        ):
            preview = ResultBubble.summarize(text, "回复已完成", limit=80)
            self._tray.showMessage(
                f"{self._active_agent.icon} {self._active_agent.name}",
                preview,
                QSystemTrayIcon.Information,
                3000,
            )

        self._worker = None
        self._retire_worker(worker)
        self._finish_stop_if_ready()
        if self._dialog:
            self._dialog.purge_expired_audit()

    def _show_result_bubble(self, result: ChatResult) -> bool:
        if (
            (self._dialog is not None and self._dialog.isVisible())
            or not self.float_btn.isVisible()
            or not self.float_btn.pet_enabled
        ):
            return False

        if result.status == ResultStatus.LIMITED:
            kind = "action"
            title = "达到输出上限"
            source = result.error
            fallback = "部分回复已保留，可调整输出上限后重试。"
            timeout_ms = 9000
        elif result.ok:
            kind = "success"
            title = "任务完成"
            source = result.text
            fallback = "回复已完成，点击查看完整内容。"
            timeout_ms = 7000
        else:
            needs_action = result.error_code in {
                ErrorCode.CONNECTION,
                ErrorCode.HTTP,
                ErrorCode.SERVER,
                ErrorCode.TIMEOUT,
            }
            kind = "action" if needs_action else "error"
            title = "需要处理" if needs_action else "生成失败"
            source = result.error or result.text
            fallback = "请点击查看详情后重试。"
            timeout_ms = 9000

        summary = ResultBubble.summarize(source, fallback)
        self._speech_feedback_active = False
        self._result_bubble.show_result(
            kind,
            title,
            summary,
            self.float_btn.frameGeometry(),
            timeout_ms=timeout_ms,
        )
        pin_to_all_spaces(self._result_bubble)
        return True


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════


def _check_permissions():
    """检测辅助功能 + 输入监听权限"""
    from ai_desktop.utils.permissions import check_all
    return check_all()


def _request_permissions(ax: bool, im: bool) -> None:
    """触发 macOS 系统标准授权弹窗（已授权则静默返回）

    用 AXIsProcessTrustedWithOptions(prompt=True) 和 CGRequestListenEventAccess()
    替代自定义弹窗 —— 这是 macOS 推荐的标准 UX。
    """
    from ai_desktop.utils.permissions import request_accessibility, request_input_monitoring
    if not ax:
        logger.info("请求辅助功能权限（系统弹窗）")
        request_accessibility()
    if not im:
        logger.info("请求输入监听权限（系统弹窗）")
        request_input_monitoring()


def _open_accessibility_prefs() -> None:
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])


def _open_input_monitoring_prefs() -> None:
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"])


def main() -> None:
    if "--search-credential-check" in sys.argv[1:]:
        from ai_desktop.diagnostics.credential_check import main as credential_main
        args = sys.argv[1:].copy()
        args.remove('--search-credential-check')
        raise SystemExit(credential_main(args))
    if "--search-credential-read" in sys.argv[1:]:
        from ai_desktop.services.credential_reader import child_main
        raise SystemExit(child_main())
    if "--harness-acceptance" in sys.argv[1:]:
        from ai_desktop.diagnostics.harness_acceptance import main as acceptance_main
        args = sys.argv[1:].copy()
        args.remove("--harness-acceptance")
        raise SystemExit(acceptance_main(args))
    if "--version" in sys.argv[1:]:
        print(__version__)
        return
    if "--ocr-runtime" in sys.argv[1:]:
        from dataclasses import asdict

        from ai_desktop.services.ocr_service import probe_ocr_runtime

        print(json.dumps(asdict(probe_ocr_runtime()), ensure_ascii=False))
        return
    if "--speech-runtime" in sys.argv[1:]:
        from ai_desktop.services.speech_service import probe_speech_runtime

        print(json.dumps(probe_speech_runtime(), ensure_ascii=False))
        return
    if "--execution-runtime" in sys.argv[1:]:
        from ai_desktop.services.bash_executor import probe_execution_runtime
        print(json.dumps(probe_execution_runtime()))
        return
    if "--search-runtime" in sys.argv[1:]:
        from ai_desktop.services.search_credentials import probe_search_runtime

        print(json.dumps(probe_search_runtime(), ensure_ascii=False))
        return

    log_util.setup()

    # 崩溃处理钩子（必须在任何异常可能发生之前安装）
    from ai_desktop.utils import crash_handler
    crash_handler.install()

    app = QApplication(sys.argv)
    initialize()
    app.setApplicationName("AI 桌面助手")
    app.setQuitOnLastWindowClosed(False)

    _quit_flag = False

    def _on_sigint(*_) -> None:
        nonlocal _quit_flag
        _quit_flag = True
        print("\n正在退出...")

    signal.signal(signal.SIGINT, _on_sigint)

    controller = ChatController()
    app.paletteChanged.connect(controller.refresh_theme)
    controller.exit_ready.connect(app.quit)

    def _poll_quit() -> None:
        if _quit_flag:
            poll_timer.stop()
            controller.stop()

    poll_timer = QTimer()
    poll_timer.timeout.connect(_poll_quit)
    poll_timer.start(200)

    smoke_mode = os.environ.get("AIDE_SMOKE_TEST") == "1"

    # 权限检查（辅助功能 + 输入监听）
    # 已授权 → 静默跳过；缺失 → 触发 macOS 系统标准授权弹窗
    perm = _check_permissions() if not smoke_mode else PermissionStatus(True, True)
    logger.info("权限状态: AX=%s, InputMonitoring=%s", perm.accessibility, perm.input_monitoring)
    if not perm.all_granted:
        # Initial onboarding may request access once. Recovery only inspects it;
        # revoking permission does not trigger repeated system prompts.
        _request_permissions(perm.accessibility, perm.input_monitoring)

    controller.start()

    # ── 启动检查 ────────────────────────────────────────

    def _startup_check() -> None:
        controller.start_background_checks()

    startup_timer = QTimer()
    startup_timer.setSingleShot(True)
    startup_timer.timeout.connect(_startup_check)
    if smoke_mode:
        # Exercise real Fluent widgets in the frozen/native process, rather
        # than validating only the desktop sprite and import availability.
        controller._show_dialog()
        if controller._dialog is None:
            raise RuntimeError("Fluent chat window did not initialize")
        controller._dialog.set_auto_hide(False)
        controller._dialog.add_user_message("Fluent UI smoke")
        controller._dialog.add_assistant_message("**界面已就绪** · Fluent Light")
        from ai_desktop.ui.tool_card import probe_search_card, probe_tool_card
        probe_tool_card(controller._dialog)
        logger.info("Fluent command card initialized and invalidated without execution")
        probe_search_card(controller._dialog)
        logger.info("Fluent search card and verified citations initialized without network")
        from ai_desktop.ui.run_history_dialog import probe_run_audit
        probe_run_audit(controller._dialog)
        logger.info("Audit schema retention and read-only history verified without tools")
        from ai_desktop.ui.task_dialog import probe_task_entry
        probe_task_entry(controller._dialog)
        logger.info("Explicit task authorization and model-step cards verified without execution")
        _smoke_settings = SettingsDialog({}, parent=controller._dialog)
        _smoke_settings.show()
        controller.shutdown_started.connect(_smoke_settings.close)
        logger.info("Fluent chat and settings windows initialized")
        QTimer.singleShot(1500, controller.stop)
    else:
        startup_timer.start(1500)
    controller.shutdown_started.connect(poll_timer.stop)
    controller.shutdown_started.connect(startup_timer.stop)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
