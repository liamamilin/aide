"""Controller shutdown waits for every task without blocking the UI thread."""
import threading
from unittest.mock import MagicMock, call, patch

import pytest
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ai_desktop.capture.screenshot import ScreenshotResult, ScreenshotStatus
from ai_desktop.main import ChatController
from ai_desktop.ui.chat_dialog import ChatDialog


@pytest.fixture
def controller(qtbot, tmp_db):
    with patch("ai_desktop.main.FloatButton"), patch("ai_desktop.main.MenuBarIcon"):
        with patch.object(ChatController, "_create_hotkey_backend", side_effect=lambda: MagicMock()):
            ctl = ChatController()
        ctl._dialog = ChatDialog(ctl._all_agents, ctl._active_agent, [ctl._model], ctl._model)
        yield ctl
        ctl.stop()
        qtbot.waitUntil(lambda: ctl._stopped, timeout=3000)


def test_stop_without_tasks_finishes_once(qtbot, controller):
    ready = []
    controller.exit_ready.connect(lambda: ready.append(True))
    controller.stop()
    controller.stop()
    assert controller._stopped
    assert ready == [True]
    assert controller._dialog is None
    controller.hotkey.stop.assert_called_once()
    controller.hotkey_img.stop.assert_called_once()


def test_theme_refresh_uses_one_controller_entry(controller):
    controller._tray.refresh_theme.reset_mock()
    with patch("ai_desktop.main.styles.refresh_all", return_value=5) as refresh_all, \
            patch.object(controller._dialog, "refresh_theme") as refresh_dialog:
        controller.refresh_theme()

    refresh_all.assert_called_once_with()
    controller._tray.refresh_theme.assert_called_once_with()
    refresh_dialog.assert_called_once_with()


def test_hidden_float_entry_can_be_restored_from_tray(controller):
    """隐藏宠物后，菜单栏动作应能重新显示悬浮入口。"""
    button = controller.float_btn
    button.isVisible.return_value = True
    controller._hide_float_entry()
    button.hide.assert_called_once_with()
    controller._tray.set_float_entry_visible.assert_called_with(False, button.pet_enabled)

    button.isVisible.return_value = False
    controller._on_float_entry_toggle()
    button.show.assert_called_once_with()
    controller._tray.set_float_entry_visible.assert_called_with(True, button.pet_enabled)


def test_screen_follow_preference_is_saved(controller):
    with patch("ai_desktop.main.save_setting") as save:
        controller._on_screen_follow_changed(False)
        controller._on_screen_follow_changed(True)
    assert save.call_args_list[-2:] == [
        call("float_follow_cursor_screen", "false"),
        call("float_follow_cursor_screen", "true"),
    ]


def test_empty_read_selection_uses_nonblocking_pet_feedback(controller):
    task = MagicMock()
    controller._selection_capture = task
    controller._pending_read_selection = True
    with patch.object(controller, "_show_speech_feedback", return_value=True) as feedback, \
            patch.object(controller, "_show_notice") as notice:
        controller._on_selection_captured(task, "")

    assert controller._selection_capture is None
    assert not controller._pending_read_selection
    controller.float_btn.show_result.assert_called_with(False)
    feedback.assert_called_once_with(
        "error", "朗读选区", "没有读取到选中文字，请重新框选后再试。",
        timeout_ms=7000,
    )
    notice.assert_not_called()
    controller._tray.showMessage.assert_not_called()


def test_empty_read_selection_falls_back_to_tray_when_pet_hidden(controller):
    task = MagicMock()
    controller._selection_capture = task
    controller._pending_read_selection = True
    with patch.object(controller, "_show_speech_feedback", return_value=False), \
            patch.object(controller, "_show_notice") as notice:
        controller._on_selection_captured(task, "")

    controller._tray.showMessage.assert_called_once()
    notice.assert_not_called()


def test_exit_waits_for_chat_abort_and_rejects_new_work(qtbot, controller, ollama_server):
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message("question")
    qtbot.waitUntil(scenario.received.is_set)
    ready = []
    controller.exit_ready.connect(lambda: ready.append(True))
    controller._on_exit()
    controller._on_user_message("ignored")
    controller._on_screenshot_hotkey()
    assert ready == [] or scenario.disconnected.is_set()
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    qtbot.waitUntil(lambda: controller._stopped, timeout=1000)
    assert ready == [True]
    assert controller._worker is None
    assert controller._stale_workers == []
    assert controller._screenshot_worker is None
    assert len(ollama_server.requests) == 1


def test_exit_terminates_owned_screenshot_process(qtbot, controller, monkeypatch):
    started = threading.Event()
    terminated = threading.Event()
    process = MagicMock()
    process.poll.side_effect = lambda: -15 if terminated.is_set() else None

    def terminate():
        terminated.set()

    process.terminate.side_effect = terminate
    process.communicate.return_value = ("", "")

    def popen(*args, **kwargs):
        started.set()
        return process

    monkeypatch.setattr("ai_desktop.capture.screenshot.subprocess.Popen", popen)
    controller._on_screenshot_hotkey()
    qtbot.waitUntil(started.is_set)
    ready = []
    controller.exit_ready.connect(lambda: ready.append(True))
    controller.stop()
    qtbot.waitUntil(terminated.is_set, timeout=1000)
    qtbot.waitUntil(lambda: controller._stopped, timeout=1000)
    assert ready == [True]
    assert controller._screenshot_worker is None
    process.terminate.assert_called_once()


def test_stop_aborts_status_probe_without_waiting(qtbot, controller, ollama_server):
    scenario = ollama_server.enqueue(before_headers=True)
    controller._refresh_model_list()
    qtbot.waitUntil(scenario.received.is_set)
    ready = []
    controller.exit_ready.connect(lambda: ready.append(True))
    controller.stop()
    assert controller._stopped
    assert ready == [True]
    assert controller._service_checks._service_active is None
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    assert controller._shutdown_workers == []


def test_exit_waits_for_plain_text_clipboard_restore(qtbot, controller):
    class DelayedCapture(QObject):
        completed = pyqtSignal(str)

        def cancel(self):
            QTimer.singleShot(30, lambda: self.completed.emit(""))

    capture = DelayedCapture(controller)
    controller._selection_capture = capture
    capture.completed.connect(
        lambda text: controller._on_selection_captured(capture, text),
    )
    ready = []
    controller.exit_ready.connect(lambda: ready.append(True))
    controller.stop()
    assert not controller._stopped
    assert ready == []
    qtbot.waitUntil(lambda: controller._stopped, timeout=1000)
    assert ready == [True]


def test_global_screenshot_hotkey_returns_to_gui_thread(qtbot, controller, monkeypatch):
    gui_thread = threading.get_ident()
    constructed_on = []
    worker = MagicMock()

    def worker_factory(_parent):
        constructed_on.append(threading.get_ident())
        return worker

    monkeypatch.setattr("ai_desktop.main.ScreenshotWorker", worker_factory)
    background = threading.Thread(target=controller._on_global_screenshot_hotkey)
    background.start()
    background.join(timeout=1)
    qtbot.waitUntil(lambda: worker.start.called, timeout=1000)
    assert constructed_on == [gui_thread]
    assert worker.completed.connect.call_count == 1
    controller._screenshot_worker = None


def test_global_text_hotkey_stages_capture_on_gui_thread(qtbot, controller, monkeypatch):
    gui_thread = threading.get_ident()
    constructed_on = []

    class FakeCapture(QObject):
        completed = pyqtSignal(str)

        def __init__(self, parent):
            super().__init__(parent)
            constructed_on.append(threading.get_ident())

        def start(self):
            QTimer.singleShot(0, lambda: self.completed.emit("selected text"))

        def cancel(self):
            self.completed.emit("")

    monkeypatch.setattr("ai_desktop.main.SelectionCaptureTask", FakeCapture)
    real_dialog = controller._dialog
    dialog = MagicMock()
    dialog.isVisible.return_value = True
    controller._dialog = dialog
    try:
        background = threading.Thread(target=controller._on_global_hotkey)
        background.start()
        background.join(timeout=1)
        qtbot.waitUntil(lambda: dialog.set_input_text.called, timeout=1000)
    finally:
        controller._dialog = real_dialog
    assert constructed_on == [gui_thread]
    dialog.set_input_text.assert_called_once_with("selected text")
    assert controller._selection_capture is None


def test_repeated_screenshot_trigger_keeps_one_worker(controller, monkeypatch):
    worker = MagicMock()
    worker.isFinished.return_value = False  # still running → second call ignored
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr("ai_desktop.main.ScreenshotWorker", factory)
    controller._on_screenshot_hotkey()
    controller._on_screenshot_hotkey()
    factory.assert_called_once_with(controller)
    worker.start.assert_called_once()
    worker.deleteLater.assert_not_called()
    controller._screenshot_worker = None


def test_stale_screenshot_worker_is_cleaned_up(controller, monkeypatch):
    """A finished-but-uncleaned worker should be reset so the next hotkey works."""
    stale = MagicMock()
    stale.isFinished.return_value = True
    controller._screenshot_worker = stale
    fresh = MagicMock()
    fresh.isFinished.return_value = False
    factory = MagicMock(return_value=fresh)
    monkeypatch.setattr("ai_desktop.main.ScreenshotWorker", factory)
    controller._on_screenshot_hotkey()
    stale.deleteLater.assert_called_once()
    factory.assert_called_once_with(controller)
    fresh.start.assert_called_once()
    controller._screenshot_worker = None


def test_screenshot_cancel_is_silent(controller):
    with patch.object(controller, "_show_notice") as notice, \
            patch("ai_desktop.main.QMessageBox") as message_box:
        controller._on_screenshot_result(ScreenshotResult(ScreenshotStatus.CANCELLED))
    notice.assert_not_called()
    message_box.assert_not_called()


def test_successful_screenshot_is_attached_then_raw_temp_is_deleted(controller, tmp_path):
    raw = tmp_path / "capture.png"
    raw.write_bytes(b"temporary capture")
    real_dialog = controller._dialog
    dialog = MagicMock()
    dialog.isVisible.return_value = True
    controller._dialog = dialog
    try:
        controller._on_screenshot_result(
            ScreenshotResult(ScreenshotStatus.SUCCEEDED, path=str(raw)),
        )
    finally:
        controller._dialog = real_dialog
    dialog.attach_image_paths.assert_called_once_with([str(raw)])
    assert not raw.exists()


@pytest.mark.parametrize(("status", "title"), [
    (ScreenshotStatus.TIMED_OUT, "截图超时"),
    (ScreenshotStatus.COMMAND_FAILED, "截图失败"),
])
def test_non_permission_screenshot_errors_have_no_settings_action(controller, status, title):
    with patch.object(controller, "_show_notice") as notice, \
            patch.object(controller, "_open_screen_recording_prefs") as open_prefs, \
            patch("ai_desktop.main.QMessageBox") as message_box:
        controller._on_screenshot_result(ScreenshotResult(status, error="<failure>"))
    assert notice.call_args.args[1] == title
    if status == ScreenshotStatus.COMMAND_FAILED:
        assert "&lt;failure&gt;" in notice.call_args.args[2]
    open_prefs.assert_not_called()
    message_box.assert_not_called()


def test_permission_screenshot_error_offers_settings_action(controller):
    with patch("ai_desktop.main.QMessageBox") as message_box, \
            patch.object(controller, "_show_notice") as notice, \
            patch.object(controller, "_open_screen_recording_prefs") as open_prefs:
        box = message_box.return_value
        settings_button = box.addButton.return_value
        box.clickedButton.return_value = settings_button
        controller._on_screenshot_result(
            ScreenshotResult(ScreenshotStatus.PERMISSION_DENIED, error="not permitted"),
        )
    notice.assert_not_called()
    box.exec_.assert_called_once()
    open_prefs.assert_called_once()
