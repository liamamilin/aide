"""F02 OCR preview and controller integration."""

import threading
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage, QTextCursor
from PyQt5.QtWidgets import QPushButton

from ai_desktop.config import Agent
from ai_desktop.main import ChatController
from ai_desktop.services.ocr_service import (
    AsyncOCRService,
    OCRBounds,
    OCRResult,
    OCRStatus,
    OCRTextBlock,
)
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.ocr_preview_dialog import OCRPreviewDialog


@pytest.fixture()
def chat_dialog(qtbot):
    agent = Agent("general_assistant", "通用助手", "🤖", "help")
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        dialog = ChatDialog([agent], agent, ["model"], "model")
    qtbot.addWidget(dialog)
    return dialog


@pytest.fixture()
def controller(qtbot, tmp_db):
    with patch("ai_desktop.main.FloatButton"), patch("ai_desktop.main.MenuBarIcon"), \
            patch.object(
                ChatController,
                "_create_hotkey_backend",
                side_effect=lambda: MagicMock(),
            ):
        value = ChatController()
    value._ocr = MagicMock()
    value._ocr.take_shutdown_workers.return_value = []
    value._dialog = MagicMock()
    yield value
    value.stop()
    qtbot.waitUntil(lambda: value._stopped, timeout=1000)


def _result(path, status, *, task_id="task-id", confidence=1.0, error=""):
    blocks = ()
    if status == OCRStatus.SUCCEEDED:
        blocks = (
            OCRTextBlock(
                "recognized text",
                confidence,
                OCRBounds(0.1, 0.7, 0.4, 0.1),
            ),
        )
    return OCRResult(
        task_id,
        path,
        status,
        blocks,
        ("en-US", "zh-Hans"),
        25.0,
        error,
    )


def test_preview_supports_edit_copy_and_text_only_choice(qtbot):
    preview = OCRPreviewDialog()
    qtbot.addWidget(preview)
    preview.begin("/managed/screenshot.png")
    assert preview.is_loading
    assert not preview._editor.isEnabled()

    preview.show_result(
        "raw text",
        block_count=2,
        elapsed_ms=18.4,
        languages=("en-US", "zh-Hans"),
        low_confidence=True,
    )
    assert preview._editor.isEnabled()
    assert "置信度较低" in preview._status.text()
    preview._editor.setPlainText("corrected text")

    clipboard = MagicMock()
    with patch(
        "ai_desktop.ui.ocr_preview_dialog.QApplication.clipboard",
        return_value=clipboard,
    ):
        qtbot.mouseClick(preview._copy_button, Qt.LeftButton)
    clipboard.setText.assert_called_once_with("corrected text")

    with qtbot.waitSignal(preview.text_accepted, timeout=1000) as signal:
        qtbot.mouseClick(preview._text_only_button, Qt.LeftButton)
    assert signal.args == ["corrected text", "/managed/screenshot.png", False]


def test_preview_can_keep_original_image(qtbot):
    preview = OCRPreviewDialog()
    qtbot.addWidget(preview)
    preview.begin("/managed/image.png")
    preview.show_result(
        "recognized",
        block_count=1,
        elapsed_ms=10.0,
        languages=("en-US",),
        low_confidence=False,
    )
    with qtbot.waitSignal(preview.text_accepted, timeout=1000) as signal:
        qtbot.mouseClick(preview._keep_image_button, Qt.LeftButton)
    assert signal.args == ["recognized", "/managed/image.png", True]


@pytest.mark.parametrize(("method", "needle"), [
    (lambda preview: preview.show_empty(12.0), "未识别到文字"),
    (lambda preview: preview.show_error("图片损坏"), "图片损坏"),
])
def test_preview_distinguishes_empty_and_error(qtbot, method, needle):
    preview = OCRPreviewDialog()
    qtbot.addWidget(preview)
    preview.begin("/managed/image.png")
    method(preview)
    assert needle in preview._status.text()
    assert not preview._copy_button.isEnabled()
    assert not preview._text_only_button.isEnabled()
    assert not preview._keep_image_button.isEnabled()


def test_closing_loading_preview_requests_cancel(qtbot):
    preview = OCRPreviewDialog()
    qtbot.addWidget(preview)
    preview.begin("/managed/image.png")
    with qtbot.waitSignal(preview.cancel_requested, timeout=1000):
        preview.close()


def test_each_pending_image_has_local_ocr_action(qtbot, chat_dialog, tmp_path):
    path = tmp_path / "sample.png"
    image = QImage(80, 60, QImage.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(path))
    chat_dialog._pending_images = [str(path)]
    chat_dialog._refresh_image_preview()
    button = chat_dialog.findChild(QPushButton, "ocr_image_btn")
    assert button is not None

    with qtbot.waitSignal(chat_dialog.ocr_requested, timeout=1000) as signal:
        qtbot.mouseClick(button, Qt.LeftButton)
    assert signal.args == [str(path)]


def test_accepted_ocr_text_is_inserted_at_cursor(chat_dialog):
    chat_dialog._input.setPlainText("question")
    cursor = chat_dialog._input.textCursor()
    cursor.movePosition(QTextCursor.End)
    chat_dialog._input.setTextCursor(cursor)
    chat_dialog._insert_ocr_text("recognized")
    assert chat_dialog._input.toPlainText() == "question\nrecognized"


def test_text_only_choice_uses_existing_text_request_pipeline(qtbot, chat_dialog):
    path = "/managed/image.png"
    chat_dialog._pending_images = [path]
    with patch(
        "ai_desktop.ui.chat_dialog.image_utils.discard_staged_image"
    ) as discard:
        chat_dialog._use_ocr_text("recognized", path, False)
    assert chat_dialog._input.toPlainText() == "recognized"
    assert chat_dialog.get_pending_images() == []
    discard.assert_called_once_with(path)
    with qtbot.waitSignal(chat_dialog.message_sent, timeout=1000) as signal:
        chat_dialog._on_send()
    assert signal.args == ["recognized", []]


def test_text_plus_image_choice_uses_existing_attachment_pipeline(qtbot, chat_dialog):
    path = "/managed/image.png"
    chat_dialog._pending_images = [path]
    with patch(
        "ai_desktop.ui.chat_dialog.image_utils.discard_staged_image"
    ) as discard:
        chat_dialog._use_ocr_text("recognized", path, True)
    assert chat_dialog._input.toPlainText() == "recognized"
    assert chat_dialog.get_pending_images() == [path]
    discard.assert_not_called()
    with qtbot.waitSignal(chat_dialog.message_sent, timeout=1000) as signal:
        chat_dialog._on_send()
    assert signal.args == ["recognized", [path]]


def test_controller_starts_ocr_only_for_pending_image(controller):
    path = "/managed/image.png"
    controller._dialog.get_pending_images.return_value = [path]
    controller._on_ocr_requested(path)
    controller._dialog.show_ocr_loading.assert_called_once_with(path)
    controller._ocr.start.assert_called_once_with(path)
    assert controller._ocr_image_path == path

    controller._ocr.reset_mock()
    controller._on_ocr_requested("/managed/missing.png")
    controller._ocr.start.assert_not_called()


@pytest.mark.parametrize("status", [OCRStatus.SUCCEEDED, OCRStatus.EMPTY, OCRStatus.FAILED])
def test_controller_routes_current_ocr_result(controller, status):
    path = "/managed/image.png"
    controller._ocr_image_path = path
    controller._dialog.get_pending_images.return_value = [path]
    result = _result(path, status, confidence=0.3, error="OCR failed")
    controller._on_ocr_completed(result)

    assert controller._ocr_image_path is None
    if status == OCRStatus.SUCCEEDED:
        controller._dialog.show_ocr_result.assert_called_once_with(
            path,
            "recognized text",
            block_count=1,
            elapsed_ms=25.0,
            languages=("en-US", "zh-Hans"),
            low_confidence=True,
        )
    elif status == OCRStatus.EMPTY:
        controller._dialog.show_ocr_empty.assert_called_once_with(path, 25.0)
    else:
        controller._dialog.show_ocr_error.assert_called_once_with(path, "OCR failed")


def test_removing_image_or_hiding_window_cancels_ocr(controller):
    path = "/managed/image.png"
    controller._ocr_image_path = path
    controller._on_pending_images_changed([])
    controller._ocr.cancel.assert_called_once_with()
    controller._dialog.close_ocr_preview.assert_called_once_with(path)
    assert controller._ocr_image_path is None

    controller._ocr.reset_mock()
    controller._ocr_image_path = path
    controller._on_dialog_closed()
    controller._ocr.cancel.assert_called_once_with()
    assert controller._ocr_image_path is None


def test_controller_shutdown_waits_for_active_ocr(qtbot, controller):
    started = threading.Event()
    release = threading.Event()

    class Backend:
        def recognize(self, request, _is_cancelled):
            started.set()
            release.wait(2)
            return _result(
                request.image_path,
                OCRStatus.SUCCEEDED,
                task_id=request.task_id,
            )

    controller._ocr = AsyncOCRService(controller, service=Backend())
    controller._ocr.completed.connect(controller._on_ocr_completed)
    controller._ocr.start("/managed/image.png")
    qtbot.waitUntil(started.is_set, timeout=1000)

    controller.stop()
    assert not controller._stopped
    release.set()
    qtbot.waitUntil(lambda: controller._stopped, timeout=1000)
