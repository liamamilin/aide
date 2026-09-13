"""Exercise the real Qt worker/controller/UI pipeline against a temporary DB."""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QLabel

import ai_desktop.utils.storage as storage
from ai_desktop.llm.events import ChatResult, EventKind, ResultStatus, StreamEvent
from ai_desktop.llm.service_checks import ImageCapability
from ai_desktop.main import ChatController, StreamingChatWorker
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.utils.storage import get_conversation


@pytest.fixture
def controller(qtbot, tmp_db):
    with patch("ai_desktop.main.FloatButton"), patch("ai_desktop.main.MenuBarIcon"):
        with patch.object(ChatController, "_create_hotkey_backend", return_value=MagicMock()):
            ctl = ChatController()
        ctl._dialog = ChatDialog(ctl._all_agents, ctl._active_agent, [ctl._model], ctl._model)
        ctl._image_capability = ImageCapability.SUPPORTED
        ctl._dialog.set_image_capability(ImageCapability.SUPPORTED)
        ctl._dialog.message_sent.connect(ctl._on_user_message)
        qtbot.addWidget(ctl._dialog)
        yield ctl
        ctl._stop_worker()
        qtbot.waitUntil(lambda: not ctl._stale_workers, timeout=3000)


def send_and_wait(qtbot, controller, ollama_server, events, images=None):
    scenario = ollama_server.enqueue(*events)
    controller._on_user_message("question", images=images)
    assert controller._worker is not None
    qtbot.waitUntil(lambda: controller._worker is None and not controller._stale_workers, timeout=3000)
    return scenario


def assert_input_ready(controller):
    assert controller._dialog._input.isEnabled()
    assert controller._dialog._send_btn.text() == "发送"
    assert not controller._dialog._stream_timer.isActive()
    controller.float_btn.set_responding.assert_called_with(False)


def bubble_text(controller):
    return "\n".join(label.text() for label in controller._dialog._msg_container.findChildren(QLabel))


def test_normal_reply_with_cannot_prefix_is_saved(qtbot, controller, ollama_server):
    text = "无法确定原因，可以先检查日志。"
    send_and_wait(qtbot, controller, ollama_server, [{"message": {"content": text}, "done": True}])
    conversation = get_conversation(controller._convo_id)
    assert [(m.role, m.content) for m in conversation.messages] == [("user", "question"), ("assistant", text)]
    assert_input_ready(controller)
    assert text in bubble_text(controller)


@pytest.mark.parametrize("events,display", [
    ([{"error": "invalid image payload"}], "invalid image payload"),
    ([{"message": {"content": "partial"}}], "partial"),
    ([{"message": {"content": "partial"}}, {"error": "model crashed"}], "model crashed"),
])
def test_failure_is_visible_without_saving_an_assistant_message(qtbot, controller, ollama_server, events, display):
    send_and_wait(qtbot, controller, ollama_server, events)
    conversation = get_conversation(controller._convo_id)
    assert [m.role for m in conversation.messages] == ["user"]
    assert [m.role for m in controller._messages] == ["user"]
    assert_input_ready(controller)
    assert display in bubble_text(controller)
    assert "❌" in bubble_text(controller)
    controller._tray.showMessage.assert_not_called()


def test_missing_image_unblocks_input_without_http(qtbot, controller, ollama_server, tmp_path):
    send_and_wait(qtbot, controller, ollama_server, [], images=[str(tmp_path / "missing.png")])
    assert ollama_server.requests == []
    assert [m.role for m in get_conversation(controller._convo_id).messages] == ["user"]
    assert "图片读取失败" in bubble_text(controller)
    assert_input_ready(controller)


def test_unsupported_model_blocks_image_and_restores_draft(controller, tmp_path):
    image = tmp_path / "draft.png"
    image.write_bytes(b"draft")
    controller._image_capability = ImageCapability.UNSUPPORTED
    controller._dialog._pending_images = []
    with patch.object(controller, "_show_notice") as notice:
        controller._on_user_message("看图", [str(image)])
    assert controller._convo_id == 0
    assert controller._dialog._input.toPlainText() == "看图"
    assert controller._dialog.get_pending_images() == [str(image)]
    notice.assert_called_once()


def test_unknown_model_respects_declined_attempt(controller, tmp_path):
    image = tmp_path / "draft.png"
    image.write_bytes(b"draft")
    controller._image_capability = ImageCapability.UNKNOWN
    with patch.object(
        controller._dialog,
        "confirm_unknown_image_capability",
        return_value=False,
    ) as confirm:
        controller._on_user_message("看图", [str(image)])
    confirm.assert_called_once_with(controller._model)
    assert controller._convo_id == 0
    assert controller._dialog.get_pending_images() == [str(image)]


def test_unknown_model_can_send_after_explicit_attempt(
    qtbot, controller, ollama_server, tmp_path,
):
    image_path = tmp_path / "valid.png"
    image = QImage(2, 2, QImage.Format_ARGB32)
    image.fill(QColor("red"))
    assert image.save(str(image_path), "PNG")
    controller._image_capability = ImageCapability.UNKNOWN
    with patch.object(
        controller._dialog,
        "confirm_unknown_image_capability",
        return_value=True,
    ) as confirm:
        scenario = send_and_wait(
            qtbot,
            controller,
            ollama_server,
            [{"message": {"content": "看到了"}, "done": True}],
            images=[str(image_path)],
        )
    confirm.assert_called_once_with(controller._model)
    assert scenario.received.is_set()
    assert ollama_server.requests[0]["payload"]["messages"][-1]["images"]


@pytest.mark.parametrize("before_headers,partial", [(True, ""), (False, ""), (False, "半个回答")])
def test_stop_restores_ui_immediately_and_closes_socket(qtbot, controller, ollama_server, before_headers, partial):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []), before_headers=before_headers, hold_open=True,
    )
    controller._on_user_message("question")
    qtbot.waitUntil(scenario.received.is_set)
    if not before_headers:
        qtbot.waitUntil(scenario.sent.is_set)
    if partial:
        qtbot.waitUntil(lambda: controller._response_text == partial)
    started = time.perf_counter()
    controller._on_stop_requested()
    assert_input_ready(controller)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.2
    print(f"stop_ui_ms={elapsed * 1000:.2f}, before_headers={before_headers}, partial={bool(partial)}")
    assert controller._worker is None
    assert "已停止生成" in bubble_text(controller)
    assert partial in bubble_text(controller)
    assert "❌" not in bubble_text(controller)
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    qtbot.waitUntil(lambda: not controller._stale_workers)
    assert [m.role for m in get_conversation(controller._convo_id).messages] == ["user"]


def test_late_events_after_stop_cannot_change_replacement_request(qtbot, controller, ollama_server):
    old_scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message("first")
    old = controller._worker
    old_id = old.request.request_id
    qtbot.waitUntil(old_scenario.received.is_set)
    controller._on_stop_requested()
    ollama_server.enqueue({"message": {"content": "new answer"}, "done": True})
    controller._on_user_message("second")
    new = controller._worker
    # Emit queued old signals before processing finished / deferred deletion.
    old.thinking_chunk.emit("old thinking")
    old.content_event.emit(StreamEvent(old_id, EventKind.CONTENT, "old chunk"))
    old.done.emit(ChatResult(old_id, ResultStatus.SUCCEEDED, "old answer"))
    assert controller._worker is new
    assert not controller._dialog._input.isEnabled()
    assert "old" not in bubble_text(controller)
    qtbot.waitUntil(lambda: controller._worker is None and not controller._stale_workers)
    conversation = get_conversation(controller._convo_id)
    assert [(m.role, m.content) for m in conversation.messages] == [
        ("user", "first"), ("user", "second"), ("assistant", "new answer"),
    ]


def test_new_conversation_invalidates_old_stream(qtbot, controller, ollama_server):
    scenario = ollama_server.enqueue({"message": {"content": "old partial"}}, hold_open=True)
    controller._on_user_message("first")
    old_convo = controller._convo_id
    qtbot.waitUntil(lambda: controller._response_text == "old partial")
    controller._new_conversation()
    assert controller._convo_id == 0
    qtbot.waitUntil(lambda: "old partial" not in bubble_text(controller))
    assert_input_ready(controller)
    send_and_wait(qtbot, controller, ollama_server, [{"message": {"content": "new answer"}, "done": True}])
    qtbot.waitUntil(scenario.disconnected.is_set)
    assert controller._convo_id != old_convo
    assert [m.role for m in get_conversation(old_convo).messages] == ["user"]
    assert get_conversation(controller._convo_id).messages[-1].content == "new answer"


def test_stop_during_background_image_preparation_restores_ui_without_posting(
    qtbot, controller, ollama_server, monkeypatch, tmp_path,
):
    from ai_desktop.utils import images
    preparing, release = threading.Event(), threading.Event()
    encoding_threads = []

    def encode_image(_path):
        encoding_threads.append(threading.get_ident())
        preparing.set()
        assert release.wait(2)
        return "encoded"

    monkeypatch.setattr(images, "encode_image_base64", encode_image)
    controller._on_user_message("image", images=[str(tmp_path / "test.png")])
    try:
        qtbot.waitUntil(preparing.is_set)
        started = time.perf_counter()
        controller._on_stop_requested()
        assert_input_ready(controller)
        assert time.perf_counter() - started < 0.2
        assert encoding_threads != [threading.get_ident()]
        assert controller._stale_workers  # Retain the still-running QThread.
    finally:
        release.set()
    qtbot.waitUntil(lambda: not controller._stale_workers)
    assert ollama_server.requests == []


def test_repeated_requests_release_qthread_objects(qtbot, controller, ollama_server):
    for index in range(12):
        send_and_wait(qtbot, controller, ollama_server, [{"message": {"content": str(index)}, "done": True}])
    qtbot.waitUntil(lambda: not controller.findChildren(StreamingChatWorker))
    assert len(ollama_server.requests) == 12
    assert len(get_conversation(controller._convo_id).messages) == 24


def test_busy_send_restores_text_and_same_stored_attachment(qtbot, controller, ollama_server, tmp_path):
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message("first")
    qtbot.waitUntil(scenario.received.is_set)
    image = tmp_path / "draft.png"
    image.write_bytes(b"stored draft")
    controller._dialog._input.setPlainText("second draft")
    controller._dialog._pending_images = [str(image)]
    controller._dialog._on_send()
    assert controller._dialog._input.toPlainText() == "second draft"
    assert controller._dialog.get_pending_images() == [str(image)]
    assert [message.content for message in get_conversation(controller._convo_id).messages] == ["first"]


def test_send_setup_failure_restores_draft_and_rolls_back_empty_conversation(
    qtbot, controller, monkeypatch, tmp_path,
):
    image = tmp_path / "draft.png"
    image.write_bytes(b"stored draft")
    controller._dialog._input.setPlainText("retry me")
    controller._dialog._pending_images = [str(image)]

    def fail_save(*args, **kwargs):
        raise OSError("database unavailable")

    monkeypatch.setattr("ai_desktop.main.save_message", fail_save)
    controller._dialog._on_send()
    assert controller._dialog._input.toPlainText() == "retry me"
    assert controller._dialog.get_pending_images() == [str(image)]
    assert controller._convo_id == 0
    assert storage.list_conversations() == []
    assert_input_ready(controller)


def test_worker_setup_failure_rolls_back_message_in_existing_conversation(qtbot, controller, monkeypatch):
    conversation = storage.create_conversation("code_expert", "existing")
    storage.save_message(conversation.id, "user", "kept")
    controller._on_conversation_selected(conversation.id)
    controller._dialog._input.setPlainText("retry me")
    monkeypatch.setattr("ai_desktop.main.StreamingChatWorker", MagicMock(side_effect=RuntimeError("cannot start")))
    controller._dialog._on_send()
    assert [message.content for message in get_conversation(conversation.id).messages] == ["kept"]
    assert [message.content for message in controller._messages] == ["kept"]
    assert controller._dialog._input.toPlainText() == "retry me"
    assert "retry me" not in bubble_text(controller)
    assert_input_ready(controller)


def test_loading_history_invalidates_active_request(qtbot, controller, ollama_server):
    target = storage.create_conversation("translator", "target")
    storage.save_message(target.id, "user", "saved question")
    storage.save_message(target.id, "assistant", "saved answer")
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message("source question")
    source_id = controller._convo_id
    old_worker = controller._worker
    qtbot.waitUntil(scenario.received.is_set)

    controller._on_conversation_selected(target.id)
    old_worker.content_event.emit(StreamEvent(old_worker.request.request_id, EventKind.CONTENT, "late chunk"))
    old_worker.done.emit(ChatResult(old_worker.request.request_id, ResultStatus.SUCCEEDED, "late answer"))
    qtbot.wait(10)
    assert controller._convo_id == target.id
    assert "saved answer" in bubble_text(controller)
    assert "late" not in bubble_text(controller)
    assert [message.content for message in get_conversation(target.id).messages] == ["saved question", "saved answer"]
    assert [message.content for message in get_conversation(source_id).messages] == ["source question"]
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_deleting_current_conversation_invalidates_request_and_allows_new_send(qtbot, controller, ollama_server):
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message("deleted question")
    deleted_id = controller._convo_id
    old_worker = controller._worker
    qtbot.waitUntil(scenario.received.is_set)
    storage.delete_conversation(deleted_id)
    controller._on_conversation_deleted(deleted_id)
    old_worker.done.emit(ChatResult(old_worker.request.request_id, ResultStatus.SUCCEEDED, "late answer"))
    qtbot.wait(10)
    assert controller._convo_id == 0
    assert controller._messages == []
    assert get_conversation(deleted_id) is None
    assert "late" not in bubble_text(controller)
    ollama_server.enqueue({"message": {"content": "replacement"}, "done": True})
    controller._on_user_message("new question")
    qtbot.waitUntil(lambda: controller._worker is None and not controller._stale_workers)
    assert [message.content for message in get_conversation(controller._convo_id).messages] == [
        "new question", "replacement",
    ]
